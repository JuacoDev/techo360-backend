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
    CurrentUserResponse
)

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from services.auth_service import autenticar_usuario
from core.config import settings


router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)


    
# -------------------
# Configuración de OAuth2
# -------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

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
        "exp": ahora + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

# -------------------
# Login
# -------------------
@router.post(
    "/login",
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
        422: {
            "description": "Datos de entrada inválidos."
        }
    }
)
def register(datos: RegisterRequest, db: Session = Depends(get_db)):

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

    # Crear usuario
    nuevo_usuario = MapCalorUser(
        correo=datos.correo,
        namevar=datos.namevar,
        passwordvar=datos.password
    )

    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)

    return {
        "message": "Usuario registrado correctamente",
        "id": nuevo_usuario.id
    }

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
