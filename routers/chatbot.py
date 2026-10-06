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
    EnviarMensajeRequest,
    IntercambioResponse,
    MensajeResponse,
)
from services import chatbot_service
from services.chatbot_rpc_client import ChatbotTimeoutError, solicitar_chatbot

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


@router.get(
    "/conversaciones/{conversacion_id}/mensajes",
    response_model=list[MensajeResponse],
    summary="Obtener los mensajes de una conversación",
    description=(
        "Devuelve todos los mensajes de una conversación existente, "
        "ordenados desde el más antiguo hasta el más reciente. "
        "Solo el propietario de la conversación puede consultar sus mensajes."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {
            "description": (
                "La conversación no existe o no pertenece "
                "al usuario autenticado."
            )
        },
    },
)
def listar_mensajes_conversacion(
    conversacion_id: int,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    conversacion = (
        db.query(Conversacion)
        .filter(
            Conversacion.id == conversacion_id,
            Conversacion.usuario_id == current_user.id,
        )
        .first()
    )

    if conversacion is None:
        raise HTTPException(
            status_code=404,
            detail="Conversación no encontrada.",
        )

    return (
        db.query(Mensaje)
        .filter(Mensaje.conversacion_id == conversacion_id)
        .order_by(Mensaje.fecha.asc())
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


@router.get(
    "/conversaciones/{conversacion_id}/mensajes",
    response_model=list[MensajeResponse],
    summary="Listar los mensajes de una conversacion existente",
    description=(
        "Devuelve todos los mensajes de la conversacion, ordenados por "
        "fecha ascendente (para reabrir el historial de una "
        "conversacion en el orden en que ocurrio)."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "La conversacion no existe o no pertenece al usuario autenticado."},
    },
)
def listar_mensajes(
    conversacion_id: int,
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

    return (
        db.query(Mensaje)
        .filter(Mensaje.conversacion_id == conversacion_id)
        .order_by(Mensaje.fecha.asc())
        .all()
    )


@router.post(
    "/conversaciones/{conversacion_id}/mensajes/enviar",
    response_model=IntercambioResponse,
    status_code=201,
    summary="Enviar un mensaje real del usuario y obtener la respuesta del asistente",
    description=(
        "Persiste el mensaje del usuario, pide la respuesta al pool de "
        "workers de chatbot via RPC sobre RabbitMQ (cola "
        "'chatbot_requests', ver services/chatbot_rpc_client.py) y "
        "persiste la respuesta del asistente en la misma conversacion. "
        "El backend persiste sincronicamente aqui (decision del "
        "26-sep-2026, ver core/config.py: 'el backend maneja TODA la "
        "persistencia') -- por eso el consumer de "
        "'chatbot_eventos_persistencia' ya no se dispara para este "
        "camino (ver AVISO en techo360-chatbot/app/rabbitmq_consumer.py), "
        "solo para el endpoint de pruebas locales POST /chatbot/send de "
        "techo360-chatbot. La respuesta puede tardar varios segundos "
        "(Ollama sin GPU en el VPS)."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "La conversacion no existe o no pertenece al usuario autenticado."},
        503: {"description": "El asistente no esta disponible (Ollama caido o RabbitMQ inalcanzable)."},
        504: {"description": "Ningun worker respondio a tiempo."},
    },
)
async def enviar_mensaje(
    conversacion_id: int,
    datos: EnviarMensajeRequest,
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

    mensaje_usuario = chatbot_service.agregar_mensaje(db, conversacion_id, "usuario", datos.contenido)

    try:
        respuesta = await solicitar_chatbot(
            {
                "mensaje": datos.contenido,
                "usuario_id": current_user.id,
                "consentimiento_entrenamiento": conversacion.consentimiento_entrenamiento,
                "conversacion_id": conversacion_id,
            }
        )
    except ChatbotTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="No se pudo contactar al asistente. Intenta de nuevo en unos segundos.",
        ) from error

    if respuesta.get("status") != "ok":
        tipo = respuesta.get("tipo")
        detail = respuesta.get("detail") or "El asistente no pudo generar una respuesta."
        if tipo == "ollama_no_disponible":
            raise HTTPException(status_code=503, detail=detail)
        raise HTTPException(status_code=502, detail=detail)

    mensaje_asistente = chatbot_service.agregar_mensaje(
        db, conversacion_id, "asistente", respuesta["response"]
    )

    return IntercambioResponse(mensaje_usuario=mensaje_usuario, mensaje_asistente=mensaje_asistente)


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
