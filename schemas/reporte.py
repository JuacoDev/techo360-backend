from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


VariableReporte = Literal[
    "score_privacion",
    "cobertura_acueducto",
    "cobertura_alcantarillado",
    "deficit_habitacional_cuantitativo",
    "deficit_habitacional_cualitativo",
    "densidad_poblacional",
]


class PeriodoReporte(BaseModel):
    anio_inicio: int = Field(ge=2000, le=2042)
    anio_fin: int = Field(ge=2000, le=2042)

    @model_validator(mode="after")
    def validar_rango(self):
        if self.anio_inicio > self.anio_fin:
            raise ValueError("anio_inicio debe ser menor o igual a anio_fin.")
        return self


class OpcionesReporte(BaseModel):
    titulo: str = Field(default="Informe de Indicadores", min_length=1, max_length=250)
    introduccion: str = Field(default="", max_length=10000)
    conclusiones: str = Field(default="", max_length=10000)
    incluir_graficas: bool = True
    comentarios: dict[str, str] = Field(default_factory=dict)


class ZonasReporte(BaseModel):
    departamentos: list[str] = Field(default_factory=list, max_length=33)
    municipios: list[str] = Field(default_factory=list, max_length=1105)

    @model_validator(mode="after")
    def validar_codigos(self):
        if any(not codigo.isdigit() or len(codigo) != 2 for codigo in self.departamentos):
            raise ValueError("Los códigos de departamento deben tener 2 dígitos.")
        if any(not codigo.isdigit() or len(codigo) != 5 for codigo in self.municipios):
            raise ValueError("Los códigos de municipio deben tener 5 dígitos.")
        if len(set(self.departamentos)) != len(self.departamentos):
            raise ValueError("No se permiten códigos de departamento duplicados.")
        if len(set(self.municipios)) != len(self.municipios):
            raise ValueError("No se permiten códigos de municipio duplicados.")
        return self


class ReporteGenerarRequest(BaseModel):
    formato: Literal["pdf", "excel"]
    periodo: PeriodoReporte
    opciones: OpcionesReporte = Field(default_factory=OpcionesReporte)
    variables: list[VariableReporte] = Field(min_length=1, max_length=6)
    zonas: ZonasReporte

    @model_validator(mode="after")
    def validar_seleccion(self):
        if not self.zonas.departamentos and not self.zonas.municipios:
            raise ValueError("Debe seleccionar al menos un departamento o municipio.")
        if len(set(self.variables)) != len(self.variables):
            raise ValueError("No se permiten variables duplicadas.")
        return self


class SerieGraficaPrevia(BaseModel):
    nombre: str
    valores: list[float | None]


class GraficaPrevia(BaseModel):
    variable: str
    nombre: str
    unidad: str
    series: list[SerieGraficaPrevia]
    total_series: int


class PreviaGraficasResponse(BaseModel):
    anios: list[int]
    graficas: list[GraficaPrevia]


class ReporteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int
    titulo: str
    contenido: str
    fecha_creacion: datetime
    estado: str
    formato: str | None
    error: str | None
    descarga_url: str | None = None