from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class CrearConversacionRequest(BaseModel):
    # usuario_id NO viaja en el body -- se toma del usuario autenticado
    # (get_current_user), igual que en el resto de la API. El cliente
    # no puede crear una conversacion a nombre de otro usuario.
    consentimiento_entrenamiento: bool


class ConversacionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int
    consentimiento_entrenamiento: bool
    fecha: datetime


class ActualizarFeedbackRequest(BaseModel):
    feedback_util: bool


class CrearMensajeRequest(BaseModel):
    # Literal en vez de str -- valida en la API (422 claro) en vez de
    # dejar que el CHECK constraint de BD lo rechace como 500 (ver
    # AVISO en models/mensaje.py sobre por que no se repite como Enum
    # alla: aqui es validacion de entrada, no redefinicion del schema).
    rol: Literal["usuario", "asistente"]
    contenido: str


class MensajeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversacion_id: int
    rol: str
    contenido: str
    feedback_util: bool | None
    fecha: datetime


class EnviarMensajeRequest(BaseModel):
    contenido: str


class IntercambioResponse(BaseModel):
    # Los dos mensajes ya persistidos (usuario + asistente) del
    # intercambio -- el cliente necesita el id real del mensaje del
    # asistente para poder calificarlo despues (PATCH .../feedback).
    mensaje_usuario: MensajeResponse
    mensaje_asistente: MensajeResponse
