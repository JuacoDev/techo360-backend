from pydantic import BaseModel


class IndicadorHistoricoItem(BaseModel):
    anio: int
    valor: float | None
    origen: str | None
    tipo: str


class IndicadorHistoricoResponse(BaseModel):
    codigo_divipola: str
    variable: str
    anio_inicio: int
    anio_fin: int
    serie: list[IndicadorHistoricoItem]