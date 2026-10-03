import asyncio

from sqlalchemy.orm import Session

from schemas.proyecciones import PuntoProyeccion, PuntoZona
from services.proyeccion_cache import proyeccion_cache
from services.proyeccion_rpc_client import solicitar_proyeccion
from services.score_privacion import (
    VARIABLE_ACUEDUCTO,
    VARIABLE_ALCANTARILLADO,
    VARIABLE_DEFICIT_COMPUESTO,
    calcular_limites_base,
    calcular_score_privacion,
    normalizar_minmax,
    obtener_deficit_base,
)


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

    return [
        PuntoZona(codigo_municipio=zona["codigo_municipio"], valor=zona["valor"], es_real=zona["es_real"])
        for zona in respuesta["zonas"]
    ]


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


async def calcular_zonas_score_privacion(anio: int, db: Session) -> list[PuntoZona]:
    """Score de privacion proyectado (decision de producto, no
    densidad_poblacional): acueducto y alcantarillado se proyectan por
    RPC -- 2 llamadas EN PARALELO (asyncio.gather), no 3: deficit_
    habitacional_cuantitativo NO se proyecta por regresion (viene de un
    censo fijo, CNPV 2018, un solo punto real por municipio para
    siempre -- ver techo360-proyeccion/app/services/proyeccion.py::
    INDICADORES_SOPORTADOS). Para deficit se reutiliza, sin cambios, el
    valor del año base 2024 (obtener_deficit_base) para TODOS los años
    proyectados.

    Los 3 insumos se normalizan con los limites FIJOS del año base 2024
    (_obtener_limites_base_cacheados), no con el min/max del propio año
    proyectado -- asi el score proyectado queda en la MISMA escala que
    el score real de 2024 (ver ANIO_BASE_SCORE arriba para el porque).

    Si falla CUALQUIERA de las 2 llamadas RPC (acueducto o
    alcantarillado) -- incluyendo ProyeccionTimeoutError por cache frio
    en techo360-proyeccion -- se espera a que ambas terminen
    (return_exceptions=True) y se relanza el primer error encontrado:
    nunca se cachea ni se devuelve un score a medias con solo uno de los
    dos insumos proyectados."""
    limites = await _obtener_limites_base_cacheados(db)
    deficit_base = await asyncio.to_thread(obtener_deficit_base, db, ANIO_BASE_SCORE)

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

    # Union de codigos presentes en CUALQUIERA de los 2 resultados -- un
    # municipio que aparezca en uno pero no en el otro entra igual (con
    # valor=None, ver abajo); uno que no aparezca en NINGUNO de los 2
    # (p.ej. sin dato real/regresable para ninguna de las 2 coberturas)
    # queda fuera de la lista, mismo criterio que calcular_zonas().
    codigos = dict.fromkeys((*por_codigo_acueducto, *por_codigo_alcantarillado))

    zonas: list[PuntoZona] = []
    for codigo in codigos:
        punto_acueducto = por_codigo_acueducto.get(codigo)
        punto_alcantarillado = por_codigo_alcantarillado.get(codigo)

        indicadores_zona = {
            VARIABLE_ACUEDUCTO: {
                "score_normalizado": normalizar_minmax(
                    punto_acueducto.valor if punto_acueducto else None,
                    *limites.get(VARIABLE_ACUEDUCTO, (None, None)),
                )
            },
            VARIABLE_ALCANTARILLADO: {
                "score_normalizado": normalizar_minmax(
                    punto_alcantarillado.valor if punto_alcantarillado else None,
                    *limites.get(VARIABLE_ALCANTARILLADO, (None, None)),
                )
            },
            VARIABLE_DEFICIT_COMPUESTO: {
                "score_normalizado": normalizar_minmax(
                    deficit_base.get(codigo),
                    *limites.get(VARIABLE_DEFICIT_COMPUESTO, (None, None)),
                )
            },
        }
        score = calcular_score_privacion(indicadores_zona)

        es_real = bool(
            punto_acueducto
            and punto_alcantarillado
            and punto_acueducto.es_real
            and punto_alcantarillado.es_real
        )
        zonas.append(PuntoZona(codigo_municipio=codigo, valor=score, es_real=es_real))

    return zonas
