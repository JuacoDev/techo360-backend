from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from routers.auth import get_current_user
from schemas.proyecciones import ProyeccionResponse
from services.proyeccion_service import (
    ContratoModeloNoConfiguradoError,
    IndicadorNoSoportadoError,
    MODELO_VIGENTE,
    ORIGEN_PROYECCION,
    ModeloNoDisponibleError,
    calcular_proyeccion,
)


router = APIRouter(
    prefix="/api/proyecciones",
    tags=["proyecciones"],
)


@router.get(
    "",
    response_model=ProyeccionResponse,
    summary="Obtener proyección futura",
    description=(
        "Calcula la serie futura de densidad_poblacional mediante "
        "el modelo LightGBM vigente."
    ),
    responses={
        401: {"description": "Requiere autenticación."},
        404: {"description": "No existen datos para el municipio."},
        422: {"description": "El código o indicador no son válidos."},
        503: {"description": "El modelo LightGBM no está disponible."},
    },
)
def obtener_proyeccion_municipio(
    codigo_municipio: str = Query(
        ...,
        min_length=5,
        max_length=5,
        pattern=r"^\d{5}$",
        description="Código DIVIPOLA de cinco dígitos.",
    ),
    indicador: str = Query(
        ...,
        pattern=r"^densidad_poblacional$",
        description="Indicador actualmente soportado.",
    ),
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    try:
        serie = calcular_proyeccion(
            db,
            codigo_municipio,
            indicador,
        )
    except IndicadorNoSoportadoError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (
        ModeloNoDisponibleError,
        ContratoModeloNoConfiguradoError,
    ) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return {
        "codigo_municipio": codigo_municipio,
        "indicador": indicador,
        "modelo": MODELO_VIGENTE,
        "origen": ORIGEN_PROYECCION,
        "serie": serie,
    }