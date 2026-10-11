import asyncio
import logging

import aio_pika

from core.config import settings
from services.chatbot_persistencia_consumer import EVENTOS_QUEUE

logger = logging.getLogger(__name__)

INTERVALO_MONITOR_SEGUNDOS = 5

_ultimo_estado = None


async def consultar_estado_rabbitmq() -> dict:
    """
    Comprueba la conexión con RabbitMQ y el número real
    de consumers registrados en la cola obligatoria.
    """
    connection = None

    try:
        connection = await aio_pika.connect_robust(
            settings.rabbitmq_url,
            timeout=5,
        )

        channel = await connection.channel()

        queue = await channel.declare_queue(
            EVENTOS_QUEUE,
            passive=True,
        )

        resultado = queue.declaration_result
        consumers_activos = resultado.consumer_count

        return {
            "rabbitmq": "connected",
            "consumers": {
                EVENTOS_QUEUE: {
                    "active": consumers_activos > 0,
                    "count": consumers_activos,
                }
            },
            "healthy": consumers_activos > 0,
        }

    except Exception:
        logger.exception(
            "No fue posible verificar RabbitMQ o el consumer '%s'.",
            EVENTOS_QUEUE,
        )

        return {
            "rabbitmq": "disconnected",
            "consumers": {
                EVENTOS_QUEUE: {
                    "active": False,
                    "count": 0,
                }
            },
            "healthy": False,
        }

    finally:
        if connection is not None and not connection.is_closed:
            await connection.close()


async def monitorizar_consumers() -> None:
    """
    Comprueba periódicamente el estado y registra los cambios.
    """
    global _ultimo_estado

    while True:
        try:
            estado = await consultar_estado_rabbitmq()
            saludable = estado["healthy"]

            if _ultimo_estado is None:
                logger.info(
                    "Estado inicial de RabbitMQ: %s",
                    estado,
                )

            elif saludable and not _ultimo_estado:
                logger.info(
                    "CONSUMER RESTAURADO: '%s'.",
                    EVENTOS_QUEUE,
                )

            elif not saludable and _ultimo_estado:
                logger.error(
                    "CONSUMER PERDIDO: '%s'. Estado: %s",
                    EVENTOS_QUEUE,
                    estado,
                )

            _ultimo_estado = saludable

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Error inesperado monitorizando consumers RabbitMQ."
            )

        await asyncio.sleep(INTERVALO_MONITOR_SEGUNDOS)