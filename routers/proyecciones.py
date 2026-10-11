from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from routers.auth import get_current_user
from schemas.proyecciones import (
    ProyeccionResponse,
    PuntoProyeccion,
    PuntoZona,
    RangoZonasResponse,
    ZonasResponse,
    RangoDisponibleResponse,
)
from services.proyeccion_cache import proyeccion_cache
from services.proyeccion_rpc_client import ProyeccionTimeoutError
from services.proyeccion_service import (
    ANIO_MAXIMO_ZONAS,
    ANIO_MINIMO_ZONAS,
    INDICADOR_SCORE_PRIVACION,
    IndicadorNoSoportadoError,
    INDICADOR_VIGENTE,
    INDICADORES_MATERIALIZADOS,
    MODELO_MATERIALIZADO,
    MODELO_VIGENTE,
    ORIGEN_MATERIALIZADO,
    ORIGEN_PROYECCION,
    ModeloNoDisponibleError,
    RangoProyeccionInvalidoError,
    calcular_proyeccion,
    calcular_serie_materializada,
    calcular_zonas,
    calcular_zonas_materializadas,
    calcular_zonas_score_privacion,
    modelo_de_zonas,
)


router = APIRouter(
    prefix="/api/proyecciones",
    tags=["proyecciones"],
)

# Indicadores aceptados. densidad_poblacional y score_privacion conservan su camino (RPC /
# calculo); los 6 de INDICADORES_MATERIALIZADOS se leen de mapcalordb (ver proyeccion_service).
PATRON_INDICADOR_ZONAS = "^(" + "|".join([INDICADOR_VIGENTE, INDICADOR_SCORE_PRIVACION, *sorted(INDICADORES_MATERIALIZADOS)]) + ")$"
PATRON_INDICADOR_SERIE = "^(" + "|".join([INDICADOR_VIGENTE, *sorted(INDICADORES_MATERIALIZADOS)]) + ")$"

# >>> RESUELTO -- ya no bloqueante (verificado 28-sep-2026) <<<
# Este endpoint (GET /api/proyecciones/serie, UN municipio) usaba a
# ciegas la regresion para el rango [desde, hasta] pedido, sin verificar
# antes si ya existia un dato REAL en MapCalorDB para alguno de esos años
# -- mismo gap que tenia GET /api/proyecciones/zonas. Ambos quedaron
# resueltos en techo360-proyeccion (commit 27c02cd de JrGG22, 26-sep-2026):
# calcular_serie_proyectada()/calcular_zonas() (ver
# app/services/proyeccion.py de ese repo) consultan
# obtener_datos_municipio_para_anios()/obtener_datos_todos_municipios_para_anio()
# -- SIN el tope `yearM <= anio_actual` -- y sobreescriben con el dato
# real cualquier año de la respuesta que ya tenga fila real en MapCalorDB.
# Verificado en vivo contra la BD real: /serie para 05001 2027-2035
# devuelve los mismos valores exactos de "DANE-proyeccion-poblacion-YYYY",
# no una recta.


@router.get(
    "/serie",
    response_model=ProyeccionResponse,
    response_model_exclude_none=True,
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
        pattern=PATRON_INDICADOR_SERIE,
        description=(
            "densidad_poblacional (RPC) o uno de los 6 indicadores materializados en mapcalordb "
            "(rango 2024-2042, cada punto con es_real/origen/fiabilidad)."
        ),
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
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    materializado = indicador in INDICADORES_MATERIALIZADOS
    cacheado = await proyeccion_cache.obtener_serie(codigo_municipio, desde, hasta, indicador)
    if cacheado is not None:
        serie = [PuntoProyeccion(**punto) for punto in cacheado]
    else:
        try:
            if materializado:
                serie = await calcular_serie_materializada(codigo_municipio, indicador, desde, hasta, db)
            else:
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
            codigo_municipio, desde, hasta, [punto.model_dump() for punto in serie], indicador
        )

    return {
        "codigo_municipio": codigo_municipio,
        "indicador": indicador,
        "modelo": MODELO_MATERIALIZADO if materializado else MODELO_VIGENTE,
        "origen": ORIGEN_MATERIALIZADO if materializado else ORIGEN_PROYECCION,
        "serie": serie,
    }


