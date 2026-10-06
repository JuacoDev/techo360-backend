from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReporteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int
    titulo: str
    contenido: str
    fecha_creacion: datetime