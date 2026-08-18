from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.session import get_db
from models.zona import Usuario
from schemas.zona import UsuarioResponse


router = APIRouter(
    prefix="/api/usuarios",
    tags=["usuarios"]
)


@router.get(
    "",
    response_model=List[UsuarioResponse],
    summary="Obtener usuarios",
    description="Obtiene los usuarios disponibles."
)
def obtener_usuarios(db: Session = Depends(get_db)):
    usuarios = db.query(Zona).all()

    return usuarios
