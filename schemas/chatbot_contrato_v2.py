"""Contrato RPC v2 para la cola chatbot_requests: modelos pydantic (paso 2.4-A).

Especificacion: claude/fase2-2-contrato-rpc-v2.md (secciones 3 y 4). Solo valida mensajes:
no cambia el comportamiento de ningun servicio (el consumer y el cliente v2 son el paso 4.4).

Hay DOS copias de este archivo (techo360-chatbot/app/contrato_v2.py y
techo360-backend/schemas/chatbot_contrato_v2.py): mismas clases, campos y validaciones; solo
cambia el origen de es_proyeccion(). Se prueban contra el mismo contrato_v2_ejemplos.json
(identico byte a byte en los dos repos). Mantenerlas sincronizadas a mano.

Escrito para pydantic 2.x (API de v2: field_validator / model_validator).
"""
from typing import Annotated, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from services.origen import es_proyeccion  # misma regla que el chatbot (copia local en app/contrato_v2.py)


VERSION_CONTRATO_MAXIMA = 2
MAX_MENSAJES = 12
MAX_CONTENIDO = 8000
MAX_CONTEXTO = 40

TIPOS_ERROR = (
    "cancelado",
    "ollama_no_disponible",
    "payload_invalido",
    "contrato_no_soportado",
    "contexto_excedido",
    "error_interno",
)

Numero = Union[StrictInt, StrictFloat]


class ErrorContrato(Exception):
    """Fallo de validacion del contrato; `tipo` es el de la respuesta de error (seccion 4)."""

    def __init__(self, tipo: str, detail: str):
        super().__init__(detail)
        self.tipo = tipo
        self.detail = detail


class PeticionV1(BaseModel):
    """Forma actual (v1), tomada de routers/chatbot.py (_procesar) y de _procesar_mensaje del consumer.

    El consumer v1 exige mensaje, usuario_id y consentimiento_entrenamiento (KeyError ->
    payload_invalido); conversacion_id, mensaje_id e intento son opcionales.
    """

    version_contrato: Optional[Literal[1]] = None
    mensaje: StrictStr
    usuario_id: StrictInt
    consentimiento_entrenamiento: StrictBool
    conversacion_id: Optional[StrictInt] = None
    mensaje_id: Optional[StrictInt] = None
    intento: Optional[StrictInt] = None


class MensajeV2(BaseModel):
    rol: Literal["system", "user", "assistant"]
    contenido: StrictStr = Field(min_length=1, max_length=MAX_CONTENIDO)


class ContextoItemV2(BaseModel):
    id: StrictStr = Field(min_length=1)
    tipo: Literal["dato", "proyeccion", "fragmento"]
    variable: Optional[StrictStr] = None
    codigodivipola: Optional[StrictStr] = None
    anio: Optional[StrictInt] = None
    valor: Union[Numero, StrictStr]
    unidad: Optional[StrictStr] = None
    origen: StrictStr = Field(min_length=1)
    es_real: StrictBool
    fiabilidad: Optional[StrictInt] = Field(default=None, ge=0, le=100)
    fuente: Optional[StrictStr] = None

    @model_validator(mode="after")
    def _coherencia(self):
        if self.tipo in ("dato", "proyeccion") and isinstance(self.valor, str):
            raise ValueError("valor debe ser numerico para tipo dato/proyeccion")
        if self.es_real and es_proyeccion(self.origen):
            raise ValueError("es_real=true con origen de proyeccion")
        return self


class MetadatosPeticionV2(BaseModel):
    usuario_id: StrictInt
    consentimiento_entrenamiento: StrictBool
    conversacion_id: Optional[StrictInt] = None
    mensaje_id: Optional[StrictInt] = None
    intento: Optional[StrictInt] = None
    version_prompt: Optional[StrictStr] = None


class PeticionV2(BaseModel):
    version_contrato: Literal[2]
    mensajes: List[MensajeV2] = Field(min_length=1, max_length=MAX_MENSAJES)
    contexto: List[ContextoItemV2] = Field(default_factory=list, max_length=MAX_CONTEXTO)
    metadatos: MetadatosPeticionV2

    @field_validator("mensajes")
    @classmethod
    def _orden_roles(cls, v):
        if sum(1 for m in v if m.rol == "system") != 1 or v[0].rol != "system":
            raise ValueError("debe haber exactamente un system y debe ir primero")
        if v[-1].rol != "user":
            raise ValueError("el ultimo mensaje debe ser user")
        return v

    @field_validator("contexto")
    @classmethod
    def _ids_unicos(cls, v):
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de contexto repetidos")
        return v


class MetadatosRespuestaV2(BaseModel):
    modelo: StrictStr = Field(min_length=1)
    digest: Optional[StrictStr] = None
    done_reason: Optional[StrictStr] = None
    prompt_eval_count: Optional[StrictInt] = Field(default=None, ge=0)
    eval_count: Optional[StrictInt] = Field(default=None, ge=0)
    duracion_ms: Optional[StrictInt] = Field(default=None, ge=0)
    contexto_ids: List[StrictStr] = Field(default_factory=list)


class RespuestaOkV2(BaseModel):
    version_contrato: Literal[2]
    status: Literal["ok"]
    response: StrictStr
    metadatos: MetadatosRespuestaV2


class RespuestaErrorV2(BaseModel):
    version_contrato: Literal[2]
    status: Literal["error"]
    tipo: Literal[
        "cancelado",
        "ollama_no_disponible",
        "payload_invalido",
        "contrato_no_soportado",
        "contexto_excedido",
        "error_interno",
    ]
    detail: StrictStr


RespuestaV2 = Annotated[Union[RespuestaOkV2, RespuestaErrorV2], Field(discriminator="status")]
_ADAPTADOR_RESPUESTA = TypeAdapter(RespuestaV2)


def _resumen(exc: ValidationError) -> str:
    """Campos y motivos del fallo, sin el valor recibido (no se vuelca texto de mensajes)."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def validar_peticion(payload) -> Union[PeticionV1, PeticionV2]:
    """version_contrato ausente o 1 -> PeticionV1; 2 -> PeticionV2; mayor -> contrato_no_soportado.

    Cualquier otro fallo (no es objeto, version no entera, esquema) -> payload_invalido.
    """
    if not isinstance(payload, dict):
        raise ErrorContrato("payload_invalido", "el payload no es un objeto")
    version = payload.get("version_contrato", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ErrorContrato("payload_invalido", "version_contrato debe ser un entero >= 1")
    if version > VERSION_CONTRATO_MAXIMA:
        raise ErrorContrato(
            "contrato_no_soportado",
            f"version_contrato {version} mayor que la soportada ({VERSION_CONTRATO_MAXIMA})",
        )
    modelo = PeticionV1 if version == 1 else PeticionV2
    try:
        return modelo.model_validate(payload)
    except ValidationError as exc:
        raise ErrorContrato("payload_invalido", _resumen(exc)) from exc


def validar_respuesta(payload) -> Union[RespuestaOkV2, RespuestaErrorV2]:
    try:
        return _ADAPTADOR_RESPUESTA.validate_python(payload)
    except ValidationError as exc:
        raise ErrorContrato("payload_invalido", _resumen(exc)) from exc
