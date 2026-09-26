from schemas.proyecciones import PuntoProyeccion, PuntoZona
from services.proyeccion_rpc_client import solicitar_proyeccion


INDICADOR_VIGENTE = "densidad_poblacional"

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
    if indicador != INDICADOR_VIGENTE:
        raise IndicadorNoSoportadoError(
            f"El indicador soportado es '{INDICADOR_VIGENTE}'."
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
    if indicador != INDICADOR_VIGENTE:
        raise IndicadorNoSoportadoError(
            f"El indicador soportado es '{INDICADOR_VIGENTE}'."
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
