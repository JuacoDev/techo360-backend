from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from db.session import get_db
from schemas.auth import LoginRequest, LoginResponse
from services.auth_service import (
    autenticar_usuario,
    crear_access_token
)


router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Iniciar sesión",
    description="Valida las credenciales del usuario y devuelve un token JWT."
)
def login(
    datos: LoginRequest,
    db: Session = Depends(get_db)
):
    usuario = autenticar_usuario(
        db,
        datos.correo,
        datos.password
    )

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = crear_access_token(
        usuario.id,
        usuario.correo
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }