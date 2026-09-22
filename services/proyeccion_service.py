from schemas.proyecciones import PuntoProyeccion
from services.proyeccion_rpc_client import solicitar_proyeccion


INDICADOR_VIGENTE = "densidad_poblacional"
MODELO_VIGENTE = "LightGBM"
ORIGEN_PROYECCION = "LightGBM-proyeccion"

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
        detail = respuesta.get("detail") or "El modelo LightGBM no está disponible."
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
