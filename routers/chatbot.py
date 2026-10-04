import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.config import settings
from db.session import get_db
from db.session_vector import get_db_vector
from models.conversacion import Conversacion
from models.mapcalor_user import MapCalorUser
from models.mensaje import Mensaje
from routers.auth import get_current_user
from schemas.chatbot import (
    ActualizarFeedbackRequest,
    ConversacionBloqueadaResponse,
    ConversacionResponse,
    CrearConversacionRequest,
    CrearMensajeRequest,
    EnviarMensajeRequest,
    ErrorConMensajeResponse,
    IntercambioResponse,
    MensajeResponse,
)
from services import chatbot_estados, chatbot_service
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

    # Fase C: un 'pendiente'/'procesando' de hace mas de 4 min ya no tiene a nadie
    # esperando (el RPC tiene tope de 200 s): pasa a 'error' para que se pueda resolver.
    chatbot_estados.barrer_colgados(db)

    return (
        db.query(Mensaje)
        .filter(Mensaje.conversacion_id == conversacion_id)
        .order_by(Mensaje.fecha.asc(), Mensaje.id.asc())
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
        409: {
            "model": ConversacionBloqueadaResponse,
            "description": "Solo con CHATBOT_BLOQUEO_PENDIENTES: la conversacion ya tiene un mensaje sin resolver.",
        },
        503: {"description": "El asistente no esta disponible (Ollama caido o RabbitMQ inalcanzable)."},
        504: {"description": "Ningun worker respondio a tiempo."},
    },
)
async def enviar_mensaje(
    conversacion_id: int,
    datos: EnviarMensajeRequest,
    db: Session = Depends(get_db_vector),
    # Misma sesion que usa get_current_user (FastAPI cachea la dependencia por peticion):
    # se libera antes del RPC (ver _procesar).
    db_usuarios: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.id == conversacion_id, Conversacion.usuario_id == current_user.id)
        .first()
    )
    if conversacion is None:
        raise HTTPException(status_code=404, detail="Conversacion no encontrada.")

    # Fase C: con CHATBOT_BLOQUEO_PENDIENTES=true el mensaje pasa por la maquina de
    # estados (pendiente -> procesando -> respondido/error) y una conversacion con
    # un mensaje sin resolver no admite otro (409). Apagado (por defecto) = el
    # comportamiento de siempre, abajo, sin cambios.
    if settings.chatbot_bloqueo_pendientes:
        return await _enviar_con_estados(db, db_usuarios, conversacion, datos.contenido, current_user)

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


def _json_mensaje(mensaje: Mensaje | None):
    return MensajeResponse.model_validate(mensaje).model_dump(mode="json") if mensaje is not None else None


def _respuesta_error(status: int, detail: str, mensaje: Mensaje | None, clave: str = "mensaje_usuario") -> JSONResponse:
    """`detail` sigue siendo texto (un frontend viejo lo muestra igual); el mensaje
    ya guardado, con su estado, va aparte."""
    return JSONResponse(status_code=status, content={"detail": detail, clave: _json_mensaje(mensaje)})


def _texto_intento_descartado(actual: Mensaje | None) -> str:
    """409 cuando el resultado de un RPC ya no corresponde al estado del mensaje."""
    estado = actual.estado if actual is not None else None
    if estado == chatbot_estados.CANCELADO:
        return "El mensaje fue cancelado; se descartó la respuesta."
    if estado == chatbot_estados.PROCESANDO:
        return "Hay un intento más reciente en curso; se ignoró el resultado de este."
    if estado == chatbot_estados.RESPONDIDO:
        return "El mensaje ya fue respondido por otro intento."
    if estado == chatbot_estados.ERROR:
        return "El mensaje ya estaba marcado con error; se descartó el resultado."
    return "El mensaje ya no existe; se descartó el resultado."


def _descartado(db: Session, mensaje_id: int) -> JSONResponse:
    actual = db.get(Mensaje, mensaje_id)
    if actual is not None:
        db.refresh(actual)
    return _respuesta_error(409, _texto_intento_descartado(actual), actual)


def _fallo(db: Session, mensaje_id: int, intento: int, status: int, detail: str, detalle_guardado: str) -> JSONResponse:
    """El asistente fallo: el mensaje pasa a 'error' SOLO si este intento sigue siendo el
    vigente (no pisa un 'cancelado' ni a un reintento posterior)."""
    mensaje = chatbot_estados.marcar_error(db, mensaje_id, detalle_guardado, intento)
    if mensaje is None:
        return _descartado(db, mensaje_id)
    return _respuesta_error(status, detail, mensaje)


