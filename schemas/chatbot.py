from datetime import datetime

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


class MensajeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversacion_id: int
    rol: str
    contenido: str
    feedback_util: bool | None
    fecha: datetime
