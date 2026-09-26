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


class PuntoZona(BaseModel):
    codigo_municipio: str
    valor: float
    # T-104: distingue si el valor es un dato real ya cargado (el año
    # pedido cae dentro del rango que techo360-proyeccion ya trae como
    # "real") o si se calculo por regresion -- mismo criterio de
    # honestidad que origen/PanelIndicadores.jsx en el frontend, para que
    # el mapa pueda pintar el choropleth distinto (ej. sin el badge ambar
    # de proyeccion) en los municipios con dato real.
    es_real: bool


class ZonasResponse(BaseModel):
    indicador: str
    anio: int
    modelo: str
    zonas: list[PuntoZona]