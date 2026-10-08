import asyncio
import json
import logging

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractRobustConnection,
)

from services.reportes_service import procesar_reporte

logger = logging.getLogger(__name__)

REPORTES_QUEUE = "reportes_generacion"

# Robust aio-pika mantiene canales y colas con referencias débiles para
# restaurarlos tras una reconexión; se conservan referencias fuertes aquí.
_CANALES_VIVOS: list[AbstractChannel] = []
_COLAS_VIVAS: list = []


async def _procesar_mensaje(message: AbstractIncomingMessage) -> None:
    async with message.process(requeue=True):
        try:
            payload = json.loads(message.body)
            reporte_id = int(payload["reporte_id"])
            if reporte_id < 1:
                raise ValueError("El ID de reporte debe ser positivo.")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.exception("Mensaje inválido recibido en la cola '%s'.", REPORTES_QUEUE)
            return

        await asyncio.to_thread(procesar_reporte, reporte_id)


async def iniciar_consumidor_reportes(
    rabbitmq_url: str,
) -> tuple[AbstractRobustConnection, AbstractChannel]:
    conexion = await aio_pika.connect_robust(rabbitmq_url)
    canal: AbstractChannel = await conexion.channel()
    _CANALES_VIVOS.append(canal)
    await canal.set_qos(prefetch_count=2)

    cola = await canal.declare_queue(REPORTES_QUEUE, durable=True)
    _COLAS_VIVAS.append(cola)
    await cola.consume(_procesar_mensaje)
    logger.info("Consumidor de '%s' iniciado.", REPORTES_QUEUE)
    return conexion, canal


async def publicar_reporte(canal: AbstractChannel, reporte_id: int) -> None:
    await canal.default_exchange.publish(
        aio_pika.Message(
            body=json.dumps({"reporte_id": reporte_id}).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        ),
        routing_key=REPORTES_QUEUE,
    )
