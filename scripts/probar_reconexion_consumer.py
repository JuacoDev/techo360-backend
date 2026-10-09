"""Prueba de reconexion con RabbitMQ para el backend (T-126).

Modos (primer argumento):
  persistencia    consumer REAL de services/chatbot_persistencia_consumer.py
                  ('chatbot_eventos_persistencia'): tras la accion la cola debe
                  reaparecer con consumer_count=1 y un evento publicado debe
                  llegar a la persistencia (BD simulada).
  rpc-chatbot     cliente REAL services/chatbot_rpc_client.solicitar_chatbot
  rpc-proyeccion  cliente REAL services/proyeccion_rpc_client.solicitar_proyeccion
                  (una peticion NUEVA debe recibir respuesta de un respondedor
                  simulado que atiende la cola de peticiones). Estos clientes
                  abren canal y cola de respuesta exclusiva por peticion, asi que
                  no necesitan retencion; esta prueba lo comprueba.
Limite conocido: una peticion RPC EN VUELO durante un corte pierde su cola de
respuesta exclusiva y termina en timeout (hasta 200 s); no se cubre aqui.

Escenarios: recreate (broker nuevo y vacio, como el VPS), restart (docker
restart), netcut (docker network disconnect/connect, heartbeat corto).
Levanta un RabbitMQ DESECHABLE en Docker (nunca toca el del VPS). La BD y la
config se sustituyen por stubs en sys.modules ANTES de importar los modulos
reales (SQLAlchemy y pydantic-settings no hacen falta; en Windows App Control
bloquea SQLAlchemy).

Requiere: Docker en marcha y `pip install aio-pika==9.4.3`.
Uso:
    python scripts/probar_reconexion_consumer.py persistencia            # 3 escenarios x3
    python scripts/probar_reconexion_consumer.py rpc-chatbot recreate 1  # uno, 1 ciclo
Variables opcionales: RMQ_CONTENEDOR, RMQ_PUERTO (para correr modos en paralelo).
"""
import asyncio
import gc
import json
import os
import subprocess
import sys
import time
import types
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aio_pika
from aio_pika import DeliveryMode, Message

MODO = sys.argv[1] if len(sys.argv) > 1 else "persistencia"
CONTENEDOR = os.environ.get("RMQ_CONTENEDOR", f"rmq-reconexion-{MODO}")
PUERTO = int(os.environ.get("RMQ_PUERTO", "5681"))
RED = f"{CONTENEDOR}-net"
# heartbeat corto: sin el, un corte de red tarda ~minutos en detectarse
URL = f"amqp://guest:guest@localhost:{PUERTO}/?heartbeat=5"
ESPERA_MAX_S = 60

# ---- stubs (antes de importar los modulos reales del backend) ----
EVENTOS_PERSISTIDOS: list[str] = []


class _DbFalsa:
    def close(self) -> None:
        pass


def _agregar_mensaje(_db, _conversacion_id, rol, contenido) -> None:
    if rol == "usuario":
        EVENTOS_PERSISTIDOS.append(contenido)


def _crear_conversacion(_db, _usuario_id, _consentimiento):
    return types.SimpleNamespace(id=1)


_m = types.ModuleType("db.session_vector")
_m.SessionLocalVector = lambda: _DbFalsa()
sys.modules["db.session_vector"] = _m
_m = types.ModuleType("services.chatbot_service")
_m.agregar_mensaje, _m.crear_conversacion = _agregar_mensaje, _crear_conversacion
_m.conversacion_de_usuario = lambda _db, _cid, _uid: types.SimpleNamespace(id=_cid)  # el consumer verifica propiedad
sys.modules["services.chatbot_service"] = _m
_cfg = types.ModuleType("core.config")
_cfg.settings = types.SimpleNamespace(rabbitmq_url=URL)
sys.modules["core.config"] = _cfg

import services.chatbot_persistencia_consumer as persistencia  # noqa: E402 -- tras los stubs, a proposito

COLA_POR_MODO = {
    "persistencia": persistencia.EVENTOS_QUEUE,
    "rpc-chatbot": "chatbot_requests",
    "rpc-proyeccion": "proyeccion_requests",
}
COLA = COLA_POR_MODO[MODO]
_RESPONDEDOR_VIVO: list = []  # el respondedor simulado tambien debe sobrevivir al corte


def docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _run_broker() -> None:
    docker("run", "-d", "--name", CONTENEDOR, "--hostname", CONTENEDOR, "--network", RED,
           "-e", "RABBITMQ_ERLANG_COOKIE=reprocookie", "-p", f"{PUERTO}:5672",
           "rabbitmq:3.13-management-alpine", check=True)


def levantar_broker() -> None:
    docker("rm", "-f", CONTENEDOR)
    docker("network", "create", RED)
    _run_broker()
    esperar_broker()


def esperar_broker() -> None:
    time.sleep(10)  # un `exec` como root en el arranque deja la cookie de Erlang ilegible
    t0 = time.time()
    while time.time() - t0 < 120:
        r = docker("exec", "-u", "rabbitmq", CONTENEDOR, "rabbitmq-diagnostics", "-q", "check_port_connectivity")
        if r.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("el broker no quedo listo")


def consumers_de_la_cola() -> int | None:
    r = docker("exec", "-u", "rabbitmq", CONTENEDOR, "rabbitmqctl", "list_queues", "--no-table-headers", "name", "consumers")
    for linea in r.stdout.splitlines():
        partes = linea.split()
        if len(partes) == 2 and partes[0] == COLA:
            return int(partes[1])
    return None  # la cola no existe