async def _procesar(
    db: Session,
    db_usuarios: Session,
    conversacion: Conversacion,
    mensaje_usuario: Mensaje,
    current_user: MapCalorUser,
):
    """El mensaje ya esta 'procesando': pide la respuesta al asistente y cierra el
    intercambio. Comun a enviar y reintentar. Mismos codigos de error de siempre
    (504 timeout, 503 asistente no disponible, 502 respuesta invalida), pero ahora
    el mensaje del usuario queda en 'error' y viaja en el cuerpo."""
    # Todo lo que hace falta DESPUES del RPC se lee ahora, como valores simples, y se
    # libera la conexion de AMBAS sesiones (la de postgres-vector y la de postgres-users,
    # que get_current_user deja abierta): el RPC dura hasta 200 s y no debe retener
    # conexiones del pool (5+10) todo ese tiempo. Despues del rollback los objetos ORM
    # quedan expirados: de aqui en adelante solo se usan `mensaje_id` e `intento`.
    mensaje_id = mensaje_usuario.id
    intento = mensaje_usuario.intentos
    payload = {
        "mensaje": mensaje_usuario.contenido,
        "usuario_id": current_user.id,
        "consentimiento_entrenamiento": conversacion.consentimiento_entrenamiento,
        "conversacion_id": conversacion.id,
    }
    db.rollback()
    db_usuarios.rollback()

    try:
        respuesta = await solicitar_chatbot(payload)
    except ChatbotTimeoutError as error:
        return _fallo(db, mensaje_id, intento, 504, str(error), chatbot_estados.DETALLE_TIMEOUT)
    except asyncio.CancelledError:
        # El servidor se esta deteniendo con el RPC en vuelo: que no quede 'procesando'.
        chatbot_estados.marcar_error(db, mensaje_id, "El servidor se detuvo mientras se procesaba.", intento)
        raise
    except Exception:
        return _fallo(
            db, mensaje_id, intento, 503,
            "No se pudo contactar al asistente. Intenta de nuevo en unos segundos.",
            "No se pudo contactar al asistente.",
        )

    try:
        if not isinstance(respuesta, dict):
            raise ValueError("respuesta del asistente no es un objeto")
        if respuesta.get("status") != "ok":
            detail = respuesta.get("detail") or "El asistente no pudo generar una respuesta."
            status = 503 if respuesta.get("tipo") == "ollama_no_disponible" else 502
            return _fallo(db, mensaje_id, intento, status, detail, detail)
        texto = respuesta["response"]
        if not isinstance(texto, str) or not texto.strip():
            raise ValueError("respuesta del asistente vacia")
        cerrado = chatbot_estados.cerrar_respondido(db, mensaje_id, texto, intento)
    except Exception:
        # Respuesta mal formada o fallo de la BD al cerrar: el mensaje NO puede quedar
        # 'procesando' (bloquearia la conversacion hasta el barrido de 4 min).
        db.rollback()
        return _fallo(
            db, mensaje_id, intento, 502,
            "El asistente devolvió una respuesta inválida. Intenta de nuevo.",
            "Respuesta inválida del asistente.",
        )
    if cerrado is None:
        return _descartado(db, mensaje_id)  # cancelado, barrido o intento posterior: la respuesta se descarta
    return IntercambioResponse(mensaje_usuario=cerrado[0], mensaje_asistente=cerrado[1])


async def _enviar_con_estados(
    db: Session, db_usuarios: Session, conversacion: Conversacion, contenido: str, current_user: MapCalorUser
):
    chatbot_estados.barrer_colgados(db)
    try:
        mensaje = chatbot_estados.crear_pendiente(db, conversacion.id, contenido)
    except chatbot_estados.ConversacionBloqueada as bloqueo:
        return _respuesta_error(
            409,
            "Tienes un mensaje sin resolver en esta conversación: reintenta, cancela o elimina ese mensaje antes de enviar otro.",
            bloqueo.mensaje_pendiente,
            clave="mensaje_pendiente",
        )
    procesando = chatbot_estados.marcar_procesando(db, mensaje.id)
    if procesando is None:  # cancelado o eliminado entre guardarlo y enviarlo: no se llama al asistente
        return _descartado(db, mensaje.id)
    return await _procesar(db, db_usuarios, conversacion, procesando, current_user)


