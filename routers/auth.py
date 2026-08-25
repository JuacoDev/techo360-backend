from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from db.session import get_db
from schemas.auth import LoginRequest, LoginResponse
from services.auth_service import (
    autenticar_usuario,
    crear_access_token,
    pwd_context
)
from models.mapcalor_user import MapCalorUser

router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)

# LOGIN
@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Iniciar sesión",
    description="Valida las credenciales del usuario y devuelve un token JWT."
)
def login(datos: LoginRequest, db: Session = Depends(get_db)):
    usuario = autenticar_usuario(db, datos.correo, datos.password)

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = crear_access_token(usuario.id, usuario.correo)

    return {
        "access_token": token,
        "token_type": "bearer"
    }


# REGISTRO
@router.post(
    "/register",
    summary="Registrar usuario",
    description="Crea un nuevo usuario en la base de datos con contraseña encriptada."
)
def register(correo: str, password: str, name: str, db: Session = Depends(get_db)):
    # Verificar si ya existe
    usuario = db.query(MapCalorUser).filter(MapCalorUser.correo == correo).first()
    if usuario:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Usuario ya existe"
        )

    # Hashear la contraseña antes de guardar
    password_hash = pwd_context.hash(password)

    nuevo_usuario = MapCalorUser(
        correo=correo,
        passwordVar=password_hash,
        nameVar=name
    )
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)

    return {"message": "Usuario registrado", "id": nuevo_usuario.id}
