import asyncio
import json
import logging
import uuid
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from core.config import settings

logger = logging.getLogger(__name__)

# Nombre de cola acordado con Daniel (pool de workers de laboratorio,
# ver AVISO en techo360-chatbot/app/rabbitmq_consumer.py) -- no cambiar
# sin coordinar con ese extremo.
REQUEST_QUEUE = "chatbot_requests"

# >>> AVISO -- timeout medido real contra el VPS (27-sep-2026) <<<
# Con SOLO el consumer de respaldo del VPS conectado (prioridad 0 -- el
# pool de workers de laboratorio, prioridad 10, offline en ese momento),
# 2 intentos seguidos tardaron ~80s y ~180s sin dejar rastro de exito
# en los logs del contenedor. Causa real, confirmada via SSH+docker
# logs/stats (no era degradacion de recursos ni un consumer roto):
# techo360-chatbot/app/ollama_client.py tiene su PROPIO timeout interno
# de 120s (TIMEOUT_OLLAMA_S) para cubrir la carga en frio del modelo
# GGUF -- y Ollama descarga el modelo de memoria tras inactividad
# (keep_alive por defecto), asi que CUALQUIER mensaje despues de un
# rato sin trafico vuelve a pagar esa carga en frio. Si esa carga +
# generacion supera los 120s internos, rabbitmq_consumer.py atrapa
# OllamaNoDisponibleError y arma una respuesta de error SIN loguear
# nada (a diferencia del except Exception generico, que si loguea) --
# por eso no aparecia ningun rastro en docker logs pese a que el
# consumer si estuvo "trabajando" ese tiempo completo.
#
# Con el pool de laboratorio conectado (prioridad 10, ya validado en
# ~6-9s/~24s por el equipo) responde en segundos de sobra. 200s es
# decision explicita del usuario (27-sep-2026): cubrir tambien el peor
# caso medido del fallback del VPS solo (hasta ~180s, ver AVISO arriba)
# para que el usuario siempre reciba respuesta real aunque el pool de
# laboratorio este offline -- a costa de que, en ese escenario, la UI
# de "pensando" puede durar varios minutos en vez de fallar rapido.
RPC_TIMEOUT_SECONDS = 200.0

# Aviso de cancelacion (opcional, mejor esfuerzo): exchange fanout NO durable al que cada
# consumer del chatbot (VPS y workers) se enlaza con su propia cola exclusiva. Un consumer
# con imagen vieja no se enlaza y el aviso se pierde sin efecto. El nombre y el formato del
# cuerpo ({"mensaje_id": int, "intento": int}) los comparte techo360-chatbot
# (app/rabbitmq_consumer.py): no cambiar sin coordinar con ese extremo.
EXCHANGE_CANCELACIONES = "chatbot_cancelaciones"
# Un aviso solo importa mientras el intento cancelado pueda seguir vivo: tope del RPC
# (200 s) mas margen para un mensaje que espera turno en la cola.
TTL_AVISO_CANCELACION_S = 300

_connection: AbstractRobustConnection | None = None
_connection_lock = asyncio.Lock()


class ChatbotTimeoutError(TimeoutError):
    pass


async def _get_connection() -> AbstractRobustConnection:
    global _connection
    async with _connection_lock:
        if _connection is None or _connection.is_closed:
            _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    return _connection


async def solicitar_chatbot(
    payload: dict[str, Any],
    timeout: float = RPC_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """RPC sobre RabbitMQ hacia el pool de workers de chatbot (cola
    REQUEST_QUEUE). Mismo patron que
    services/proyeccion_rpc_client.solicitar_proyeccion: cola de
    respuesta exclusiva por solicitud (reply_to), correlada por
    correlation_id. Lanza ChatbotTimeoutError si ningun worker responde
    a tiempo.
    """
    connection = await _get_connection()
    correlation_id = str(uuid.uuid4())
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    channel = await connection.channel()
    try:
        # durable=True y declarada en ambos extremos (aqui y en el
        # consumer de techo360-chatbot) -- si el consumer arranca
        # despues que el backend publique, el mensaje espera en la cola
        # en vez de perderse.
        await channel.declare_queue(REQUEST_QUEUE, durable=True)

        reply_queue = await channel.declare_queue(exclusive=True)

        async def on_reply(message: AbstractIncomingMessage) -> None:
            async with message.process():
                if message.correlation_id == correlation_id and not future.done():
                    future.set_result(json.loads(message.body))

        consumer_tag = await reply_queue.consume(on_reply)

        await channel.default_exchange.publish(
            Message(
                body=json.dumps(payload).encode(),
                content_type="application/json",
                correlation_id=correlation_id,
                reply_to=reply_queue.name,
                delivery_mode=DeliveryMode.PERSISTENT,
            ),
            routing_key=REQUEST_QUEUE,
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as error:
            raise ChatbotTimeoutError(
                "El asistente no respondio a tiempo."
            ) from error
        finally:
            await reply_queue.cancel(consumer_tag)
            await reply_queue.delete()
    finally:
        await channel.close()


async def avisar_cancelacion(mensaje_id: int, intento: int) -> None:
    """Publica el aviso de que el `intento` de `mensaje_id` fue cancelado. De mejor
    esfuerzo: nunca lanza (cancelar ya se hizo en la BD y es lo que importa); si falla,
    solo queda en el log."""
    try:
        connection = await _get_connection()
        channel = await connection.channel()
        try:
            exchange = await channel.declare_exchange(EXCHANGE_CANCELACIONES, aio_pika.ExchangeType.FANOUT, durable=False)
            await exchange.publish(
                Message(
                    body=json.dumps({"mensaje_id": mensaje_id, "intento": intento}).encode(),
                    content_type="application/json",
                    delivery_mode=DeliveryMode.NOT_PERSISTENT,
                    expiration=TTL_AVISO_CANCELACION_S,
                ),
                routing_key="",
            )
        finally:
            await channel.close()
    except Exception:  # noqa: BLE001 -- el aviso es opcional; no debe afectar a la cancelacion
        logger.warning("No se pudo publicar el aviso de cancelacion (mensaje %s, intento %s)", mensaje_id, intento, exc_info=True)