@router.get(
    "/zonas",
    response_model=ZonasResponse,
    summary="Choropleth completo de densidad_poblacional o score_privacion para un año (T-104)",
    description=(
        "Devuelve el valor (real o proyectado) de densidad_poblacional, "
        "o el score de privación compuesto, para TODOS los municipios en "
        "un año dado, en una sola solicitud -- ver "
        "techo360-proyeccion/app/services/proyeccion.py::calcular_zonas "
        "(una consulta SQL + regresion vectorizada, no un loop de ~1105 "
        "llamadas individuales). indicador=score_privacion combina "
        "acueducto + alcantarillado proyectados (2 RPC en paralelo) con "
        "deficit_habitacional_cuantitativo fijo del año base (no se "
        "proyecta: viene de un censo fijo, CNPV 2018) -- ver "
        "services/proyeccion_service.py::calcular_zonas_score_privacion. "
        "Respuesta cacheada en Redis 24h por (indicador, año) -- ver "
        "services/proyeccion_cache.py."
    ),
    responses={
        401: {"description": "Requiere autenticación."},
        422: {
            "description": (
                "El indicador no es válido, o el año está fuera del rango "
                "con datos reales cargados (ver GET /api/proyecciones/rango)."
            )
        },
        503: {"description": "El servicio de proyección no está disponible."},
        504: {"description": "El servicio de proyección no respondió a tiempo."},
    },
)
async def obtener_zonas_proyeccion(
    anio: int = Query(
        ...,
        ge=ANIO_MINIMO_ZONAS,
        le=ANIO_MAXIMO_ZONAS,
        description=(
            f"Año del choropleth. Rango con datos DANE reales cargados: "
            f"{ANIO_MINIMO_ZONAS}-{ANIO_MAXIMO_ZONAS} (ver GET "
            f"/api/proyecciones/rango)."
        ),
    ),
    indicador: str = Query(
        default=INDICADOR_VIGENTE,
        pattern=PATRON_INDICADOR_ZONAS,
        description=(
            "densidad_poblacional, score_privacion o uno de los 6 indicadores materializados "
            "(cobertura_acueducto, cobertura_alcantarillado, deficit_habitacional_cuantitativo, "
            "deficit_habitacional_cualitativo, deficit_habitacional_total, tenencia_informal_terrenos)."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    cacheado = await proyeccion_cache.obtener_zonas(indicador, anio)
    if cacheado is not None:
        zonas = [PuntoZona(**zona) for zona in cacheado]
    else:
        try:
            if indicador == INDICADOR_SCORE_PRIVACION:
                zonas = await calcular_zonas_score_privacion(anio, db)
            elif indicador in INDICADORES_MATERIALIZADOS:
                zonas = await calcular_zonas_materializadas(indicador, anio, db)
            else:
                zonas = await calcular_zonas(indicador, anio)
        except IndicadorNoSoportadoError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ProyeccionTimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error
        except ModeloNoDisponibleError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        await proyeccion_cache.guardar_zonas(indicador, anio, [zona.model_dump() for zona in zonas])

    return {
        "indicador": indicador,
        "anio": anio,
        "modelo": modelo_de_zonas(indicador),
        "zonas": zonas,
    }


@router.get(
    "/rango",
    response_model=RangoZonasResponse,
    summary="Rango de años válido para GET /zonas",
    description=(
        "Expone el rango de años con datos DANE reales cargados para "
        "densidad_poblacional (ver ANIO_MINIMO_ZONAS/ANIO_MAXIMO_ZONAS en "
        "services/proyeccion_service.py), para que el frontend no tenga "
        "que hardcodear el tope del selector de año -- si se vuelve a "
        "cargar DANE con más años, solo hay que actualizar esas dos "
        "constantes y este endpoint (y el `le=` de GET /zonas) quedan "
        "sincronizados automáticamente."
    ),
    responses={401: {"description": "Requiere autenticación."}},
)
async def obtener_rango_zonas(
    current_user: MapCalorUser = Depends(get_current_user),
):
    return {
        "indicador": INDICADOR_VIGENTE,
        "anio_minimo": ANIO_MINIMO_ZONAS,
        "anio_maximo": ANIO_MAXIMO_ZONAS,
    }

@router.get(
    "/rango-disponible",
    response_model=RangoDisponibleResponse,
    summary="Obtener rango de años disponible para proyecciones",
    description=(
        "Devuelve el año mínimo y máximo disponibles para las "
        "proyecciones, permitiendo que el frontend construya el "
        "selector de año sin hardcodear el rango."
    ),
    responses={
        401: {"description": "Requiere autenticación."},
    },
)
async def obtener_rango_disponible(
    current_user: MapCalorUser = Depends(get_current_user),
):
    return {
        "anio_minimo": ANIO_MINIMO_ZONAS,
        "anio_maximo": ANIO_MAXIMO_ZONAS,
    }