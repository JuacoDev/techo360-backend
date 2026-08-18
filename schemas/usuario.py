from pydantic import BaseModel


class UsuarioResponse(BaseModel):
    id: int
    municipio: str
    nombre: str
    valor: str
    año: int
    origen: str

    class Config:
        from_attributes = True
