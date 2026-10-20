from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CrearConversacionRequest(BaseModel):
    # usuario_id NO viaja en el body -- se toma del usuario autenticado
    # (get_current_user), igual que en el resto de la API. El cliente
    # no puede crear una conversacion a nombre de otro usuario.
    consentimiento_entrenamiento: bool


class ConversacionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Fase "expandir": se devuelven ambos. El cliente debe usar public_id; id (entero
    # secuencial) se retira en la fase "contraer" (ver docs de la migracion 0003).
    id: int
    public_id: UUID | None = None
    usuario_id: int
    consentimiento_entrenamiento: bool
    fecha: datetime


class ActualizarFeedbackRequest(BaseModel):
    feedback_util: bool


class CrearMensajeRequest(BaseModel):
    # SEGURIDAD: el cliente solo puede escribir mensajes de rol "usuario". Antes
    # admitia "asistente", lo que permitia falsificar respuestas del bot en la
    # propia conversacion (y meterlas en el dataset si hay consentimiento). Las
    # respuestas del asistente las escribe SOLO el servidor (enviar/reintentar).
    # Literal -> 422 claro si llega otro valor; por defecto "usuario" para los
    # clientes que no lo mandan.
    rol: Literal["usuario"] = "usuario"
    contenido: str


class MensajeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversacion_id: int
    rol: str
    contenido: str
    feedback_util: bool | None
    fecha: datetime
    # Fase C: valores por defecto para que un cliente/objeto sin estos campos
    # (backend anterior a la migracion 0002) siga siendo valido.
    estado: str = "respondido"
    error_detalle: str | None = None
    # 7.1: payload de la grafica que acompana la respuesta del asistente (especificacion + datos resueltos), ver
    # claude/fase7-6-payload.md. None en todo lo demas; no es una columna de `mensajes` sino la lectura de grafica.
    grafica: dict | None = None


class EnviarMensajeRequest(BaseModel):
    contenido: str


class IntercambioResponse(BaseModel):
    # Los dos mensajes ya persistidos (usuario + asistente) del
    # intercambio -- el cliente necesita el id real del mensaje del
    # asistente para poder calificarlo despues (PATCH .../feedback).
    mensaje_usuario: MensajeResponse
    mensaje_asistente: MensajeResponse


class ErrorConMensajeResponse(BaseModel):
    """Cuerpo de 502/503/504 (y 409 por cancelado) al enviar o reintentar con
    estados activos: `detail` sigue siendo un TEXTO (un frontend viejo lo muestra
    igual) y `mensaje_usuario` trae el mensaje ya guardado, con su estado."""

    detail: str
    mensaje_usuario: MensajeResponse | None = None


class ConversacionBloqueadaResponse(BaseModel):
    """Cuerpo del 409 cuando la conversacion ya tiene un mensaje sin resolver."""

    detail: str
    mensaje_pendiente: MensajeResponse | None = None
