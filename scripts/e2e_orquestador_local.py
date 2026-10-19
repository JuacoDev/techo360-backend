"""Extremo a extremo LOCAL del orquestador v2 (claude/fase4-especificacion-2.md, 4.9 punto 3).

Contenedores DESECHABLES de RabbitMQ y PostgreSQL (puertos aleatorios en 127.0.0.1, sin volumen; se borran por su
id al terminar), un stub HTTP que imita /api/chat y /api/tags de Ollama (SIN modelo real ni inferencia), el consumer
v2 REAL del chatbot (techo360-chatbot, importado en este proceso) y el backend real (router del chatbot con el
pipeline del orquestador). No toca produccion, no usa SSH.

Uso (desde techo360-backend, con techo360-chatbot al lado en la rama feat/consumer-v2):
    python scripts/e2e_orquestador_local.py

Casos: respuesta valida (con y sin maquina de estados); cifra inventada corregida en el reintento; cifra inventada
dos veces => respuesta determinista; margen de tiempo insuficiente => fallback sin reintento; homonimo => aclaracion
sin llegar a RabbitMQ; worker viejo (v1) recibe v2 => error visible; bandera apagada => camino v1 intacto;
aislamiento: otro usuario no puede leer ni escribir una conversacion ajena.
"""
import asyncio
import importlib.util
import json
import os
import random
import subprocess
import sys
import threading
import time
import types
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ_BACKEND = Path(__file__).resolve().parents[1]
RAIZ_CHATBOT = RAIZ_BACKEND.parent / "techo360-chatbot"
IMG_PG, IMG_MQ = "pgvector/pgvector:pg16", "rabbitmq:3.13-management-alpine"
contenedores: list[str] = []
resultados: list[tuple[str, bool, str]] = []


