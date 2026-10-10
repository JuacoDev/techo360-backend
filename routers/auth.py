from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import jwt
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from schemas.auth import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    CurrentUserResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
)

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from services.auth_service import autenticar_usuario, hashear_password
from services.otp_service import (
    OTPInvalidError,
    OTPNotVerifiedError,
    OTPTooManyAttemptsError,
    OTPVerificationUsedError,
    otp_service,
)
from core.config import ACCESS_TOKEN_EXPIRE_HOURS, settings
from fastapi import Request
from services.rate_limiter import obtener_ip_cliente, verificar_limite

router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)
# -------------------
# Configuración de OAuth2
# -------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def limitar_login(request: Request):
    await verificar_limite(
        request=request,
        ambito="auth_login",
        identificador=obtener_ip_cliente(request),
        limite=5,
        ventana_segundos=900,
    )

# -------------------
# Crear token JWT
# -------------------
def crear_access_token(user_id: int, correo: str, name: str) -> str:
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "correo": correo,
        "name": name,
        "iat": ahora,
        "exp": ahora + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

# -------------------
# Login
# -------------------
@router.post(
    "/login",
    dependencies=[Depends(limitar_login)],
    summary="Iniciar sesión",
    description="Autentica un usuario mediante su identificador y contraseña y devuelve un token JWT.",
    response_model=LoginResponse,
    responses={
        401: {
            "description": "Credenciales incorrectas o usuario no autorizado."
        },
        422: {
            "description": "Datos de entrada inválidos."
        }
    }
)
def login(datos: LoginRequest, db: Session = Depends(get_db)):
    usuario = autenticar_usuario(db, datos.identifier, datos.password)

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = crear_access_token(usuario.id, usuario.correo, usuario.namevar)

    return {
        "access_token": token,
        "token_type": "bearer"
    }


    # -------------------
# Registro de usuario
# -------------------
@router.post(
    "/register",
    summary="Registrar usuario",
    description="Registra un nuevo usuario en la base de datos.",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "description": "El correo o nombre de usuario ya está registrado."
        },
        403: {
            "description": (
                "El correo no tiene una verificacion OTP vigente (nunca se verifico, expiro "
                "o ya se uso para otro registro)."
            )
        },
        422: {
            "description": "Datos de entrada inválidos."
        }
    }
)
async def register(datos: RegisterRequest, db: Session = Depends(get_db)):

    # SEGURIDAD: el registro exige que /otp/verify haya dejado la marca de este correo.
    # Se comprueba ANTES de consultar la base, asi un llamador sin verificar tampoco
    # puede averiguar que correos o nombres ya existen.
    try:
        await otp_service.comprobar_verificado(datos.correo)
    except (OTPNotVerifiedError, OTPVerificationUsedError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    # Verificar si ya existe el correo
    usuario_correo = db.query(MapCalorUser).filter(
        MapCalorUser.correo == datos.correo
    ).first()

    if usuario_correo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El correo ya está registrado"
        )

    # Verificar si ya existe el nombre
    usuario_nombre = db.query(MapCalorUser).filter(
        MapCalorUser.namevar == datos.namevar
    ).first()

    if usuario_nombre:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre de usuario ya está registrado"
        )

    # La marca se consume solo cuando el registro va a hacerse (un correo o nombre ya
    # tomado, arriba, no la gasta: se puede corregir y reintentar). Consumo atomico:
    # de dos peticiones simultaneas con la misma marca, solo una registra.
    try:
        await otp_service.consumir_verificado(datos.correo)
    except (OTPNotVerifiedError, OTPVerificationUsedError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    # Crear usuario
    nuevo_usuario = MapCalorUser(
        correo=datos.correo,
        namevar=datos.namevar,
        passwordvar=hashear_password(datos.password)
    )

    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)

    token = crear_access_token(nuevo_usuario.id, nuevo_usuario.correo, nuevo_usuario.namevar)

    return {
        "message": "Usuario registrado correctamente",
        "id": nuevo_usuario.id,
        "access_token": token,
        "token_type": "bearer"
    }


# -------------------
# Reset de contraseña (recuperacion con OTP)
# -------------------
# AVISO -- min_length=8 vive en dos sitios (aqui, via
# ResetPasswordRequest, y en RegisterRequest) porque son los dos unicos
# lugares donde se fija una contraseña nueva; login no la revalida
# (compara contra el hash existente, no le importa la longitud).
#
# otp_service.verify_otp() es generico por correo (no distingue
# "proposito registro" vs "proposito recuperacion", ver AVISO en
# services/otp_service.py) -- se reutiliza tal cual, mismo patron que
# ya usa el frontend antes de llamar a /auth/register.
@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    summary="Cambiar contraseña con codigo OTP",
    description=(
        "Verifica el codigo OTP enviado al correo y, si es valido, "
        "actualiza la contraseña (hasheada con bcrypt). El codigo se "
        "invalida al usarse (un solo uso, ver otp_service.verify_otp)."
    ),
    responses={
        400: {"description": "Codigo invalido/expirado, o el correo no esta registrado."},
        422: {"description": "Datos de entrada invalidos (ej. contraseña muy corta)."},
        429: {"description": "Se agotaron los intentos de verificacion del codigo."},
    },
)
async def reset_password(datos: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        await otp_service.verify_otp(datos.email, datos.code)
    except OTPInvalidError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except OTPTooManyAttemptsError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    usuario = db.query(MapCalorUser).filter(MapCalorUser.correo == datos.email).first()
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El correo no está registrado")

    usuario.passwordvar = hashear_password(datos.new_password)
    db.commit()

    return {"status": "ok"}


# -------------------
# Dependencia para validar token
# -------------------
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        user_id: str = payload.get("sub")

        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido",
                headers={"WWW-Authenticate": "Bearer"}
            )

        usuario = db.query(MapCalorUser).filter(MapCalorUser.id == int(user_id)).first()

        if usuario is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuario no encontrado",
                headers={"WWW-Authenticate": "Bearer"}
            )

        return usuario

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"}
        )

# -------------------
# Endpoint protegido
# -------------------
@router.get(
    "/me",
    summary="Usuario autenticado",
    description="Obtiene la información del usuario autenticado mediante el token JWT.",
    response_model=CurrentUserResponse,
    responses={
        401: {
            "description": "Token inválido, expirado o usuario no encontrado."
        }
    }
)
def read_users_me(current_user: MapCalorUser = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "correo": current_user.correo,
        "namevar": current_user.namevar
    }
