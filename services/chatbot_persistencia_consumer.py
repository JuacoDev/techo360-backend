import json
import logging
from collections import Counter
from dataclasses import dataclass

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractRobustConnection

from db.session_vector import SessionLocalVector
from services.chatbot_service import agregar_mensaje, conversacion_de_usuario, crear_conversacion

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

# SEGURIDAD: este evento lo publicaba POST /chatbot/send de techo360-chatbot, que
# estaba expuesto sin sesion y ponia en el cuerpo usuario_id y conversacion_id a
# gusto de quien llamaba; ademas RabbitMQ es alcanzable por Tailscale desde el pool
# de workers. Por eso este consumer NO confia en el evento: valida su forma y
# comprueba que la conversacion sea de usuario_id ANTES de escribir. Lo que no pasa
# se descarta (ack, sin reintento: reintentar un evento malo no lo arregla), se
# registra SIN el contenido de los mensajes y se cuenta por motivo.
LARGO_MAXIMO_TEXTO = 20000
DESCARTADOS: Counter = Counter()  # motivo -> eventos descartados desde que arranco el proceso


class EventoInvalido(ValueError):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class EventoIntercambio:
    usuario_id: int
    consentimiento_entrenamiento: bool
    mensaje_usuario: str
    respuesta_asistente: str
    conversacion_id: int | None


def _es_entero(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def validar_evento(payload) -> EventoIntercambio:
    """Forma exacta del evento "intercambio_completo"; EventoInvalido(motivo) si no."""
    if not isinstance(payload, dict):
        raise EventoInvalido("no_es_objeto")
    if payload.get("tipo") != "intercambio_completo":
        raise EventoInvalido("tipo_desconocido")
    usuario_id = payload.get("usuario_id")
    if not _es_entero(usuario_id) or usuario_id <= 0:
        raise EventoInvalido("usuario_id_invalido")
    if not isinstance(payload.get("consentimiento_entrenamiento"), bool):
        raise EventoInvalido("consentimiento_invalido")
    for campo in ("mensaje_usuario", "respuesta_asistente"):
        texto = payload.get(campo)
        if not isinstance(texto, str) or not texto.strip() or len(texto) > LARGO_MAXIMO_TEXTO:
            raise EventoInvalido(f"{campo}_invalido")
    # Opcional: sin conversacion_id se crea una nueva (comportamiento anterior); si
    # viene, se reutiliza SOLO si es de usuario_id (se comprueba en _procesar_evento).
    conversacion_id = payload.get("conversacion_id")
    if conversacion_id is not None and (not _es_entero(conversacion_id) or conversacion_id <= 0):
        raise EventoInvalido("conversacion_id_invalido")
    return EventoIntercambio(
        usuario_id=usuario_id,
        consentimiento_entrenamiento=payload["consentimiento_entrenamiento"],
        mensaje_usuario=payload["mensaje_usuario"],
        respuesta_asistente=payload["respuesta_asistente"],
        conversacion_id=conversacion_id,
    )


def _descartar(motivo: str, **ids) -> None:
    DESCARTADOS[motivo] += 1
    detalle = " ".join(f"{k}={v}" for k, v in ids.items())
    logger.warning(
        "Evento de '%s' descartado (%s) %s total_motivo=%d", EVENTOS_QUEUE, motivo, detalle, DESCARTADOS[motivo]
    )


async def _procesar_evento(message: AbstractIncomingMessage) -> None:
    async with message.process():
        try:
            evento = validar_evento(json.loads(message.body))
        except json.JSONDecodeError:
            _descartar("json_invalido")
            return
        except EventoInvalido as error:
            _descartar(error.motivo)
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
            conversacion_id = evento.conversacion_id
            if conversacion_id is None:
                conversacion_id = crear_conversacion(db, evento.usuario_id, evento.consentimiento_entrenamiento).id
            elif conversacion_de_usuario(db, conversacion_id, evento.usuario_id) is None:
                # Inexistente o de otro usuario: misma respuesta para no revelar cual.
                _descartar("conversacion_ajena_o_inexistente", usuario_id=evento.usuario_id, conversacion_id=conversacion_id)
                return
            agregar_mensaje(db, conversacion_id, "usuario", evento.mensaje_usuario)
            agregar_mensaje(db, conversacion_id, "asistente", evento.respuesta_asistente)
        except Exception:
            logger.exception("No se pudo persistir el intercambio recibido de '%s'", EVENTOS_QUEUE)
        finally:
            db.close()


# aio-pika guarda en RobustConnection los canales, y en RobustChannel las
# colas, con WeakSet (aio_pika/robust_connection.py y robust_channel.py:
# `_queues = defaultdict(WeakSet)`). Tras reconectar, _on_open() restaura SOLO
# lo que sigue vivo ahi. `channel` y `queue` son locales de
# iniciar_consumidor_persistencia(); sin otra referencia fuerte el recolector
# de basura los libera, el WeakSet queda vacio y la reconexion 'funciona' pero
# no re-declara la cola ni re-registra el consumer (consumer_count=0, sin
# excepcion ni log). Reproducido en local con aio-pika 9.4.3 tras recrear o
# reiniciar RabbitMQ y cortar la red, y visto en produccion el 6-oct-2026
# (T-126). Mismo parche que techo360-chatbot y techo360-proyeccion. Los
# clientes RPC (chatbot_rpc_client, proyeccion_rpc_client) NO lo necesitan:
# abren un canal y una cola de respuesta nuevos por peticion.
_CHANNELS_VIVOS: list = []
_QUEUES_VIVAS: list = []


async def iniciar_consumidor_persistencia(rabbitmq_url: str) -> AbstractRobustConnection:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    channel: AbstractChannel = await connection.channel()
    _CHANNELS_VIVOS.append(channel)
    await channel.set_qos(prefetch_count=4)

    queue = await channel.declare_queue(EVENTOS_QUEUE, durable=True)
    _QUEUES_VIVAS.append(queue)
    await queue.consume(_procesar_evento)
    logger.info("Consumidor de '%s' arrancado.", EVENTOS_QUEUE)
    return connection
