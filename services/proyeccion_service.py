import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from schemas.proyecciones import ComponenteScore, PuntoProyeccion, PuntoZona
from services.proyeccion_cache import proyeccion_cache
from services.proyeccion_rpc_client import solicitar_proyeccion
from services.origen import FIABILIDAD_DATO_REAL, FIABILIDAD_MINIMA, es_real, fiabilidad_de_origen
from services.score_privacion import (
    CODIGOS_FUERA_DEL_UNIVERSO,
    VARIABLE_ACUEDUCTO,
    VARIABLE_ALCANTARILLADO,
    VARIABLE_DEFICIT_COMPUESTO,
    calcular_limites_base,
    calcular_score_privacion,
    normalizar_minmax,
    obtener_deficit_para_anio,
)


logger = logging.getLogger(__name__)

INDICADOR_VIGENTE = "densidad_poblacional"

# Indicador nuevo (decision de producto): el mapa de proyeccion pasa a
# graficar el score de privacion por año, no densidad_poblacional. No
# reemplaza a INDICADOR_VIGENTE -- GET /api/proyecciones/zonas sigue
# aceptando densidad_poblacional sin cambios, ver routers/proyecciones.py.
INDICADOR_SCORE_PRIVACION = "score_privacion"

# Indicadores que de verdad viajan por RPC a techo360-proyeccion (ese
# repo ahora soporta los 3 -- ver INDICADORES_SOPORTADOS en
# app/services/proyeccion.py de ese repo). score_privacion NO esta aqui:
# no es un indicador que techo360-proyeccion conozca, se combina en ESTE
# backend a partir de acueducto + alcantarillado (RPC) + deficit (fijo,
# sin RPC) -- ver calcular_zonas_score_privacion() mas abajo.
INDICADORES_RPC_SOPORTADOS = frozenset({
    INDICADOR_VIGENTE,
    VARIABLE_ACUEDUCTO,
    VARIABLE_ALCANTARILLADO,
})

# Año base real desde el que se fija la escala del score proyectado
# (mismo año que ANIO_DEFAULT en routers/zonas.py). Los limites min/max
# de acueducto/alcantarillado/deficit se calculan UNA vez sobre este año
# y se reutilizan para TODOS los años proyectados -- a diferencia del
# score real (GET /api/zonas), que recalcula el min/max por cada año
# pedido (ver services/score_privacion.py). Si el score real normalizara
# por año y el proyectado tambien pero cada uno con SU PROPIO min/max,
# un mismo score de 0.5 en 2024 y en 2030 no representarian la misma
# situacion absoluta -- fijar el proyectado al año base evita eso.
ANIO_BASE_SCORE = 2024

# >>> AVISO: ya NO es LightGBM (23-sep-2026) <<<
# Estos dos valores decian "LightGBM" / "LightGBM-proyeccion". El
# servicio de proyeccion (techo360-proyeccion) dejo de usar LightGBM para
# densidad_poblacional tras un backtesting contra los años reales
# 2019-2024: la extrapolacion lineal sobre los ultimos puntos reales dio
# RMSE 28.07 contra 223.89 del LightGBM recursivo, y el modelo llegaba a
# correlacionar NEGATIVAMENTE (-0.212) con la tendencia real. El aviso
# completo, con la tabla del backtesting y sus limitaciones, esta en el
# docstring de app/services/proyeccion.py de ese repo.
#
# Se mantienen los MISMOS campos del contrato (`modelo`, `origen`), solo
# cambia su valor -- seguir diciendo "LightGBM" seria reportar algo que
# no es lo que se calculo.
#
# El origen usa el prefijo "proyeccion-" a proposito: es la convencion
# del proyecto para distinguir relleno de dato real, y es la que el
# frontend usa para clasificar la tarjeta (ver PanelIndicadores.jsx y el
# catalogo de originVar en ingestion/db.py). Nota: "LightGBM-proyeccion"
# sigue RESERVADO en ese catalogo para el dia en que algun modelo si
# supere a su baseline -- no se reutiliza aqui.
#
# >>> OJO: estos dos valores estan DUPLICADOS entre repos <<<
# El calculo real vive en techo360-proyeccion
# (app/services/proyeccion.py::METODO_VIGENTE) y este backend solo
# reproduce el nombre; la respuesta del RPC ya trae su propio campo
# "modelo", pero el router arma la respuesta con estas constantes. Si se
# cambia el metodo alla y no aca, la API reporta un nombre que no
# corresponde al calculo -- ya paso una vez al pasar de 2 puntos a 4.
# Mantenerlos sincronizados a mano hasta que se decida propagar el valor
# que devuelve el RPC en vez de hardcodearlo.
MODELO_VIGENTE = "regresion-lineal-4puntos"
ORIGEN_PROYECCION = "proyeccion-regresion-lineal-4puntos"

