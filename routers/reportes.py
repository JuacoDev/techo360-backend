import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from models.reporte import Reporte
from routers.auth import get_current_user
from schemas.reporte import (
    GraficasResponse,
    PreviaGraficasResponse,
    ReporteGenerarRequest,
    ReporteHistorial,
    ReporteResponse,
)
from services.reportes_cache import cache_reportes, huella_de
from services.reportes_consumer import publicar_reporte
from services.reportes_service import (
    construir_previa_graficas,
    obtener_datos_reporte,
    obtener_ruta_archivo,
    validar_zonas_existentes,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/reportes",
    tags=["reportes"],
)


def _plantilla(reporte: Reporte) -> str | None:
    solicitud = reporte.solicitud if isinstance(reporte.solicitud, dict) else {}
    return (solicitud.get("opciones") or {}).get("plantilla")


def _respuesta_reporte(reporte: Reporte) -> ReporteResponse:
    listo = reporte.estado == "listo" and (reporte.archivo_nombre or reporte.huella)
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
            "descarga_url": f"/api/reportes/{reporte.id}/descargar" if listo else None,
            "actualizado_en": reporte.actualizado_en,
            "plantilla": _plantilla(reporte),
        }
    )


def _reporte_propio(db: Session, reporte_id: int, usuario: MapCalorUser) -> Reporte:
    reporte = (
        db.query(Reporte)
        .filter(Reporte.id == reporte_id, Reporte.usuario_id == usuario.id)
        .first()
    )
    if reporte is None:
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    return reporte


@router.post(
    "/generar",
    response_model=ReporteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solicitar la generación asíncrona de un reporte",
)
async def generar_reporte(
    solicitud: ReporteGenerarRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    try:
        validar_zonas_existentes(db, solicitud)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    configuracion = solicitud.model_dump(mode="json")
    huella = huella_de(configuracion, current_user.id)

    if solicitud.reemplaza is not None:
        # «Seguir editando»: la nueva versión reemplaza la configuración del mismo reporte del historial.
        reporte = _reporte_propio(db, solicitud.reemplaza, current_user)
        if reporte.estado in {"pendiente", "procesando"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ese reporte todavía se está generando; espera a que termine para editarlo.",
            )
    else:
        reporte = Reporte(usuario_id=current_user.id, contenido="")
        db.add(reporte)
    reporte.titulo = solicitud.opciones.titulo
    reporte.formato = solicitud.formato
    reporte.solicitud = configuracion
    reporte.huella = huella
    reporte.error = None
    reporte.archivo_nombre = None
    reporte.actualizado_en = func.now()

    # La misma configuración pedida el mismo día ya generada en las últimas 24 h: se entrega tal cual.
    if await asyncio.to_thread(cache_reportes.existe, huella):
        reporte.estado = "listo"
        reporte.contenido = "Reporte entregado desde la caché de 24 horas."
        db.commit()
        db.refresh(reporte)
        response.status_code = status.HTTP_200_OK
        return _respuesta_reporte(reporte)

    reporte.estado = "pendiente"
    reporte.contenido = "Reporte pendiente de generación."
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


@router.post(
    "/previa-graficas",
    response_model=PreviaGraficasResponse,
    summary="Series por año de las gráficas de línea (vista previa anterior; se conserva por compatibilidad)",
)
def previa_graficas(
    solicitud: ReporteGenerarRequest,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    try:
        validar_zonas_existentes(db, solicitud)
        return construir_previa_graficas(solicitud, db)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.post(
    "/graficas",
    response_model=GraficasResponse,
    summary="Escenas vectoriales de las gráficas elegidas, para la vista previa (las mismas que usa el PDF)",
)
def graficas_del_reporte(
    solicitud: ReporteGenerarRequest,
    ancho: int = Query(560, ge=280, le=900, description="Ancho del lienzo; el PDF IEEE usa 360."),
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    # Import tardío: estos módulos importan reportes_service.
    from services.reportes_datos_graficas import a_json, escenas_de_solicitud, variables_de_graficas

    if not solicitud.graficas:
        return GraficasResponse(graficas=[])
    try:
        validar_zonas_existentes(db, solicitud)
        zonas, series = obtener_datos_reporte(db, solicitud, variables_de_graficas(solicitud))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    escenas = escenas_de_solicitud(db, solicitud, zonas, series, ancho)
    return GraficasResponse(graficas=[a_json(spec, resultado) for spec, resultado in escenas])


@router.get(
    "/historial",
    response_model=list[ReporteHistorial],
    summary="Historial de reportes del usuario (solo configuración), el más reciente primero",
)
def historial_de_reportes(
    limite: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    reportes = (
        db.query(Reporte)
        .filter(Reporte.usuario_id == current_user.id, Reporte.solicitud.isnot(None))
        .order_by(Reporte.actualizado_en.desc(), Reporte.id.desc())
        .limit(limite)
        .all()
    )
    return [
        ReporteHistorial(
            id=reporte.id,
            titulo=reporte.titulo,
            formato=reporte.formato,
            plantilla=_plantilla(reporte),
            estado=reporte.estado,
            fecha_creacion=reporte.fecha_creacion,
            actualizado_en=reporte.actualizado_en,
        )
        for reporte in reportes
    ]


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
    "/{reporte_id}/solicitud",
    summary="Configuración guardada de un reporte (para seguir editándolo)",
)
def solicitud_de_reporte(
    reporte_id: int,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    reporte = _reporte_propio(db, reporte_id, current_user)
    if not isinstance(reporte.solicitud, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Este reporte no tiene configuración guardada.")
    return {"id": reporte.id, "titulo": reporte.titulo, "solicitud": reporte.solicitud}


@router.get(
    "/{reporte_id}/descargar",
    summary="Descargar el archivo de un reporte listo",
)
def descargar_reporte(
    reporte_id: int,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    reporte = _reporte_propio(db, reporte_id, current_user)
    if reporte.estado != "listo" or not (reporte.archivo_nombre or reporte.huella):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El reporte todavía no está listo para descargar.",
        )

    extension = "xlsx" if reporte.formato == "excel" else "pdf"
    tipo_mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if extension == "xlsx"
        else "application/pdf"
    )
    nombre = f"reporte_{reporte.id}.{extension}"

    contenido = cache_reportes.obtener(reporte.huella)
    if contenido is not None:
        return Response(
            content=contenido,
            media_type=tipo_mime,
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
        )
    ruta = obtener_ruta_archivo(reporte)
    if ruta is not None and ruta.is_file():
        return FileResponse(ruta, media_type=tipo_mime, filename=nombre)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="El archivo ya no está disponible (se conserva 24 horas). Genéralo de nuevo con la configuración guardada.",
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
    return _respuesta_reporte(_reporte_propio(db, reporte_id, current_user))


@router.delete(
    "/{reporte_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Quitar un reporte del historial",
)
def eliminar_reporte(
    reporte_id: int,
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    reporte = _reporte_propio(db, reporte_id, current_user)
    if reporte.estado in {"pendiente", "procesando"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El reporte todavía se está generando.")
    ruta = obtener_ruta_archivo(reporte)
    db.delete(reporte)
    db.commit()
    if ruta is not None:
        ruta.unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