def sh(*args, entrada=None, check=True):
    r = subprocess.run(args, input=entrada, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"{args[:4]} -> {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def registrar(nombre, ok, detalle=""):
    resultados.append((nombre, bool(ok), detalle))
    print(("  OK   " if ok else "  FALLA ") + nombre + (f" -- {detalle}" if detalle else ""), flush=True)


# ----------------------------------------------------------------------------- infraestructura
def levantar():
    pg_port, mq_port = random.randint(20000, 40000), random.randint(40001, 60000)
    pg = sh("docker", "run", "-d", "--rm", "-e", "POSTGRES_PASSWORD=desechable", "-e", "POSTGRES_DB=e2e",
            "-p", f"127.0.0.1:{pg_port}:5432", IMG_PG)
    mq = sh("docker", "run", "-d", "--rm", "-p", f"127.0.0.1:{mq_port}:5672", IMG_MQ)
    contenedores.extend([pg, mq])
    print(f"contenedores: postgres {pg[:12]} :{pg_port} | rabbitmq {mq[:12]} :{mq_port}", flush=True)
    for _ in range(90):
        if subprocess.run(["docker", "exec", pg, "psql", "-U", "postgres", "-d", "e2e", "-tAc", "select 1"], capture_output=True).returncode == 0:
            break
        time.sleep(1)
    time.sleep(2)
    for _ in range(90):
        if subprocess.run(["docker", "exec", mq, "rabbitmq-diagnostics", "-q", "ping"], capture_output=True).returncode == 0:
            break
        time.sleep(2)
    esperar_amqp(mq_port)
    return pg, pg_port, mq_port


def esperar_amqp(puerto):
    """El ping del contenedor no basta: Docker Desktop tarda en abrir el puerto publicado. Se espera un handshake AMQP real."""
    import aio_pika

    async def intento():
        c = await aio_pika.connect(f"amqp://guest:guest@127.0.0.1:{puerto}/", timeout=5)
        await c.close()

    for _ in range(60):
        try:
            asyncio.run(intento())
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("RabbitMQ no acepta conexiones AMQP")


def sql(pg, texto):
    return sh("docker", "exec", "-i", pg, "psql", "-U", "postgres", "-d", "e2e", "-v", "ON_ERROR_STOP=1", "-q", "-A", "-t", entrada=texto)


def preparar_base(pg):
    for archivo in ("0001_conversaciones_mensajes.sql", "0002_mensajes_estado.sql", "0003_conversaciones_public_id.sql",
                    "0004_mensajes_metadatos_generacion.sql"):
        contenido = (RAIZ_CHATBOT / "migrations" / archivo).read_text(encoding="utf-8")
        if archivo.startswith("0002"):  # el backfill de 0002 espera los 6 mensajes historicos de produccion: aqui la base es nueva
            contenido = contenido.replace("IF n <> 6 THEN", "IF false THEN")
        sql(pg, contenido)
    sql(pg, """
        CREATE TABLE zonas_departamento (codigo_departamento varchar(2) PRIMARY KEY, nombre text NOT NULL);
        CREATE TABLE zonas_municipio (codigo_municipio varchar(6) PRIMARY KEY, codigo_departamento varchar(2) NOT NULL, nombre text NOT NULL);
        CREATE TABLE mapcalordb (id serial PRIMARY KEY, namevar text, valuevar double precision, yearm int, originvar text,
                                 codigodivipola text, intervaloconfianza double precision);
        INSERT INTO zonas_departamento VALUES ('68','Santander'),('05','Antioquia');
        INSERT INTO zonas_municipio VALUES ('68001','68','Bucaramanga'),('05615','05','Rionegro'),('68615','68','Rionegro'),('05001','05','Medellín');
        INSERT INTO mapcalordb (namevar, valuevar, yearm, originvar, codigodivipola)
            SELECT 'cobertura_acueducto', 80.0 + (a - 2019), a, 'TerriData', '68001' FROM generate_series(2019, 2024) a;
    """)


class StubOllama:
    """Imita /api/chat y /api/tags de Ollama. Sin modelo: devuelve el texto que el caso de prueba le encola."""

    def __init__(self):
        self.respuestas, self.peticiones, self.retardo = deque(), [], 0.0
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                cuerpo = json.dumps({"models": [{"name": "techo360-chatbot:v3.2", "digest": "sha256:stub0123"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_POST(self):
                datos = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.peticiones.append(datos)
                time.sleep(stub.retardo)
                texto = stub.respuestas.popleft() if stub.respuestas else "Respuesta por defecto sin cifras."
                cuerpo = json.dumps({"message": {"role": "assistant", "content": texto}, "done_reason": "stop",
                                     "prompt_eval_count": 321, "eval_count": 12, "total_duration": 2_000_000_000}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(cuerpo)

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.puerto = self.servidor.server_address[1]
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def reiniciar(self, *respuestas, retardo=0.0):
        self.respuestas, self.peticiones, self.retardo = deque(respuestas), [], retardo


def cargar_consumer_viejo():
    """El consumer v1 TAL COMO esta en origin/Testing (sin camino v2): el 'worker con imagen vieja'."""
    fuente = sh("git", "-C", str(RAIZ_CHATBOT), "show", "origin/Testing:app/rabbitmq_consumer.py")
    modulo = types.ModuleType("consumer_viejo")
    exec(compile(fuente, "consumer_viejo.py", "exec"), modulo.__dict__)
    return modulo


class Consumer:
    """Un consumer del chatbot corriendo en su propio hilo/loop."""

    def __init__(self, modulo, url):
        self.loop = asyncio.new_event_loop()
        self.conexion = None
        listo = threading.Event()

        def correr():
            asyncio.set_event_loop(self.loop)
            self.conexion = self.loop.run_until_complete(modulo.iniciar_consumidor(url))
            listo.set()
            self.loop.run_forever()

        self.hilo = threading.Thread(target=correr, daemon=True)
        self.hilo.start()
        if not listo.wait(60):
            raise RuntimeError("el consumer no arranco")

    def detener(self):
        asyncio.run_coroutine_threadsafe(self.conexion.close(), self.loop).result(15)
        self.loop.call_soon_threadsafe(self.loop.stop)


# --------------------------------------------------------------------------------------- casos
def main():
    pg, pg_port, mq_port = levantar()
    preparar_base(pg)
    stub = StubOllama()
    url_pg = f"postgresql://postgres:desechable@127.0.0.1:{pg_port}/e2e"
    url_mq = f"amqp://guest:guest@127.0.0.1:{mq_port}/"
    os.environ.update(DATABASE_URL=url_pg, DATABASE_VECTOR_URL=url_pg, RABBITMQ_URL=url_mq, JWT_SECRET_KEY="solo-e2e-local",
                      OLLAMA_URL=f"http://127.0.0.1:{stub.puerto}", OLLAMA_MODEL="techo360-chatbot:v3.2")
    sys.path.insert(0, str(RAIZ_BACKEND))
    sys.path.insert(0, str(RAIZ_CHATBOT))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.config import settings
    from db.session import SessionLocal, get_db
    from db.session_vector import SessionLocalVector, get_db_vector
    from routers import chatbot as rchatbot
    from routers.auth import get_current_user
    from services.orquestador import pipeline as pp

    usuario = types.SimpleNamespace(id=1)
    app = FastAPI()
    app.include_router(rchatbot.router)
    app.dependency_overrides[get_current_user] = lambda: usuario

    def _db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def _dbv():
        s = SessionLocalVector()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_db_vector] = _dbv

    import app.rabbitmq_consumer as consumer_nuevo  # el consumer v2 real del chatbot

    consumer = Consumer(consumer_nuevo, url_mq)
    api = TestClient(app)
    api.__enter__()  # un solo loop para todas las peticiones (la conexion RPC global vive en el)

    def conversacion(user=1):
        usuario.id = user
        r = api.post("/chatbot/conversaciones", json={"consentimiento_entrenamiento": False})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def enviar(conv, texto, user=1):
        usuario.id = user
        return api.post(f"/chatbot/conversaciones/{conv}/mensajes/enviar", json={"contenido": texto})

    def fila_asistente(conv):
        out = sql(pg, f"""SELECT row_to_json(t) FROM (SELECT contenido, modelo, modelo_digest, version_contrato, version_prompt, done_reason,
            tokens_prompt, tokens_respuesta, regeneraciones, validacion_cifras->>'estado' AS estado, array_to_string(contexto_ids, ',') AS ids,
            (contexto IS NOT NULL) AS tiene_contexto, grafica FROM mensajes WHERE conversacion_id = {conv} AND rol = 'asistente' ORDER BY id DESC LIMIT 1) t""")
        return json.loads(out) if out else None

    P = "¿Cuál fue la cobertura de acueducto en Bucaramanga en 2024?"
    BUENA = "En 2024 la cobertura de acueducto en Bucaramanga fue 85,0 %."
    print("\n== casos ==", flush=True)
    try:
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(settings, "chatbot_orquestador_activo", True):
            # 1. respuesta valida
            stub.reiniciar(BUENA)
            c = conversacion()
            r = enviar(c, P)
            f = fila_asistente(c)
            ok = (r.status_code == 201 and r.json()["mensaje_asistente"]["contenido"] == BUENA and f and f["modelo"] == "techo360-chatbot:v3.2"
                  and f["modelo_digest"] == "sha256:stub0123" and f["version_contrato"] == 2 and f["version_prompt"] == "sp-0.4.0-1"
                  and f["done_reason"] == "stop" and (f["tokens_prompt"], f["tokens_respuesta"]) == (321, 12) and f["regeneraciones"] == 0
                  and f["estado"] == "ok" and f["ids"] == "c1" and f["tiene_contexto"])
            enviado = stub.peticiones[0]["messages"] if stub.peticiones else []
            ok = ok and [m["role"] for m in enviado] == ["system", "user"] and "[c1] Cobertura de acueducto, Bucaramanga (Santander), 2024: 85,0 %." in enviado[1]["content"]
            registrar("1 respuesta valida (camino sin estados): v2 completo, metadatos guardados en mensajes", ok, f"status {r.status_code}")

            # 1b. igual con la maquina de estados
            with __import__("unittest.mock", fromlist=["patch"]).patch.object(settings, "chatbot_bloqueo_pendientes", True):
                stub.reiniciar(BUENA)
                c2 = conversacion()
                r = enviar(c2, P)
                f = fila_asistente(c2)
                ok = r.status_code == 201 and f and f["modelo_digest"] == "sha256:stub0123" and f["estado"] == "ok"
                registrar("1b respuesta valida (camino con estados): metadatos guardados en cerrar_respondido", ok, f"status {r.status_code}")

            # 2. cifra inventada corregida en el reintento
            stub.reiniciar("En 2024 la cobertura fue 99,9 %.", BUENA)
            c = conversacion()
            r = enviar(c, P)
            f = fila_asistente(c)
            ok = (r.status_code == 201 and r.json()["mensaje_asistente"]["contenido"] == BUENA and len(stub.peticiones) == 2
                  and f["regeneraciones"] == 1 and f["estado"] == "regenerada")
            registrar("2 cifra inventada se corrige en el reintento (intento=2, mismo contexto)", ok, f"peticiones al stub: {len(stub.peticiones)}")

            # 3. cifra inventada dos veces => determinista
            stub.reiniciar("Fue 99,9 %.", "Fue 97,1 %.")
            c = conversacion()
            r = enviar(c, P)
            f = fila_asistente(c)
            texto = r.json()["mensaje_asistente"]["contenido"] if r.status_code == 201 else ""
            ok = (r.status_code == 201 and texto.startswith(pp.NOTA_FALLBACK) and "85,0 %" in texto and "99,9" not in texto and "97,1" not in texto
                  and len(stub.peticiones) == 2 and f["regeneraciones"] == 1 and f["estado"] == "abstencion")
            registrar("3 cifra inventada dos veces => respuesta determinista con los datos verificados", ok, f"peticiones al stub: {len(stub.peticiones)}")

            # 4. margen de tiempo insuficiente => fallback sin reintento
            antes = pp.responder.__kwdefaults__["rpc_timeout"]
            pp.responder.__kwdefaults__["rpc_timeout"] = 3.5
            try:
                stub.reiniciar("Fue 99,9 %.", BUENA, retardo=2.0)
                c = conversacion()
                r = enviar(c, P)
                f = fila_asistente(c)
                texto = r.json()["mensaje_asistente"]["contenido"] if r.status_code == 201 else ""
                ok = r.status_code == 201 and texto.startswith(pp.NOTA_FALLBACK) and len(stub.peticiones) == 1 and f["regeneraciones"] == 0
            finally:
                pp.responder.__kwdefaults__["rpc_timeout"] = antes
            registrar("4 margen de tiempo insuficiente => fallback sin reintento", ok, f"peticiones al stub: {len(stub.peticiones)}")

            # 5. homonimo => aclaracion sin llegar a RabbitMQ
            llamadas = {"n": 0}
            original = pp.chatbot_rpc_client.solicitar_chatbot

            async def contado(*a, **k):
                llamadas["n"] += 1
                return await original(*a, **k)

            stub.reiniciar()
            with __import__("unittest.mock", fromlist=["patch"]).patch.object(pp.chatbot_rpc_client, "solicitar_chatbot", contado):
                c = conversacion()
                r = enviar(c, "¿Cuál es la cobertura de acueducto en Rionegro?")
            texto = r.json()["mensaje_asistente"]["contenido"] if r.status_code == 201 else ""
            ok = (r.status_code == 201 and "Hay varios municipios llamados Rionegro: Rionegro (Antioquia), Rionegro (Santander). ¿A cuál te refieres?" == texto
                  and llamadas["n"] == 0 and stub.peticiones == [])
            registrar("5 homonimo => aclaracion fija, 0 publicaciones en RabbitMQ y 0 llamadas al stub", ok, f"status {r.status_code}, llamadas {llamadas['n']}")

            # 6. worker viejo (v1) recibe v2 => error visible
            consumer.detener()
            viejo = Consumer(cargar_consumer_viejo(), url_mq)
            try:
                stub.reiniciar(BUENA)
                c = conversacion()
                r = enviar(c, P)
                ok = r.status_code in (502, 503) and stub.peticiones == [] and "fuera de contrato" in r.text
                registrar("6 worker viejo (v1) recibe v2 => error visible (no responde sin contexto)", ok, f"status {r.status_code}, {r.text[:110]}")
            finally:
                viejo.detener()
            consumer = Consumer(consumer_nuevo, url_mq)

        # 7. bandera apagada => camino v1 intacto
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(settings, "chatbot_orquestador_activo", False):
            stub.reiniciar("Respuesta v1 del stub.")
            c = conversacion()
            r = enviar(c, P)
            f = fila_asistente(c)
            enviado = stub.peticiones[0]["messages"] if stub.peticiones else []
            sistema_v1 = __import__("app.ollama_client", fromlist=["SYSTEM_PROMPT"]).SYSTEM_PROMPT
            ok = (r.status_code == 201 and r.json()["mensaje_asistente"]["contenido"] == "Respuesta v1 del stub."
                  and enviado[0]["content"] == sistema_v1 and enviado[1]["content"] == P and f["modelo"] is None and f["version_contrato"] is None)
            registrar("7 bandera apagada => camino v1 intacto (system constante, mensaje tal cual, sin metadatos)", ok, f"status {r.status_code}")

        # 8. aislamiento entre usuarios
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(settings, "chatbot_orquestador_activo", True):
            stub.reiniciar(BUENA, BUENA)
            ca, cb = conversacion(user=1), conversacion(user=2)
            enviar(ca, P, user=1)
            enviar(cb, P, user=2)
            m1, m2 = stub.peticiones[0]["messages"], stub.peticiones[1]["messages"]
            sin_datos_de_usuario = all(("usuario" not in m["content"].lower().split("pregunta del usuario")[0]) for m in m1[1:2] + m2[1:2])
            ajena_leer = api.get(f"/chatbot/conversaciones/{ca}/mensajes")
            usuario.id = 2
            ajena_leer = api.get(f"/chatbot/conversaciones/{ca}/mensajes")
            ajena_escribir = enviar(ca, "hola", user=2)
            ajena_post = api.post(f"/chatbot/conversaciones/{ca}/mensajes", json={"contenido": "x"})
            ok = (m1 == m2 and sin_datos_de_usuario and ajena_leer.status_code == 404 and ajena_escribir.status_code == 404 and ajena_post.status_code == 404)
            registrar("8 aislamiento: mismo contexto publico para dos usuarios, sin datos de usuario; conversacion ajena => 404 (leer y escribir)", ok,
                      f"leer {ajena_leer.status_code}, enviar {ajena_escribir.status_code}, agregar {ajena_post.status_code}")
    finally:
        try:
            api.__exit__(None, None, None)
            consumer.detener()
        except Exception:
            pass
        for cid in contenedores:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        print(f"\ncontenedores borrados: {[c[:12] for c in contenedores]}", flush=True)
    fallos = [r for r in resultados if not r[1]]
    print(f"\nRESULTADO: {len(resultados) - len(fallos)}/{len(resultados)} casos OK", flush=True)
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