# 2026-10-10: para score_privacion el respaldo de las coberturas ya NO es la regresion
# sino el ultimo valor real (techo360-proyeccion/app/services/proyeccion.py), y el
# deficit es el congelado de 2024 (o la fila del año si existe). El campo `modelo` de
# GET /api/proyecciones/zonas lo refleja por indicador.
MODELO_SCORE_PRIVACION = "ultimo-valor-real+deficit-2024"
ORIGEN_SCORE_PROYECTADO = "proyeccion-score-privacion"
ORIGEN_SCORE_REAL = "score-privacion"


# Indicadores que /api/proyecciones/zonas y /serie sirven LEYENDO las filas ya materializadas de
# mapcalordb (2024-2042, cargadas con la carga M4): sin RPC, sin regresion y sin respaldo. Los
# nombres son los nameVar exactos de la tabla. densidad_poblacional y score_privacion conservan
# su camino anterior.
INDICADORES_MATERIALIZADOS = frozenset({
    VARIABLE_ACUEDUCTO,
    VARIABLE_ALCANTARILLADO,
    VARIABLE_DEFICIT_COMPUESTO,  # deficit_habitacional_cuantitativo
    "deficit_habitacional_cualitativo",
    "deficit_habitacional_total",
    "tenencia_informal_terrenos",
})
MODELO_MATERIALIZADO = "valores-materializados-mapcalordb"
ORIGEN_MATERIALIZADO = "mapcalordb"

# /serie de un indicador materializado: desde 2024 (ancla observada) hasta ANIO_MAXIMO_ZONAS.
ANIO_MINIMO_SERIE_MATERIALIZADA = 2024


def modelo_de_zonas(indicador: str) -> str:
    if indicador in INDICADORES_MATERIALIZADOS:
        return MODELO_MATERIALIZADO
    return MODELO_SCORE_PRIVACION if indicador == INDICADOR_SCORE_PRIVACION else MODELO_VIGENTE

# >>> Rango real cargado para densidad_poblacional (confirmado 28-sep-2026
# con SQL directo contra MapCalorDB: min/max(yearM) agrupado por
# originVar) <<<
# La carga DANE ("DANE-proyeccion-poblacion-YYYY", 1104 municipios por
# año) cubre 2024-2042 completos. GET /zonas ya usaba ge=2025 (el primer
# año "futuro" con sentido para el Mapa de Proyeccion); ANIO_MAXIMO_ZONAS
# es el tope real de arriba -- pedir un año posterior no tiene ningun
# dato DANE que lo respalde, y antes se extrapolaba en silencio en vez de
# rechazarlo. Si se vuelve a cargar DANE con mas años, este valor hay que
# actualizarlo a mano (no hay forma de derivarlo sin una consulta a la
# base, y este servicio no tiene acceso directo a MapCalorDB -- solo lo
# tiene techo360-proyeccion via RPC).
ANIO_MINIMO_ZONAS = 2025
ANIO_MAXIMO_ZONAS = 2042

# Traduce el "tipo" que pone el consumer de techo360-proyeccion en la
# respuesta de error (ver app/rabbitmq_consumer.py de ese repo) al tipo
# de excepcion que este router ya sabe mapear a un status HTTP.
_ERROR_POR_TIPO = {
    "sin_datos": lambda detail: LookupError(detail),
    "modelo_no_disponible": lambda detail: ModeloNoDisponibleError(detail),
    "rango_invalido": lambda detail: RangoProyeccionInvalidoError(detail),
    "error_interno": lambda detail: ModeloNoDisponibleError(detail),
}


class IndicadorNoSoportadoError(ValueError):
    pass


class ModeloNoDisponibleError(RuntimeError):
    pass


class RangoProyeccionInvalidoError(ValueError):
    pass


