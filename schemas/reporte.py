from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.reportes_graficas_catalogo import MAX_GRAFICAS, validar_combinacion


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


PlantillaReporte = Literal["apa", "ieee", "completo", "comparativo"]
PLANTILLAS_PDF = ("apa", "ieee")
PLANTILLAS_EXCEL = ("completo", "comparativo")


class OpcionesReporte(BaseModel):
    titulo: str = Field(default="Informe de Indicadores", min_length=1, max_length=250)
    introduccion: str = Field(default="", max_length=10000)
    conclusiones: str = Field(default="", max_length=10000)
    incluir_graficas: bool = True
    # PDF: "apa" (por defecto) o "ieee". Excel: "completo" (por defecto) o "comparativo".
    plantilla: PlantillaReporte | None = None
    comentarios: dict[str, str] = Field(default_factory=dict)


TipoGrafica = Literal[
    "coropletico",
    "barras_ranking",
    "barras_agrupadas",
    "barras_apiladas",
    "lineas",
    "radar",
    "dispersion",
    "histograma",
    "boxplot",
    "bullet",
    "gauge",
    "dumbbell",
    "donut",
    "treemap",
    "correlaciones",
    "paneles",
]


class GraficaSpec(BaseModel):
    """Una gráfica del reporte. `zonas` solo se usa en los modos individual (1) y comparar (2 a 10),
    y deben estar entre las zonas seleccionadas; el modo general usa todas las seleccionadas."""

    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    tipo: TipoGrafica
    modo: Literal["individual", "comparar", "general"]
    variables: list[VariableReporte] = Field(min_length=1, max_length=6)
    zonas: list[str] = Field(default_factory=list, max_length=10)
    titulo: str = Field(default="", max_length=120)


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
    # Gráficas elegidas por el usuario, en el orden en que se muestran. Si va vacía y
    # opciones.incluir_graficas es true, se conserva el comportamiento anterior (una línea por variable).
    graficas: list[GraficaSpec] = Field(default_factory=list, max_length=MAX_GRAFICAS)
    # Id de un reporte propio del historial al que reemplaza esta versión ("seguir editándolo").
    reemplaza: int | None = Field(default=None, ge=1)

    def _validar_graficas(self) -> None:
        seleccionadas = set(self.zonas.departamentos) | set(self.zonas.municipios)
        ids = [grafica.id for grafica in self.graficas]
        if len(set(ids)) != len(ids):
            raise ValueError("Las gráficas no pueden repetir su id.")
        for numero, grafica in enumerate(self.graficas, start=1):
            if len(set(grafica.variables)) != len(grafica.variables):
                raise ValueError(f"Gráfica {numero}: no se permiten variables duplicadas.")
            if len(set(grafica.zonas)) != len(grafica.zonas):
                raise ValueError(f"Gráfica {numero}: no se permiten zonas duplicadas.")
            fuera = [codigo for codigo in grafica.zonas if codigo not in seleccionadas]
            if fuera:
                raise ValueError(f"Gráfica {numero}: la zona {fuera[0]} no está entre las seleccionadas del reporte.")
            zonas_del_modo = len(seleccionadas) if grafica.modo == "general" else len(grafica.zonas)
            error = validar_combinacion(grafica.tipo, grafica.modo, list(grafica.variables), zonas_del_modo)
            if error:
                raise ValueError(f"Gráfica {numero}: {error}")

    @model_validator(mode="after")
    def validar_seleccion(self):
        if not self.zonas.departamentos and not self.zonas.municipios:
            raise ValueError("Debe seleccionar al menos un departamento o municipio.")
        if len(set(self.variables)) != len(self.variables):
            raise ValueError("No se permiten variables duplicadas.")
        self._validar_graficas()
        permitidas = PLANTILLAS_PDF if self.formato == "pdf" else PLANTILLAS_EXCEL
        if self.opciones.plantilla is None:
            self.opciones.plantilla = permitidas[0]
        elif self.opciones.plantilla not in permitidas:
            raise ValueError(
                f"La plantilla '{self.opciones.plantilla}' no existe para {self.formato}: use {' o '.join(permitidas)}."
            )
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
    actualizado_en: datetime | None = None
    plantilla: str | None = None


class ReporteHistorial(BaseModel):
    """Una fila del historial: lo justo para listar y volver a abrir la configuración."""

    id: int
    titulo: str
    formato: str | None
    plantilla: str | None
    estado: str
    fecha_creacion: datetime
    actualizado_en: datetime


class GraficaEscena(BaseModel):
    id: str
    tipo: str
    modo: str
    titulo: str
    escena: dict
    tabla: dict
    nota: str = ""


class GraficasResponse(BaseModel):
    graficas: list[GraficaEscena]