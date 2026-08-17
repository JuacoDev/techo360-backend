from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.session import get_db
from models.zona import Zona
from schemas.zona import ZonaResponse


router = APIRouter(
    prefix="/api/zonas",
    tags=["zonas"]
)


@router.get(
    "",
    response_model=List[ZonaResponse],
    summary="Obtener zonas",
    description="Obtiene las zonas disponibles con sus coordenadas geográficas."
)
def obtener_zonas(db: Session = Depends(get_db)):
    zonas = db.query(Zona).all()

    return zonas