async def calcular_proyeccion(
    codigo_municipio: str,
    indicador: str,
    desde: int,
    hasta: int,
) -> list[PuntoProyeccion]:
    if indicador not in INDICADORES_RPC_SOPORTADOS:
        raise IndicadorNoSoportadoError(
            f"Los indicadores soportados son: {', '.join(sorted(INDICADORES_RPC_SOPORTADOS))}."
        )

    if desde > hasta:
        raise RangoProyeccionInvalidoError(
            "El año inicial no puede ser mayor que el año final."
        )

    payload = {
        "codigo_municipio": codigo_municipio,
        "indicador": indicador,
        "anio_fin": hasta,
    }

    respuesta = await solicitar_proyeccion(payload)

    if respuesta.get("status") != "ok":
        detail = respuesta.get("detail") or "El servicio de proyección no está disponible."
        error_factory = _ERROR_POR_TIPO.get(respuesta.get("tipo"), ModeloNoDisponibleError)
        raise error_factory(detail)

    anio_inicio = int(respuesta["anio_inicio"])
    if desde < anio_inicio:
        raise RangoProyeccionInvalidoError(
            f"La proyección debe comenzar después de {anio_inicio - 1}."
        )

    return [
        PuntoProyeccion(anio=int(punto["anio"]), valor=round(float(punto["valor"]), 2))
        for punto in respuesta["serie_proyectada"]
        if desde <= int(punto["anio"]) <= hasta
    ]


async def calcular_zonas(indicador: str, anio: int) -> list[PuntoZona]:
    """T-104: choropleth completo -- pide a techo360-proyeccion el batch
    de todos los municipios en una sola solicitud RPC (payload con
    "tipo": "zonas", ver app/rabbitmq_consumer.py de ese repo), no un
    loop de ~1105 llamadas a calcular_proyeccion() de aqui arriba (eso
    tardaria ~13-85 min medido en vivo, ver diagnostico 26-sep-2026)."""
    if indicador not in INDICADORES_RPC_SOPORTADOS:
        raise IndicadorNoSoportadoError(
            f"Los indicadores soportados son: {', '.join(sorted(INDICADORES_RPC_SOPORTADOS))}."
        )

    payload = {"tipo": "zonas", "indicador": indicador, "anio": anio}
    respuesta = await solicitar_proyeccion(payload)

    if respuesta.get("status") != "ok":
        detail = respuesta.get("detail") or "El servicio de proyección no está disponible."
        error_factory = _ERROR_POR_TIPO.get(respuesta.get("tipo"), ModeloNoDisponibleError)
        raise error_factory(detail)

    # origen/fiabilidad vienen del servicio de proyeccion (etiquetado 2026-10-10). Si aun
    # corre una version vieja que no los manda, quedan en None: la fiabilidad se deriva de
    # es_real mas abajo (_componente) y el resto del contrato no cambia.
    return [
        PuntoZona(
            codigo_municipio=zona["codigo_municipio"],
            valor=zona["valor"],
            es_real=zona["es_real"],
            origen=zona.get("origen"),
            fiabilidad=zona.get("fiabilidad"),
        )
        for zona in respuesta["zonas"]
    ]


def _valor_valido(valor) -> bool:
    return valor is not None and valor == valor  # None y NaN: dato faltante


def leer_zonas_materializadas(indicador: str, anio: int, db: Session) -> list[PuntoZona]:
    """Un SELECT: la fila de (indicador, año) de cada municipio del universo (zonas_municipio sin
    00000/01001). Sin regresion ni respaldo: el municipio sin fila (o con valor NULL) se OMITE de la
    respuesta y se registra en el log. es_real/fiabilidad salen de los helpers de services.origen."""
    if indicador not in INDICADORES_MATERIALIZADOS:
        raise IndicadorNoSoportadoError(f"Indicador no materializado: {indicador}.")
    filas = db.execute(
        text("""
            SELECT m.codigodivipola, m.valuevar, m.originvar, m.intervaloconfianza
            FROM public.mapcalordb m
            JOIN public.zonas_municipio z ON z.codigo_municipio = m.codigodivipola
            WHERE m.namevar = :indicador AND m.yearm = :anio AND z.codigo_municipio <> ALL(:excluidos)
            ORDER BY m.codigodivipola
        """),
        {"indicador": indicador, "anio": anio, "excluidos": list(CODIGOS_FUERA_DEL_UNIVERSO)},
    ).mappings().all()

    zonas: list[PuntoZona] = []
    omitidas = 0
    for fila in filas:
        if not _valor_valido(fila["valuevar"]):
            omitidas += 1
            continue
        origen = fila["originvar"]
        zonas.append(
            PuntoZona(
                codigo_municipio=fila["codigodivipola"],
                valor=float(fila["valuevar"]),
                es_real=es_real(origen),
                origen=origen,
                fiabilidad=fiabilidad_de_origen(origen, fila["intervaloconfianza"]),
            )
        )
    if omitidas:
        logger.warning("zonas materializadas %s/%s: %d filas con valor NULL omitidas", indicador, anio, omitidas)
    return zonas


