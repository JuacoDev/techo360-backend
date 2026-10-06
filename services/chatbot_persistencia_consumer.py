import json
import logging

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractRobustConnection

from db.session_vector import SessionLocalVector
from services.chatbot_service import agregar_mensaje, crear_conversacion

logger = logging.getLogger(__name__)

# Contrato interno chatbot -> backend (no lo publica ni consume nadie
# mas, a diferencia de chatbot_requests/proyeccion_requests que si
# comparte Daniel/el pool de labs) -- ver diagnostico de la
# reestructuracion T-97: techo360-chatbot no debe tener credenciales a
# postgres-vector, asi que publica este evento en vez de escribir
# directo (ver app/persistencia.py de techo360-chatbot).
#
# >>> AVISO -- este consumer ya NO es el camino de produccion <<<
# Desde que el backend tiene services/chatbot_rpc_client.py
# (POST /chatbot/conversaciones/{id}/mensajes/enviar), el intercambio
# real usuario<->Ollama se persiste sincronicamente en ese endpoint
# (decision del 26-sep-2026, ver core/config.py: "el backend maneja
# TODA la persistencia") -- para ese camino,
# techo360-chatbot/app/rabbitmq_consumer.py YA NO llama a
# persistir_en_background, para no duplicar filas. Este consumer sigue
# vivo solo porque POST /chatbot/send de techo360-chatbot (endpoint de
# pruebas locales sin RabbitMQ real de por medio) todavia publica aqui.
EVENTOS_QUEUE = "chatbot_eventos_persistencia"


async def _procesar_evento(message: AbstractIncomingMessage) -> None:
    async with message.process():
        try:
            payload = json.loads(message.body)
            tipo = payload.get("tipo")
            if tipo != "intercambio_completo":
                logger.warning("Tipo de evento de persistencia desconocido: %s", tipo)
                return

            usuario_id = payload["usuario_id"]
            consentimiento_entrenamiento = payload["consentimiento_entrenamiento"]
            mensaje_usuario = payload["mensaje_usuario"]
            respuesta_asistente = payload["respuesta_asistente"]
            # Opcional -- POST /chatbot/send de techo360-chatbot (unico
            # publicador que queda de este evento) no siempre tiene una
            # conversacion ya creada. Si no viene, se crea una nueva
            # (comportamiento anterior); si viene, se reutiliza en vez
            # de crear otra fila en conversaciones por cada mensaje.
            conversacion_id = payload.get("conversacion_id")
        except (KeyError, json.JSONDecodeError):
            logger.exception("Evento de persistencia con payload invalido, se descarta")
            return

        # Mismo criterio "best effort" que ya documentaba
        # persistir_intercambio en techo360-chatbot: si falla, se
        # loguea y se descarta el evento (message.process() hace ack
        # igual al salir del bloque, incluso si esta excepcion se
        # atrapa aqui) -- un intercambio de chat ya respondido al
        # usuario no debe reintentarse indefinidamente por un fallo de
        # persistencia.
        db = SessionLocalVector()
        try:
            if conversacion_id is None:
                conversacion_id = crear_conversacion(db, usuario_id, consentimiento_entrenamiento).id
            agregar_mensaje(db, conversacion_id, "usuario", mensaje_usuario)
            agregar_mensaje(db, conversacion_id, "asistente", respuesta_asistente)
        except Exception:
            logger.exception("No se pudo persistir el intercambio recibido de '%s'", EVENTOS_QUEUE)
        finally:
            db.close()


async def iniciar_consumidor_persistencia(rabbitmq_url: str) -> AbstractRobustConnection:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    channel: AbstractChannel = await connection.channel()
    await channel.set_qos(prefetch_count=4)

    queue = await channel.declare_queue(EVENTOS_QUEUE, durable=True)
    await queue.consume(_procesar_evento)
    logger.info("Consumidor de '%s' arrancado.", EVENTOS_QUEUE)
    return connection
