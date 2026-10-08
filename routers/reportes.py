import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from models.reporte import Reporte
from routers.auth import get_current_user
from schemas.reporte import ReporteGenerarRequest, ReporteResponse
from services.reportes_consumer import publicar_reporte
from services.reportes_service import obtener_ruta_archivo

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/reportes",
    tags=["reportes"],
)


def _respuesta_reporte(reporte: Reporte) -> ReporteResponse:
    return ReporteResponse.model_validate(
        {
            "id": reporte.id,
            "usuario_id": reporte.usuario_id,
            "titulo": reporte.titulo,
            "contenido": reporte.contenido,
            "fecha_creacion": reporte.fecha_creacion,
            "estado": reporte.estado,
            "formato": reporte.formato,
            "error": reporte.error,
            "descarga_url": (
                f"/api/reportes/{reporte.id}/descargar"
                if reporte.estado == "listo" and reporte.archivo_nombre
                else None
            ),
        }
    )


@router.post(
    "/generar",
    response_model=ReporteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solicitar la generación asíncrona de un reporte",
)
async def generar_reporte(
    solicitud: ReporteGenerarRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    reporte = Reporte(
        usuario_id=current_user.id,
        titulo=solicitud.opciones.titulo,
        contenido="Reporte pendiente de generación.",
        estado="pendiente",
        formato=solicitud.formato,
        solicitud=solicitud.model_dump(mode="json"),
    )
    db.add(reporte)
    db.commit()
    db.refresh(reporte)

    canal = getattr(request.app.state, "canal_reportes", None)
    if canal is None:
        reporte.estado = "error"
        reporte.error = "No se pudo enviar el reporte a procesamiento."
        reporte.contenido = "Falló el envío del reporte a la cola."
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de generación de reportes no está disponible.",
        )

    try:
        await publicar_reporte(canal, reporte.id)
    except Exception as error:
        logger.exception("No se pudo encolar el reporte %s.", reporte.id)
        reporte.estado = "error"
        reporte.error = "No se pudo enviar el reporte a procesamiento."
        reporte.contenido = "Falló el envío del reporte a la cola."
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo encolar el reporte. Intenta nuevamente.",
        ) from error

    return _respuesta_reporte(reporte)


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

    reportes = (
        db.query(Reporte)
        .filter(Reporte.usuario_id == current_user.id)
        .order_by(Reporte.fecha_creacion.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return [_respuesta_reporte(reporte) for reporte in reportes]


@router.get(
    "/{reporte_id}/descargar",
    summary="Descargar el archivo de un reporte listo",
)
def descargar_reporte(
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
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    if reporte.estado != "listo" or not reporte.archivo_nombre:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El reporte todavía no está listo para descargar.",
        )

    ruta = obtener_ruta_archivo(reporte)
    if ruta is None or not ruta.is_file():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="El archivo generado ya no está disponible.",
        )
    extension = "xlsx" if reporte.formato == "excel" else "pdf"
    tipo_mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if extension == "xlsx"
        else "application/pdf"
    )
    return FileResponse(
        ruta,
        media_type=tipo_mime,
        filename=f"reporte_{reporte.id}.{extension}",
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

    return _respuesta_reporte(reporte)