async def calcular_zonas_materializadas(indicador: str, anio: int, db: Session) -> list[PuntoZona]:
    # La consulta es sincrona (Session de SQLAlchemy): to_thread no bloquea el loop de eventos.
    return await asyncio.to_thread(leer_zonas_materializadas, indicador, anio, db)


def leer_serie_materializada(codigo_municipio: str, indicador: str, desde: int, hasta: int, db: Session) -> list[PuntoProyeccion]:
    """Un SELECT por (municipio, indicador): las filas de [desde, hasta] ordenadas por año. Sin
    regresion ni respaldo: un año sin fila se omite (log). Municipio sin ninguna fila -> LookupError (404)."""
    if indicador not in INDICADORES_MATERIALIZADOS:
        raise IndicadorNoSoportadoError(f"Indicador no materializado: {indicador}.")
    if desde > hasta:
        raise RangoProyeccionInvalidoError("El año inicial no puede ser mayor que el año final.")
    if desde < ANIO_MINIMO_SERIE_MATERIALIZADA or hasta > ANIO_MAXIMO_ZONAS:
        raise RangoProyeccionInvalidoError(
            f"Para {indicador} el rango valido es {ANIO_MINIMO_SERIE_MATERIALIZADA}-{ANIO_MAXIMO_ZONAS}."
        )
    filas = db.execute(
        text("""
            SELECT yearm, valuevar, originvar, intervaloconfianza
            FROM public.mapcalordb
            WHERE codigodivipola = :codigo AND namevar = :indicador AND yearm BETWEEN :desde AND :hasta
            ORDER BY yearm
        """),
        {"codigo": codigo_municipio, "indicador": indicador, "desde": desde, "hasta": hasta},
    ).mappings().all()

    serie = [
        PuntoProyeccion(
            anio=int(f["yearm"]),
            valor=round(float(f["valuevar"]), 2),
            es_real=es_real(f["originvar"]),
            origen=f["originvar"],
            fiabilidad=fiabilidad_de_origen(f["originvar"], f["intervaloconfianza"]),
        )
        for f in filas
        if _valor_valido(f["valuevar"])
    ]
    if not serie:
        raise LookupError(f"No hay datos de {indicador} para el municipio {codigo_municipio}.")
    if len(serie) < hasta - desde + 1:
        logger.warning("serie materializada %s/%s: %d de %d años", indicador, codigo_municipio, len(serie), hasta - desde + 1)
    return serie


async def calcular_serie_materializada(
    codigo_municipio: str, indicador: str, desde: int, hasta: int, db: Session
) -> list[PuntoProyeccion]:
    return await asyncio.to_thread(leer_serie_materializada, codigo_municipio, indicador, desde, hasta, db)


async def _obtener_limites_base_cacheados(db: Session) -> dict:
    """Limites min/max de las 3 variables del score, sobre el año base
    2024 -- se calculan una sola vez (cache Redis sin vencimiento
    practico, TTL_SEGUNDOS=24h igual que el resto de este cache, se
    recalcula solo si Redis los perdio) y se reutilizan para todos los
    años proyectados pedidos despues."""
    cacheado = await proyeccion_cache.obtener_limites_base(ANIO_BASE_SCORE)
    if cacheado is not None:
        return cacheado

    # calcular_limites_base es sincrona (SQLAlchemy Session, no async) --
    # to_thread evita bloquear el loop de eventos mientras corre la
    # consulta SQL, igual que obtener_deficit_base() mas abajo.
    limites = await asyncio.to_thread(calcular_limites_base, db, ANIO_BASE_SCORE)
    await proyeccion_cache.guardar_limites_base(ANIO_BASE_SCORE, limites)
    return limites


def _componente(variable: str, punto: PuntoZona | None) -> ComponenteScore:
    """Insumo de coberturas (viene de calcular_zonas). Sin punto -> componente vacio."""
    if punto is None:
        return ComponenteScore(variable=variable, valor=None, origen=None, es_real=False, fiabilidad=None)
    fiabilidad = punto.fiabilidad
    if fiabilidad is None:  # respuesta de una version vieja del servicio de proyeccion
        fiabilidad = FIABILIDAD_DATO_REAL if punto.es_real else FIABILIDAD_MINIMA
    return ComponenteScore(
        variable=variable, valor=punto.valor, origen=punto.origen, es_real=punto.es_real, fiabilidad=fiabilidad
    )