def accion(escenario: str) -> None:
    if escenario == "recreate":
        docker("rm", "-f", CONTENEDOR)
        _run_broker()
    elif escenario == "restart":
        docker("restart", CONTENEDOR, check=True)
    elif escenario == "netcut":
        docker("network", "disconnect", RED, CONTENEDOR, check=True)
        time.sleep(20)  # > 2 heartbeats: el cliente detecta el corte
        docker("network", "connect", RED, CONTENEDOR, check=True)
    else:
        raise SystemExit(f"escenario desconocido: {escenario}")
    esperar_broker()


# ---- modo persistencia ----
async def evento_se_persiste() -> bool:
    tag = f"evt-{uuid.uuid4()}"
    connection = await aio_pika.connect_robust(URL)
    try:
        channel = await connection.channel()
        await channel.declare_queue(COLA, durable=True)
        await channel.default_exchange.publish(
            Message(body=json.dumps({"tipo": "intercambio_completo", "usuario_id": 1,
                                     "consentimiento_entrenamiento": False,
                                     "mensaje_usuario": tag, "respuesta_asistente": "r"}).encode(),
                    content_type="application/json", delivery_mode=DeliveryMode.PERSISTENT),
            routing_key=COLA)
    finally:
        await connection.close()
    t0 = time.time()
    while time.time() - t0 < 10:
        if tag in EVENTOS_PERSISTIDOS:
            return True
        await asyncio.sleep(0.5)
    return False


# ---- modos rpc ----
async def iniciar_respondedor() -> None:
    """Respondedor simulado de la cola de peticiones (hace de chatbot/proyeccion)."""
    connection = await aio_pika.connect_robust(URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(COLA, durable=True)
    _RESPONDEDOR_VIVO.extend([connection, channel, queue])  # retencion, como el fix

    async def on_request(message) -> None:
        async with message.process():
            await channel.default_exchange.publish(
                Message(body=json.dumps({"status": "ok"}).encode(), content_type="application/json",
                        correlation_id=message.correlation_id),
                routing_key=message.reply_to)

    await queue.consume(on_request)


async def peticion_nueva_responde(timeout_total: int = ESPERA_MAX_S) -> tuple[bool, int]:
    """Llama al cliente RPC REAL; reintenta mientras el cliente se reconecta.
    Devuelve (respondio, intentos_fallidos)."""
    if MODO == "rpc-chatbot":
        import services.chatbot_rpc_client as cliente
        llamar = lambda: cliente.solicitar_chatbot({"mensaje": "hola"}, timeout=8)  # noqa: E731
    else:
        import services.proyeccion_rpc_client as cliente
        llamar = lambda: cliente.solicitar_proyeccion({"codigo_municipio": "68001"}, timeout=8)  # noqa: E731
    fallos, t0 = 0, time.time()
    while time.time() - t0 < timeout_total:
        try:
            respuesta = await llamar()
            return respuesta.get("status") == "ok", fallos
        except Exception:  # noqa: BLE001 -- aqui solo importa si el cliente se recupera
            fallos += 1
            await asyncio.sleep(2)
    return False, fallos


async def verificar() -> tuple[int | None, bool, int]:
    n = await asyncio.to_thread(consumers_de_la_cola)  # en rpc: consumer del respondedor simulado
    if n != 1:
        return n, False, 0
    if MODO == "persistencia":
        return n, await evento_se_persiste(), 0
    ok, fallos = await peticion_nueva_responde()
    return n, ok, fallos


async def main(escenarios: list[str], ciclos: int) -> int:
    await asyncio.to_thread(levantar_broker)
    conexion = None
    if MODO == "persistencia":
        conexion = await persistencia.iniciar_consumidor_persistencia(URL)  # codigo real
        gc.collect()  # fuerza el escenario que dispara el bug
    else:
        await iniciar_respondedor()

    resultados = []
    n, ok, fallos = await verificar()
    resultados.append(("inicial", n, ok, 0, fallos))
    for escenario in escenarios:
        for i in range(1, ciclos + 1):
            await asyncio.to_thread(accion, escenario)
            gc.collect()
            t0 = time.time()
            n = await asyncio.to_thread(consumers_de_la_cola)
            while n != 1 and time.time() - t0 < ESPERA_MAX_S:
                await asyncio.sleep(2)
                n = await asyncio.to_thread(consumers_de_la_cola)
            n, ok, fallos = await verificar()
            resultados.append((f"{escenario}{i}", n, ok, round(time.time() - t0), fallos))

    print(f"modo={MODO}")
    print(f"{'ciclo':<10} {'consumer_count':<15} {'procesado':<10} {'segundos':<9} intentos_fallidos")
    malos = 0
    for nombre, n, ok, seg, fallos in resultados:
        print(f"{nombre:<10} {str(n):<15} {str(ok):<10} {seg:<9} {fallos}")
        malos += 0 if (n == 1 and ok) else 1
    if conexion is not None:
        await conexion.close()
    docker("rm", "-f", CONTENEDOR)
    docker("network", "rm", RED)
    print("RESULTADO:", "OK" if malos == 0 else f"{malos} ciclo(s) fallaron")
    return 1 if malos else 0


if __name__ == "__main__":
    escenarios = [sys.argv[2]] if len(sys.argv) > 2 else ["recreate", "restart", "netcut"]
    ciclos = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    sys.exit(asyncio.run(main(escenarios, ciclos)))