@router.post(
    "/mensajes/{mensaje_id}/reintentar",
    response_model=IntercambioResponse,
    summary="Reintentar un mensaje del usuario que quedo en error o cancelado",
    description=(
        "Pasa el mensaje (error|cancelado) a 'procesando' de forma atomica -- dos "
        "reintentos a la vez, gana uno -- y vuelve a pedir la respuesta al asistente "
        "con el MISMO mensaje (no crea otro). Devuelve el intercambio completo. "
        "Mismos errores que enviar (504/503/502, con el mensaje en el cuerpo)."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "El mensaje no existe o no pertenece al usuario autenticado."},
        409: {"model": ErrorConMensajeResponse, "description": "El mensaje no esta en error ni cancelado."},
        502: {"model": ErrorConMensajeResponse},
        503: {"model": ErrorConMensajeResponse},
        504: {"model": ErrorConMensajeResponse},
    },
)
async def reintentar_mensaje(
    mensaje_id: int,
    db: Session = Depends(get_db_vector),
    db_usuarios: Session = Depends(get_db),
    current_user: MapCalorUser = Depends(get_current_user),
):
    chatbot_estados.barrer_colgados(db)
    mensaje = chatbot_estados.reintentar(db, mensaje_id, current_user.id)
    if mensaje is None:
        existente = chatbot_estados.obtener_de_usuario(db, mensaje_id, current_user.id)
        if existente is None or existente.rol != "usuario":
            raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
        return _respuesta_error(
            409, f"El mensaje está en estado '{existente.estado}': solo se puede reintentar uno con error o cancelado.", existente
        )
    conversacion = db.get(Conversacion, mensaje.conversacion_id)
    return await _procesar(db, db_usuarios, conversacion, mensaje, current_user)


@router.post(
    "/mensajes/{mensaje_id}/cancelar",
    response_model=MensajeResponse,
    summary="Cancelar un mensaje del usuario sin resolver",
    description=(
        "pendiente|procesando|error -> cancelado. Es idempotente (cancelar uno ya "
        "cancelado devuelve el mensaje). Si estaba 'procesando', el asistente sigue "
        "calculando pero su respuesta se descarta al llegar (no se puede abortar el "
        "calculo). Un mensaje cancelado SIGUE bloqueando la conversacion: hay que "
        "reintentarlo o eliminarlo."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "El mensaje no existe o no pertenece al usuario autenticado."},
        409: {"model": ErrorConMensajeResponse, "description": "El mensaje ya tiene respuesta."},
    },
)
def cancelar_mensaje(
    mensaje_id: int,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    mensaje = chatbot_estados.cancelar(db, mensaje_id, current_user.id)
    if mensaje is not None:
        return mensaje
    existente = chatbot_estados.obtener_de_usuario(db, mensaje_id, current_user.id)
    if existente is None or existente.rol != "usuario":
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    if existente.estado == chatbot_estados.CANCELADO:
        return existente
    return _respuesta_error(409, "El mensaje ya tiene respuesta; no se puede cancelar.", existente)


@router.delete(
    "/mensajes/{mensaje_id}",
    status_code=204,
    summary="Eliminar un mensaje del usuario sin resolver",
    description=(
        "Borra un mensaje de usuario en estado pendiente, error o cancelado y "
        "desbloquea la conversacion. Uno 'procesando' hay que cancelarlo antes. "
        "Un mensaje con respuesta no se puede eliminar."
    ),
    responses={
        401: {"description": "Requiere sesión iniciada."},
        404: {"description": "El mensaje no existe o no pertenece al usuario autenticado."},
        409: {"model": ErrorConMensajeResponse, "description": "Esta procesando o ya tiene respuesta."},
    },
)
def eliminar_mensaje(
    mensaje_id: int,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    resultado = chatbot_estados.eliminar(db, mensaje_id, current_user.id)
    if resultado == "eliminado":
        return Response(status_code=204)
    existente = chatbot_estados.obtener_de_usuario(db, mensaje_id, current_user.id)
    if existente is None or existente.rol != "usuario":
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    if resultado == "en_proceso":
        return _respuesta_error(409, "El mensaje se está procesando: cancélalo antes de eliminarlo.", existente)
    return _respuesta_error(409, "El mensaje ya tiene respuesta; no se puede eliminar.", existente)


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
