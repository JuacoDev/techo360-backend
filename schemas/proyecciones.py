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