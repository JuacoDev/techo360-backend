"""Esquema del bloque de grafica del chatbot (paso 2.4-B).

Especificacion: claude/fase2-3-contrato-grafica.md, secciones 2 y 3 (puntos 1 y 2). El modelo
emite, dentro de su respuesta, un bloque ```grafica con un JSON cerrado; aqui solo se valida la
ESTRUCTURA y la COMBINACION (validar_combinacion del catalogo de reportes, que no se copia).
Las comprobaciones 3 (los codigos existen en zonas_municipio) y 4 (hay datos) son del paso 4.6
y no se implementan aqui. No cambia el comportamiento de ningun servicio.

Escrito para pydantic 2.x.
"""
import json
import re
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator, model_validator

from services.reportes_graficas_catalogo import MAX_ZONAS_COMPARAR, MODOS, VARIABLES, validar_combinacion

V_ACTUAL = 1
ANIO_MIN = 2018
ANIO_MAX = 2042
MAX_ANIOS = 25
MAX_TITULO = 80
MIN_ANIOS_LINEAS = 2

# Caracteres de Markdown/HTML que no se admiten en el titulo (4.7 decide si ademas se sanea).
_CARACTERES_PROHIBIDOS_TITULO = set("<>`*#[]")
_PATRON_BLOQUE = re.compile(r"```grafica[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL)

Anio = Annotated[StrictInt, Field(ge=ANIO_MIN, le=ANIO_MAX)]


class ErrorGrafica(Exception):
    """Causa en ('json_invalido', 'bloque_sin_cierre', 'estructura', 'combinacion')."""

    def __init__(self, causa: str, detail: str):
        super().__init__(detail)
        self.causa = causa
        self.detail = detail


class GraficaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # esquema cerrado: una clave desconocida se rechaza

    v: StrictInt
    tipo: StrictStr
    modo: Literal["individual", "comparar", "general"]
    variables: List[StrictStr] = Field(min_length=1)
    zonas: List[Annotated[StrictStr, Field(pattern=r"^[0-9]{5}$")]] = Field(min_length=1, max_length=MAX_ZONAS_COMPARAR)
    anios: List[Anio] = Field(max_length=MAX_ANIOS)
    titulo: Optional[StrictStr] = Field(default=None, max_length=MAX_TITULO)

    @field_validator("v")
    @classmethod
    def _version(cls, v):
        if v != V_ACTUAL:
            raise ValueError("v debe ser 1")
        return v

    @field_validator("variables")
    @classmethod
    def _variables_del_catalogo(cls, v):
        desconocidas = [x for x in v if x not in VARIABLES]
        if desconocidas:
            raise ValueError("variable fuera del catalogo: " + desconocidas[0])
        return v

    @field_validator("variables", "zonas")
    @classmethod
    def _sin_repetidos(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("la lista no admite elementos repetidos")
        return v

    @field_validator("anios")
    @classmethod
    def _anios_ordenados(cls, v):
        if any(b <= a for a, b in zip(v, v[1:])):
            raise ValueError("anios debe estar ordenada de menor a mayor y sin repetidos")
        return v

    @field_validator("titulo")
    @classmethod
    def _titulo_sin_marcado(cls, v):
        if v is not None and (_CARACTERES_PROHIBIDOS_TITULO & set(v)):
            raise ValueError("titulo sin Markdown ni HTML")
        return v

    @model_validator(mode="after")
    def _lineas_con_dos_anios(self):
        if self.tipo == "lineas" and len(self.anios) < MIN_ANIOS_LINEAS:
            raise ValueError("una grafica de lineas exige al menos 2 anios")
        return self


def _resumen(exc: ValidationError) -> str:
    """Campos y motivos del fallo, sin el valor recibido."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def validar_especificacion(payload) -> GraficaSpec:
    """Estructura (esquema cerrado) y combinacion contra el catalogo. Sin acceso a base."""
    if not isinstance(payload, dict):
        raise ErrorGrafica("estructura", "el bloque no es un objeto JSON")
    try:
        spec = GraficaSpec.model_validate(payload)
    except ValidationError as exc:
        raise ErrorGrafica("estructura", _resumen(exc)) from exc
    mensaje = validar_combinacion(spec.tipo, spec.modo, list(spec.variables), len(spec.zonas))
    if mensaje is not None:
        raise ErrorGrafica("combinacion", mensaje)
    return spec


def validar_bloque_json(texto_json: str) -> GraficaSpec:
    try:
        payload = json.loads(texto_json)
    except ValueError as exc:
        raise ErrorGrafica("json_invalido", "el bloque no es JSON valido") from exc
    return validar_especificacion(payload)


def extraer_bloque(texto: str) -> Optional[str]:
    """JSON del PRIMER bloque ```grafica del texto; None si no hay ninguno.

    Un bloque abierto y sin cierre es un error (bloque_sin_cierre): no se intenta repararlo.
    Los bloques posteriores al primero se ignoran.
    """
    if "```grafica" not in texto:
        return None
    m = _PATRON_BLOQUE.search(texto)
    if m is None:
        raise ErrorGrafica("bloque_sin_cierre", "el bloque ```grafica no tiene cierre")
    return m.group(1)
