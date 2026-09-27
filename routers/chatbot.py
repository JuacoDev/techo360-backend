from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.session_vector import get_db_vector
from models.conversacion import Conversacion
from models.mapcalor_user import MapCalorUser
from models.mensaje import Mensaje
from routers.auth import get_current_user
from schemas.chatbot import (
    ActualizarFeedbackRequest,
    ConversacionResponse,
    CrearConversacionRequest,
    CrearMensajeRequest,
    MensajeResponse,
)
from services import chatbot_service

router = APIRouter(
    prefix="/chatbot",
    tags=["chatbot"],
)


@router.post(
    "/conversaciones",
    response_model=ConversacionResponse,
    status_code=201,
    summary="Crear una conversacion nueva",
    description=(
        "Crea una fila en conversaciones (postgres-vector) para el "
        "usuario autenticado. consentimiento_entrenamiento se guarda "
        "como snapshot al momento de crear la conversacion -- si el "
        "usuario cambia su consentimiento despues, esta fila conserva "
        "el valor que tenia en este momento (ver AVISO en la migracion, "
        "techo360-chatbot/migrations/0001_conversaciones_mensajes.sql)."
    ),
    responses={401: {"description": "Requiere sesión iniciada."}},
)
def crear_conversacion(
    datos: CrearConversacionRequest,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    return chatbot_service.crear_conversacion(db, current_user.id, datos.consentimiento_entrenamiento)


@router.get(
    "/conversaciones",
    response_model=list[ConversacionResponse],
    summary="Listar las conversaciones del usuario autenticado",
    description=(
        "Devuelve solo las conversaciones del usuario autenticado -- "
        "es historial personal, no una vista de administrador (no hay "
        "sistema de roles todavia, ver AVISO en routers/zonas.py). "
        "Ordenadas por fecha descendente (la mas reciente primero)."
    ),
    responses={401: {"description": "Requiere sesión iniciada."}},
)
def listar_conversaciones(
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    return (
        db.query(Conversacion)
        .filter(Conversacion.usuario_id == current_user.id)
        .order_by(Conversacion.fecha.desc())
        .all()
    )


@router.post(
    "/conversaciones/{conversacion_id}/mensajes",
    response_model=MensajeResponse,
    status_code=201,
    summary="Agregar un mensaje a una conversacion existente",
    description=(
        "Usa la misma funcion de servicio (services/chatbot_service.py) "
        "que el consumer de 'chatbot_eventos_persistencia' -- HTTP y "
        "RabbitMQ terminan en el mismo mecanismo, ver AVISO en "
        "chatbot_persistencia_consumer.py. Solo el dueno de la "
        "conversacion puede agregarle mensajes."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "La conversacion no existe o no pertenece al usuario autenticado."},
    },
)
def crear_mensaje(
    conversacion_id: int,
    datos: CrearMensajeRequest,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.id == conversacion_id, Conversacion.usuario_id == current_user.id)
        .first()
    )
    if conversacion is None:
        raise HTTPException(status_code=404, detail="Conversacion no encontrada.")

    return chatbot_service.agregar_mensaje(db, conversacion_id, datos.rol, datos.contenido)


@router.patch(
    "/mensajes/{mensaje_id}/feedback",
    response_model=MensajeResponse,
    summary="Marcar un mensaje del asistente como util o no",
    description=(
        "Actualiza feedback_util del mensaje. Solo el dueno de la "
        "conversacion (via join a Conversacion) puede calificarlo -- "
        "si el mensaje no existe o no le pertenece al usuario "
        "autenticado, responde 404 en ambos casos, para no revelar "
        "si el id existe."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "El mensaje no existe o no pertenece al usuario autenticado."},
    },
)
def actualizar_feedback_mensaje(
    mensaje_id: int,
    datos: ActualizarFeedbackRequest,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    mensaje = (
        db.query(Mensaje)
        .join(Conversacion, Conversacion.id == Mensaje.conversacion_id)
        .filter(Mensaje.id == mensaje_id, Conversacion.usuario_id == current_user.id)
        .first()
    )
    if mensaje is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")

    mensaje.feedback_util = datos.feedback_util
    db.commit()
    db.refresh(mensaje)
    return mensaje