def _componente_deficit(registro: dict | None) -> ComponenteScore:
    if registro is None:
        return ComponenteScore(variable=VARIABLE_DEFICIT_COMPUESTO, valor=None, origen=None, es_real=False, fiabilidad=None)
    return ComponenteScore(
        variable=VARIABLE_DEFICIT_COMPUESTO,
        valor=registro["valor"],
        origen=registro["origen"],
        es_real=registro["es_real"],
        fiabilidad=registro["fiabilidad"],
    )


async def calcular_zonas_score_privacion(anio: int, db: Session) -> list[PuntoZona]:
    """Score de privacion proyectado (decision de producto, no densidad_poblacional).

    Insumos (cada uno con su origen, es_real y fiabilidad; ver `componentes`):
      * acueducto y alcantarillado: 2 RPC EN PARALELO a techo360-proyeccion
        (calcular_zonas): fila de BD del año si existe (real o proyeccion-*), y si no,
        el ULTIMO VALOR REAL observado como respaldo (es_real=False).
      * deficit_habitacional_cuantitativo: la fila del AÑO PEDIDO si existe en BD; si
        no, el valor congelado de 2024 con es_real=False (obtener_deficit_para_anio).

    Los 3 insumos se normalizan con los limites FIJOS del año base 2024
    (_obtener_limites_base_cacheados), no con el min/max del año proyectado -- asi el
    score proyectado queda en la MISMA escala que el score real de 2024.

    es_real del score = True SOLO si los tres insumos son reales (y el score existe).
    fiabilidad del score = el MINIMO de las fiabilidades de sus componentes. Si falta
    cualquier insumo, valor=None, es_real=False, fiabilidad=None.

    Si falla CUALQUIERA de las 2 llamadas RPC se espera a que ambas terminen
    (return_exceptions=True) y se relanza el primer error: nunca se cachea ni se
    devuelve un score a medias con solo uno de los dos insumos."""
    limites = await _obtener_limites_base_cacheados(db)
    deficit = await asyncio.to_thread(obtener_deficit_para_anio, db, anio, ANIO_BASE_SCORE)

    resultados = await asyncio.gather(
        calcular_zonas(VARIABLE_ACUEDUCTO, anio),
        calcular_zonas(VARIABLE_ALCANTARILLADO, anio),
        return_exceptions=True,
    )
    for resultado in resultados:
        if isinstance(resultado, BaseException):
            raise resultado

    zonas_acueducto, zonas_alcantarillado = resultados
    por_codigo_acueducto = {z.codigo_municipio: z for z in zonas_acueducto}
    por_codigo_alcantarillado = {z.codigo_municipio: z for z in zonas_alcantarillado}

    # Union de codigos presentes en CUALQUIERA de los 2 resultados -- un municipio que
    # aparezca en uno pero no en el otro entra igual (con valor=None); uno que no aparezca
    # en NINGUNO queda fuera de la lista, mismo criterio que calcular_zonas().
    codigos = dict.fromkeys((*por_codigo_acueducto, *por_codigo_alcantarillado))

    zonas: list[PuntoZona] = []
    for codigo in codigos:
        componentes = [
            _componente(VARIABLE_ACUEDUCTO, por_codigo_acueducto.get(codigo)),
            _componente(VARIABLE_ALCANTARILLADO, por_codigo_alcantarillado.get(codigo)),
            _componente_deficit(deficit.get(codigo)),
        ]
        acueducto, alcantarillado, deficit_c = componentes

        indicadores_zona = {
            VARIABLE_ACUEDUCTO: {
                "score_normalizado": normalizar_minmax(acueducto.valor, *limites.get(VARIABLE_ACUEDUCTO, (None, None)))
            },
            VARIABLE_ALCANTARILLADO: {
                "score_normalizado": normalizar_minmax(alcantarillado.valor, *limites.get(VARIABLE_ALCANTARILLADO, (None, None)))
            },
            VARIABLE_DEFICIT_COMPUESTO: {
                "score_normalizado": normalizar_minmax(deficit_c.valor, *limites.get(VARIABLE_DEFICIT_COMPUESTO, (None, None)))
            },
        }
        score = calcular_score_privacion(indicadores_zona)

        if score is None:
            es_real = False
            fiabilidad = None
            origen = None
        else:
            es_real = all(c.es_real for c in componentes)
            fiabilidad = min(c.fiabilidad for c in componentes)
            origen = ORIGEN_SCORE_REAL if es_real else ORIGEN_SCORE_PROYECTADO
        zonas.append(
            PuntoZona(
                codigo_municipio=codigo,
                valor=score,
                es_real=es_real,
                origen=origen,
                fiabilidad=fiabilidad,
                componentes=componentes,
            )
        )

    return zonas
