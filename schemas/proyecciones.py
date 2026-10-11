from pydantic import BaseModel


class PuntoProyeccion(BaseModel):
    anio: int
    valor: float


class ProyeccionResponse(BaseModel):
    codigo_municipio: str
    indicador: str
    modelo: str
    origen: str
    serie: list[PuntoProyeccion]


class ComponenteScore(BaseModel):
    """Un insumo del score de privacion, tal como lo uso el calculo: valor CRUDO (no
    normalizado), su origen en BD (o del respaldo), si es real y su fiabilidad 0-100."""

    variable: str
    valor: float | None
    origen: str | None
    es_real: bool
    fiabilidad: float | None


class PuntoZona(BaseModel):
    codigo_municipio: str
    # None solo para indicador="score_privacion": si falta cualquiera de
    # los 3 insumos del score para ese municipio/año, se devuelve null en
    # vez de un numero (mismo criterio que score_privacion en
    # GET /api/zonas -- "sin dato", no "privacion cero"). Para
    # densidad_poblacional sigue siendo siempre un float, como antes.
    valor: float | None
    # T-104: distingue si el valor es un dato real ya cargado (el año
    # pedido cae dentro del rango que techo360-proyeccion ya trae como
    # "real") o si se calculo por regresion -- mismo criterio de
    # honestidad que origen/PanelIndicadores.jsx en el frontend, para que
    # el mapa pueda pintar el choropleth distinto (ej. sin el badge ambar
    # de proyeccion) en los municipios con dato real.
    es_real: bool
    # Campos agregados el 2026-10-10 (etiquetado real/proyeccion); opcionales para
    # que respuestas viejas (p.ej. del cache) y consumidores viejos sigan validando.
    # origen: originVar de la fila (o del respaldo calculado). fiabilidad: 0-100
    # (100 = dato real; una proyeccion lleva su intervaloConfianza o la regla por
    # antiguedad). Ambos son None cuando valor es None.
    origen: str | None = None
    fiabilidad: float | None = None
    # Solo para indicador="score_privacion": los 3 insumos con su propio origen.
    componentes: list[ComponenteScore] | None = None


class ZonasResponse(BaseModel):
    indicador: str
    anio: int
    modelo: str
    zonas: list[PuntoZona]


class RangoZonasResponse(BaseModel):
    indicador: str
    anio_minimo: int
    anio_maximo: int

class RangoDisponibleResponse(BaseModel):
    anio_minimo: int
    anio_maximo: int