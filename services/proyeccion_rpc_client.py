import asyncio
import json
import uuid
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from core.config import settings

REQUEST_QUEUE = "proyeccion_requests"
RPC_TIMEOUT_SECONDS = 200.0

_connection: AbstractRobustConnection | None = None
_connection_lock = asyncio.Lock()


class ProyeccionTimeoutError(TimeoutError):
    pass


async def _get_connection() -> AbstractRobustConnection:
    global _connection
    async with _connection_lock:
        if _connection is None or _connection.is_closed:
            _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    return _connection


async def solicitar_proyeccion(
    payload: dict[str, Any],
    timeout: float = RPC_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """RPC sobre RabbitMQ: publica en REQUEST_QUEUE con una cola de
    respuesta exclusiva por solicitud (reply_to) y espera el mensaje
    de vuelta correlacionado por correlation_id. Lanza
    ProyeccionTimeoutError si el consumidor no responde a tiempo.
    """
    connection = await _get_connection()
    correlation_id = str(uuid.uuid4())
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    channel = await connection.channel()
    try:
        # durable=True y declarada en ambos extremos (aqui y en el
        # consumer de techo360-proyeccion) -- si el consumer arranca
        # despues que el backend publique, el mensaje espera en la cola
        # en vez de perderse (el exchange default no reencola si la cola
        # no existe todavia).
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
            raise ProyeccionTimeoutError(
                "El servicio de proyeccion no respondio a tiempo."
            ) from error
        finally:
            await reply_queue.cancel(consumer_tag)
            await reply_queue.delete()
    finally:
        await channel.close()
