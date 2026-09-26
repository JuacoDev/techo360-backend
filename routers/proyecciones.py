from fastapi import APIRouter, Depends, HTTPException, Query

from models.mapcalor_user import MapCalorUser
from routers.auth import get_current_user
from schemas.proyecciones import ProyeccionResponse, PuntoProyeccion, PuntoZona, ZonasResponse
from services.proyeccion_cache import proyeccion_cache
from services.proyeccion_rpc_client import ProyeccionTimeoutError
from services.proyeccion_service import (
    IndicadorNoSoportadoError,
    INDICADOR_VIGENTE,
    MODELO_VIGENTE,
    ORIGEN_PROYECCION,
    ModeloNoDisponibleError,
    RangoProyeccionInvalidoError,
    calcular_proyeccion,
    calcular_zonas,
)


router = APIRouter(
    prefix="/api/proyecciones",
    tags=["proyecciones"],
)

# >>> AVISO -- BLOQUEANTE para Mapa de Proyeccion (25-sep-2026), no deuda
# tecnica suelta -- SIGUE APLICANDO tras T-104 (26-sep-2026) <<<
# Este endpoint SIEMPRE calcula la regresion para el rango [desde, hasta]
# pedido, sin verificar antes si ya existe un dato REAL en MapCalorDB
# para alguno de esos años. Desde el 25-sep-2026, densidad_poblacional
# tiene cobertura DANE real completa 2024-2042 (ver
# ingestion/actualizar_densidad_dane.py) -- si a Manuel/Jeremy se les
# asigna Mapa de Proyeccion y este endpoint se conecta al frontend tal
# cual esta hoy, cualquier año pedido dentro de ese rango devolveria
# "origen": "proyeccion-regresion-lineal-4puntos" (ver
# services/proyeccion_service.py) en vez del dato real DANE que ya
# existe -- viola la regla ya establecida en el proyecto de que un dato
# real siempre gana sobre uno proyectado (ver PanelIndicadores.jsx en
# techo360-frontend, mismo criterio).
#
# T-104 (GET /api/proyecciones/zonas, mas abajo) SI tiene una mitigacion
# PARCIAL de este gap (ver AVISO en
# techo360-proyeccion/app/services/proyeccion.py::calcular_zonas) -- pero
# solo cubre años <= anio_actual del servidor (hoy 2026). Este endpoint
# de UN municipio (GET /api/proyecciones/serie) NO tiene ni siquiera esa
# mitigacion parcial todavia. Fix completo pendiente: antes de calcular
# la regresion para un año, consultar si MapCalorDB ya tiene una fila
# real (originVar NOT LIKE 'proyeccion-%' y NOT LIKE '%-proyeccion') para
# ese codigo_municipio+año, y devolverla tal cual.


@router.get(
    "/serie",
    response_model=ProyeccionResponse,
    summary="Obtener proyección futura para un municipio",
    description=(
        "Calcula la serie futura de densidad_poblacional mediante "
        "regresión lineal sobre los últimos puntos reales del "
        "municipio (ver services/proyeccion_service.py: ya no se usa "
        "LightGBM para esta variable). Respuesta cacheada en Redis "
        "24h por (codigo_municipio, desde, hasta) -- ver "
        "services/proyeccion_cache.py."
    ),
    responses={
        401: {"description": "Requiere autenticación."},
        404: {"description": "No existen datos para el municipio."},
        422: {"description": "El código o indicador no son válidos."},
        503: {"description": "El servicio de proyección no está disponible."},
        504: {"description": "El servicio de proyección no respondió a tiempo."},
    },
)
async def obtener_proyeccion_municipio(
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
    desde: int = Query(
        ...,
        ge=1,
        description="Primer año futuro solicitado.",
    ),
    hasta: int = Query(
        ...,
        ge=1,
        description="Último año futuro solicitado.",
    ),
    current_user: MapCalorUser = Depends(get_current_user),
):
    cacheado = await proyeccion_cache.obtener_serie(codigo_municipio, desde, hasta)
    if cacheado is not None:
        serie = [PuntoProyeccion(**punto) for punto in cacheado]
    else:
        try:
            serie = await calcular_proyeccion(
                codigo_municipio,
                indicador,
                desde,
                hasta,
            )
        except IndicadorNoSoportadoError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RangoProyeccionInvalidoError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ProyeccionTimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error
        except ModeloNoDisponibleError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        await proyeccion_cache.guardar_serie(
            codigo_municipio, desde, hasta, [punto.model_dump() for punto in serie]
        )

    return {
        "codigo_municipio": codigo_municipio,
        "indicador": indicador,
        "modelo": MODELO_VIGENTE,
        "origen": ORIGEN_PROYECCION,
        "serie": serie,
    }


@router.get(
    "/zonas",
    response_model=ZonasResponse,
    summary="Choropleth completo de densidad_poblacional para un año (T-104)",
    description=(
        "Devuelve el valor (real o proyectado) de densidad_poblacional "
        "para TODOS los municipios en un año dado, en una sola "
        "solicitud -- ver techo360-proyeccion/app/services/proyeccion.py"
        "::calcular_zonas (una consulta SQL + regresion vectorizada, no "
        "un loop de ~1105 llamadas individuales). Respuesta cacheada en "
        "Redis 24h por año -- ver services/proyeccion_cache.py."
    ),
    responses={
        401: {"description": "Requiere autenticación."},
        422: {"description": "El indicador no es válido."},
        503: {"description": "El servicio de proyección no está disponible."},
        504: {"description": "El servicio de proyección no respondió a tiempo."},
    },
)
async def obtener_zonas_proyeccion(
    anio: int = Query(..., ge=2025, description="Año del choropleth."),
    indicador: str = Query(
        default=INDICADOR_VIGENTE,
        pattern=r"^densidad_poblacional$",
        description="Indicador actualmente soportado.",
    ),
    current_user: MapCalorUser = Depends(get_current_user),
):
    cacheado = await proyeccion_cache.obtener_zonas(anio)
    if cacheado is not None:
        zonas = [PuntoZona(**zona) for zona in cacheado]
    else:
        try:
            zonas = await calcular_zonas(indicador, anio)
        except IndicadorNoSoportadoError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ProyeccionTimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error
        except ModeloNoDisponibleError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        await proyeccion_cache.guardar_zonas(anio, [zona.model_dump() for zona in zonas])

    return {
        "indicador": indicador,
        "anio": anio,
        "modelo": MODELO_VIGENTE,
        "zonas": zonas,
    }
