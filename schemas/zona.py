from pydantic import BaseModel


class ZonaResponse(BaseModel):
    id: int
    municipio: str
    nombre: str
    latitud: float
    longitud: float

    class Config:
        from_attributes = True