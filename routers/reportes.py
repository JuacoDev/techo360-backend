from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from models.reporte import Reporte
from routers.auth import get_current_user
from schemas.reporte import ReporteResponse


router = APIRouter(
    prefix="/api/reportes",
    tags=["reportes"],
)


@router.get(
    "",
    response_model=list[ReporteResponse],
    summary="Listar reportes del usuario autenticado",
)
def listar_reportes(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    offset = (page - 1) * page_size

    return (
        db.query(Reporte)
        .filter(Reporte.usuario_id == current_user.id)
        .order_by(Reporte.fecha_creacion.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )


@router.get(
    "/{reporte_id}",
    response_model=ReporteResponse,
    summary="Obtener detalle de un reporte",
)
def obtener_reporte(
    reporte_id: int,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    reporte = (
        db.query(Reporte)
        .filter(
            Reporte.id == reporte_id,
            Reporte.usuario_id == current_user.id,
        )
        .first()
    )

    if reporte is None:
        raise HTTPException(
            status_code=404,
            detail="Reporte no encontrado.",
        )

    return reporte