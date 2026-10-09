"""Pruebas de los endpoints del chatbot con estados (fase C) con DOBLES: sin base de
datos ni RabbitMQ. La logica de estados contra Postgres de verdad esta en
services/tests_chatbot_estados_pg.py; aqui se prueba la ORQUESTACION del router:
interruptor, codigos HTTP, cuerpos de error, y que la ruta de siempre no cambia.

Uso:
    python -m routers.tests_chatbot_estados
"""
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import Settings
from db.session import get_db
from db.session_vector import get_db_vector
from routers import chatbot as router_chatbot
from routers.auth import get_current_user
from services import chatbot_estados as estados_real
from services.chatbot_rpc_client import ChatbotTimeoutError

USUARIO = SimpleNamespace(id=7)
CONVERSACION = SimpleNamespace(id=1, usuario_id=7, consentimiento_entrenamiento=False)


def _msg(mid, rol, estado, contenido="hola", detalle=None, duenio=7, intentos=0):
    """`duenio`: id del usuario dueño de la conversacion del mensaje."""
    return SimpleNamespace(
        id=mid, conversacion_id=1, rol=rol, contenido=contenido, feedback_util=None,
        fecha=datetime(2026, 10, 4, tzinfo=timezone.utc), estado=estado, error_detalle=detalle,
        duenio=duenio, intentos=intentos,
    )


class EstadosFalso:
    """Doble en memoria de services.chatbot_estados con la misma semantica (compare-and-set)."""

    SIN_RESOLVER = estados_real.SIN_RESOLVER
    DETALLE_TIMEOUT = estados_real.DETALLE_TIMEOUT
    DETALLE_COLGADO = estados_real.DETALLE_COLGADO
    PENDIENTE = estados_real.PENDIENTE
    PROCESANDO = estados_real.PROCESANDO
    ERROR = estados_real.ERROR
    RESPONDIDO = estados_real.RESPONDIDO
    CANCELADO = estados_real.CANCELADO
    ConversacionBloqueada = estados_real.ConversacionBloqueada

    def __init__(self):
        self.mensajes: dict[int, SimpleNamespace] = {}
        self.llamadas: list[str] = []
        self._siguiente = 100

    def poner(self, mensaje):
        self.mensajes[mensaje.id] = mensaje
        return mensaje

    def _nuevo_id(self):
        self._siguiente += 1
        return self._siguiente

    def _propio(self, m, uid):
        return m is not None and m.duenio == uid

    def barrer_colgados(self, db, minutos=4):
        self.llamadas.append("barrer_colgados")
        return 0

    def mensaje_sin_resolver(self, db, cid):
        return next((m for m in self.mensajes.values() if m.rol == "usuario" and m.estado in self.SIN_RESOLVER), None)

    def crear_pendiente(self, db, cid, contenido):
        self.llamadas.append("crear_pendiente")
        existente = self.mensaje_sin_resolver(db, cid)
        if existente:
            raise estados_real.ConversacionBloqueada(existente)
        return self.poner(_msg(self._nuevo_id(), "usuario", "pendiente", contenido))

    def _cambiar(self, mid, desde, hacia, detalle=None, uid=None, nuevo_intento=False, intento=None):
        m = self.mensajes.get(mid)
        if m is None or m.rol != "usuario" or m.estado not in desde:
            return None
        if uid is not None and not self._propio(m, uid):
            return None
        if intento is not None and m.intentos != intento:
            return None
        m.estado, m.error_detalle = hacia, detalle
        if nuevo_intento:
            m.intentos += 1
        return m

    def marcar_procesando(self, db, mid):
        return self._cambiar(mid, ("pendiente",), "procesando", nuevo_intento=True)

    def reintentar(self, db, mid, uid):
        self.llamadas.append("reintentar")
        return self._cambiar(mid, ("error", "cancelado"), "procesando", uid=uid, nuevo_intento=True)

    def cancelar(self, db, mid, uid):
        return self._cambiar(mid, ("pendiente", "procesando", "error"), "cancelado", uid=uid)

    def marcar_error(self, db, mid, detalle, intento=None):
        return self._cambiar(mid, ("pendiente", "procesando"), "error", detalle, intento=intento)

    def cerrar_respondido(self, db, mid, contenido, intento=None):
        usuario = self._cambiar(mid, ("procesando",), "respondido", intento=intento)
        if usuario is None:
            return None
        return usuario, self.poner(_msg(self._nuevo_id(), "asistente", "respondido", contenido, duenio=usuario.duenio))

    def obtener_de_usuario(self, db, mid, uid):
        m = self.mensajes.get(mid)
        return m if self._propio(m, uid) else None  # de otro usuario = como si no existiera

    def eliminar(self, db, mid, uid):
        m = self.mensajes.get(mid)
        if not self._propio(m, uid):
            return "no_encontrado"
        if m.rol == "usuario" and m.estado in ("pendiente", "error", "cancelado"):
            del self.mensajes[mid]
            return "eliminado"
        if m.rol == "usuario" and m.estado == "procesando":
            return "en_proceso"
        return "no_encontrado"


class DbFalsa:
    def __init__(self, nombre="vector", linea_de_tiempo=None):
        self.nombre = nombre
        self.linea = linea_de_tiempo if linea_de_tiempo is not None else []

    def rollback(self):
        self.linea.append(f"rollback:{self.nombre}")

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return CONVERSACION

    def all(self):
        return []

    def get(self, modelo, mid):
        return getattr(self, "_estados").mensajes.get(mid) if modelo.__name__ == "Mensaje" else CONVERSACION

    def refresh(self, obj):
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.estados = EstadosFalso()
        self.linea = []  # linea de tiempo: rollbacks de las dos sesiones y llamadas al RPC
        self.db = DbFalsa("vector", self.linea)
        self.db._estados = self.estados
        self.db_usuarios = DbFalsa("usuarios", self.linea)
        self.usuario_actual = USUARIO
        app = FastAPI()
        app.include_router(router_chatbot.router)
        app.dependency_overrides[get_db_vector] = lambda: self.db
        app.dependency_overrides[get_db] = lambda: self.db_usuarios
        app.dependency_overrides[get_current_user] = lambda: self.usuario_actual
        self.cliente = TestClient(app)
        self.respuesta_asistente = {"status": "ok", "response": "Respuesta del asistente."}
        self.llamadas_rpc = []

        async def rpc_falso(payload):
            self.linea.append("rpc")
            self.llamadas_rpc.append(payload)
            if isinstance(self.respuesta_asistente, BaseException):
                raise self.respuesta_asistente
            if callable(self.respuesta_asistente):
                return self.respuesta_asistente()
            return self.respuesta_asistente

        self.avisos = []  # avisos de cancelacion publicados: (mensaje_id, intento)

        async def aviso_falso(mensaje_id, intento):
            self.avisos.append((mensaje_id, intento))

        for p in (
            patch.object(router_chatbot, "chatbot_estados", self.estados),
            patch.object(router_chatbot, "solicitar_chatbot", rpc_falso),
            patch.object(router_chatbot, "avisar_cancelacion", aviso_falso),
        ):
            p.start()
            self.addCleanup(p.stop)

    def interruptor(self, valor):
        p = patch.object(router_chatbot.settings, "chatbot_bloqueo_pendientes", valor)
        p.start()
        self.addCleanup(p.stop)

    def enviar(self, texto="¿Qué es el déficit?"):
        return self.cliente.post("/chatbot/conversaciones/1/mensajes/enviar", json={"contenido": texto})


class TestInterruptor(Base):
    def test_por_defecto_esta_apagado(self):
        self.assertIs(Settings.model_fields["chatbot_bloqueo_pendientes"].default, False)

    def test_apagado_usa_la_ruta_de_siempre_sin_estados_ni_409(self):
        self.interruptor(False)
        guardados = []

        def agregar(db, cid, rol, contenido):
            guardados.append((rol, contenido))
            return _msg(len(guardados), rol, "respondido", contenido)

        with patch.object(router_chatbot.chatbot_service, "agregar_mensaje", agregar):
            r = self.enviar()
        self.assertEqual(r.status_code, 201)
        self.assertEqual([g[0] for g in guardados], ["usuario", "asistente"])
        self.assertNotIn("crear_pendiente", self.estados.llamadas)  # NO pasa por la maquina de estados
        self.assertEqual(r.json()["mensaje_usuario"]["estado"], "respondido")

    def test_apagado_un_fallo_sigue_siendo_un_error_con_detail_de_texto_como_siempre(self):
        self.interruptor(False)
        self.respuesta_asistente = ChatbotTimeoutError("sin worker")
        with patch.object(router_chatbot.chatbot_service, "agregar_mensaje", lambda db, c, rol, t: _msg(1, rol, "respondido", t)):
            r = self.enviar()
        self.assertEqual((r.status_code, r.json()["detail"]), (504, "sin worker"))
        self.assertNotIn("mensaje_usuario", r.json())  # contrato anterior intacto

    def test_apagado_no_hay_409_aunque_haya_un_mensaje_en_error(self):
        self.interruptor(False)
        self.estados.poner(_msg(1, "usuario", "error"))
        with patch.object(router_chatbot.chatbot_service, "agregar_mensaje", lambda db, c, rol, t: _msg(9, rol, "respondido", t)):
            self.assertEqual(self.enviar().status_code, 201)


class TestEnviarConEstados(Base):
    def setUp(self):
        super().setUp()
        self.interruptor(True)

    def test_exito_responde_201_y_deja_respondido(self):
        r = self.enviar()
        self.assertEqual(r.status_code, 201)
        cuerpo = r.json()
        self.assertEqual((cuerpo["mensaje_usuario"]["estado"], cuerpo["mensaje_asistente"]["contenido"]), ("respondido", "Respuesta del asistente."))
        self.assertEqual(self.llamadas_rpc[0]["mensaje"], "¿Qué es el déficit?")
        self.assertIn("barrer_colgados", self.estados.llamadas)

    def test_timeout_responde_504_con_detail_de_texto_y_el_mensaje_en_error(self):
        self.respuesta_asistente = ChatbotTimeoutError("Ningun worker respondio a tiempo.")
        r = self.enviar()
        self.assertEqual(r.status_code, 504)
        self.assertIsInstance(r.json()["detail"], str)  # un frontend viejo lo sigue mostrando
        self.assertEqual(r.json()["detail"], "Ningun worker respondio a tiempo.")
        self.assertEqual((r.json()["mensaje_usuario"]["estado"], r.json()["mensaje_usuario"]["error_detalle"]), ("error", estados_real.DETALLE_TIMEOUT))

    def test_asistente_no_disponible_503_y_respuesta_invalida_502(self):
        self.respuesta_asistente = {"status": "error", "tipo": "ollama_no_disponible", "detail": "Ollama caido"}
        r = self.enviar()
        self.assertEqual((r.status_code, r.json()["detail"], r.json()["mensaje_usuario"]["estado"]), (503, "Ollama caido", "error"))
        self.estados.mensajes.clear()
        self.respuesta_asistente = {"status": "error", "detail": "mal formada"}
        self.assertEqual(self.enviar().status_code, 502)

    def test_excepcion_inesperada_del_rpc_503_con_mensaje_legible(self):
        self.respuesta_asistente = ConnectionError("rabbit caido")
        r = self.enviar()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], "No se pudo contactar al asistente. Intenta de nuevo en unos segundos.")
        self.assertEqual(r.json()["mensaje_usuario"]["estado"], "error")

    def test_con_un_mensaje_sin_resolver_responde_409_y_no_guarda_otro(self):
        self.estados.poner(_msg(5, "usuario", "error", "pendiente de antes", "x"))
        r = self.enviar("otra")
        self.assertEqual(r.status_code, 409)
        self.assertIsInstance(r.json()["detail"], str)
        self.assertEqual((r.json()["mensaje_pendiente"]["id"], r.json()["mensaje_pendiente"]["estado"]), (5, "error"))
        self.assertEqual(len(self.estados.mensajes), 1)
        self.assertEqual(self.llamadas_rpc, [])  # ni siquiera se llamo al asistente

    def test_cancelado_mientras_el_asistente_pensaba_descarta_la_respuesta(self):
        def respuesta_y_cancelacion():
            m = next(iter(self.estados.mensajes.values()))
            self.estados.cancelar(None, m.id, 7)  # el usuario cancela mientras el RPC esta en vuelo
            return {"status": "ok", "response": "llego tarde"}

        self.respuesta_asistente = respuesta_y_cancelacion
        r = self.enviar()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["mensaje_usuario"]["estado"], "cancelado")
        self.assertEqual([m.rol for m in self.estados.mensajes.values()], ["usuario"])  # NO se guardo la respuesta

    def test_el_fallo_no_pisa_un_cancelado(self):
        def cancela_y_falla():
            self.estados.cancelar(None, next(iter(self.estados.mensajes.values())).id, 7)
            raise ChatbotTimeoutError("tarde")

        self.respuesta_asistente = cancela_y_falla
        r = self.enviar()
        self.assertEqual((r.status_code, r.json()["mensaje_usuario"]["estado"]), (409, "cancelado"))


class TestReintentar(Base):
    def test_desde_error_responde_200_con_el_intercambio_y_reusa_el_mismo_mensaje(self):
        self.estados.poner(_msg(5, "usuario", "error", "mi pregunta", "x"))
        r = self.cliente.post("/chatbot/mensajes/5/reintentar")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["mensaje_usuario"]["id"], r.json()["mensaje_usuario"]["estado"]), (5, "respondido"))
        self.assertEqual(self.llamadas_rpc[0]["mensaje"], "mi pregunta")  # reenvia el MISMO texto
        self.assertEqual(sum(1 for m in self.estados.mensajes.values() if m.rol == "usuario"), 1)  # no creo otro

    def test_funciona_aunque_el_interruptor_este_apagado(self):
        self.interruptor(False)
        self.estados.poner(_msg(5, "usuario", "error"))
        self.assertEqual(self.cliente.post("/chatbot/mensajes/5/reintentar").status_code, 200)

    def test_desde_cancelado(self):
        self.estados.poner(_msg(5, "usuario", "cancelado"))
        self.assertEqual(self.cliente.post("/chatbot/mensajes/5/reintentar").status_code, 200)

    def test_si_vuelve_a_fallar_responde_el_error_y_queda_en_error(self):
        self.estados.poner(_msg(5, "usuario", "error"))
        self.respuesta_asistente = ChatbotTimeoutError("otra vez")
        r = self.cliente.post("/chatbot/mensajes/5/reintentar")
        self.assertEqual((r.status_code, r.json()["mensaje_usuario"]["estado"]), (504, "error"))

    def test_un_mensaje_que_no_esta_en_error_ni_cancelado_da_409(self):
        for estado in ("respondido", "procesando", "pendiente"):
            self.estados.mensajes.clear()
            self.estados.poner(_msg(5, "usuario", estado))
            r = self.cliente.post("/chatbot/mensajes/5/reintentar")
            self.assertEqual(r.status_code, 409, estado)
            self.assertEqual(r.json()["mensaje_usuario"]["estado"], estado)

    def test_inexistente_o_del_asistente_da_404(self):
        self.assertEqual(self.cliente.post("/chatbot/mensajes/999/reintentar").status_code, 404)
        self.estados.poner(_msg(6, "asistente", "respondido"))
        self.assertEqual(self.cliente.post("/chatbot/mensajes/6/reintentar").status_code, 404)

    def test_dos_reintentos_seguidos_el_segundo_no_vuelve_a_llamar_al_asistente(self):
        self.estados.poner(_msg(5, "usuario", "error"))
        self.cliente.post("/chatbot/mensajes/5/reintentar")
        r2 = self.cliente.post("/chatbot/mensajes/5/reintentar")
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(len(self.llamadas_rpc), 1)


class TestAvisoDeCancelacion(Base):
    """Cancelar un mensaje en vuelo avisa al worker (opcional, mejor esfuerzo) para que deje de
    generar; la cancelacion en BD y el 409 del RPC descartado no cambian."""

    def test_el_payload_del_rpc_lleva_mensaje_e_intento_y_conserva_los_campos_de_siempre(self):
        self.interruptor(True)
        self.enviar()
        payload = self.llamadas_rpc[0]
        mensaje = next(iter(self.estados.mensajes.values()))
        self.assertEqual((payload["mensaje_id"], payload["intento"]), (mensaje.id, 1))
        for campo in ("mensaje", "usuario_id", "consentimiento_entrenamiento", "conversacion_id"):
            self.assertIn(campo, payload)  # un chatbot con imagen vieja solo lee estos

    def test_cancelar_en_vuelo_avisa_con_el_intento_vigente(self):
        self.estados.poner(_msg(5, "usuario", "procesando", intentos=2))
        r = self.cliente.post("/chatbot/mensajes/5/cancelar")
        self.assertEqual((r.status_code, r.json()["estado"]), (200, "cancelado"))
        self.assertEqual(self.avisos, [(5, 2)])

    def test_cancelar_y_reintentar_cambia_de_intento_y_el_aviso_apunta_al_viejo(self):
        self.interruptor(True)
        self.estados.poner(_msg(5, "usuario", "procesando", intentos=1))
        self.cliente.post("/chatbot/mensajes/5/cancelar")
        self.cliente.post("/chatbot/mensajes/5/reintentar")
        self.assertEqual(self.avisos, [(5, 1)])  # solo el intento 1 queda cancelado
        self.assertEqual((self.llamadas_rpc[0]["mensaje_id"], self.llamadas_rpc[0]["intento"]), (5, 2))  # el reintento es otro intento

    def test_sin_aviso_si_ya_estaba_cancelado_o_respondido_o_no_existe(self):
        self.estados.poner(_msg(5, "usuario", "cancelado"))
        self.cliente.post("/chatbot/mensajes/5/cancelar")
        self.estados.mensajes.clear()
        self.estados.poner(_msg(5, "usuario", "respondido"))
        self.cliente.post("/chatbot/mensajes/5/cancelar")
        self.cliente.post("/chatbot/mensajes/999/cancelar")
        self.assertEqual(self.avisos, [])

    def test_interruptor_apagado_no_avisa_pero_cancela(self):
        p = patch.object(router_chatbot.settings, "chatbot_avisar_cancelacion", False)
        p.start()
        self.addCleanup(p.stop)
        self.estados.poner(_msg(5, "usuario", "procesando", intentos=1))
        r = self.cliente.post("/chatbot/mensajes/5/cancelar")
        self.assertEqual(r.json()["estado"], "cancelado")
        self.assertEqual(self.avisos, [])

    def test_por_defecto_esta_encendido(self):
        self.assertIs(Settings.model_fields["chatbot_avisar_cancelacion"].default, True)


class TestPublicarAviso(unittest.TestCase):
    def test_publica_en_el_exchange_fanout_con_ttl_y_nunca_lanza(self):
        from services import chatbot_rpc_client as rpc

        publicados = []

        class Exchange:
            async def publish(self, mensaje, routing_key=""):
                publicados.append((mensaje, routing_key))

        class Canal:
            async def declare_exchange(self, nombre, tipo, durable=True):
                publicados.append(("exchange", nombre, tipo, durable))
                return Exchange()

            async def close(self):
                publicados.append("cerrado")

        class Conexion:
            async def channel(self):
                return Canal()

        async def conexion_falsa():
            return Conexion()

        with patch.object(rpc, "_get_connection", conexion_falsa):
            asyncio.run(rpc.avisar_cancelacion(5, 2))
        self.assertEqual(publicados[0][:2], ("exchange", "chatbot_cancelaciones"))
        self.assertFalse(publicados[0][3])  # no durable
        mensaje, clave = publicados[1]
        self.assertEqual(json.loads(mensaje.body), {"mensaje_id": 5, "intento": 2})
        self.assertEqual(clave, "")
        self.assertEqual(mensaje.expiration, rpc.TTL_AVISO_CANCELACION_S)
        self.assertEqual(publicados[-1], "cerrado")

        async def conexion_rota():
            raise ConnectionError("sin rabbitmq")

        with patch.object(rpc, "_get_connection", conexion_rota):
            asyncio.run(rpc.avisar_cancelacion(5, 2))  # no lanza


class TestCancelarYEliminar(Base):
    def test_cancelar_procesando_error_o_pendiente(self):
        for estado in ("procesando", "error", "pendiente"):
            self.estados.mensajes.clear()
            self.estados.poner(_msg(5, "usuario", estado))
            r = self.cliente.post("/chatbot/mensajes/5/cancelar")
            self.assertEqual((r.status_code, r.json()["estado"]), (200, "cancelado"), estado)

    def test_cancelar_es_idempotente_y_no_cancela_uno_respondido(self):
        self.estados.poner(_msg(5, "usuario", "cancelado"))
        self.assertEqual(self.cliente.post("/chatbot/mensajes/5/cancelar").json()["estado"], "cancelado")
        self.estados.mensajes.clear()
        self.estados.poner(_msg(5, "usuario", "respondido"))
        self.assertEqual(self.cliente.post("/chatbot/mensajes/5/cancelar").status_code, 409)

    def test_cancelar_inexistente_404(self):
        self.assertEqual(self.cliente.post("/chatbot/mensajes/999/cancelar").status_code, 404)

    def test_eliminar_error_cancelado_o_pendiente_da_204_sin_cuerpo(self):
        for estado in ("error", "cancelado", "pendiente"):
            self.estados.poner(_msg(5, "usuario", estado))
            r = self.cliente.delete("/chatbot/mensajes/5")
            self.assertEqual((r.status_code, r.content), (204, b""), estado)
            self.assertNotIn(5, self.estados.mensajes)

    def test_eliminar_procesando_da_409_pide_cancelar_antes(self):
        self.estados.poner(_msg(5, "usuario", "procesando"))
        r = self.cliente.delete("/chatbot/mensajes/5")
        self.assertEqual(r.status_code, 409)
        self.assertIn("cancélalo", r.json()["detail"])
        self.assertIn(5, self.estados.mensajes)

    def test_eliminar_uno_con_respuesta_da_409_y_inexistente_404(self):
        self.estados.poner(_msg(5, "usuario", "respondido"))
        self.assertEqual(self.cliente.delete("/chatbot/mensajes/5").status_code, 409)
        self.assertEqual(self.cliente.delete("/chatbot/mensajes/999").status_code, 404)


class TestAutorizacionConDosUsuarios(Base):
    """Reintentar, cancelar y eliminar de un mensaje AJENO responden 404 y no revelan que existe."""

    OTRO = SimpleNamespace(id=8)

    def setUp(self):
        super().setUp()
        self.estados.poner(_msg(5, "usuario", "error", "pregunta del usuario 7", duenio=7))
        self.usuario_actual = self.OTRO  # a partir de aqui las peticiones son del usuario 8

    def comparar_con_inexistente(self, metodo, ruta_ajena, ruta_inexistente):
        ajeno = getattr(self.cliente, metodo)(ruta_ajena)
        inexistente = getattr(self.cliente, metodo)(ruta_inexistente)
        self.assertEqual((ajeno.status_code, ajeno.content), (inexistente.status_code, inexistente.content))
        self.assertEqual(ajeno.status_code, 404)
        return ajeno

    def test_reintentar_ajeno_es_404_identico_a_inexistente_y_no_llama_al_asistente(self):
        self.comparar_con_inexistente("post", "/chatbot/mensajes/5/reintentar", "/chatbot/mensajes/999/reintentar")
        self.assertEqual(self.llamadas_rpc, [])
        self.assertEqual(self.estados.mensajes[5].estado, "error")

    def test_cancelar_ajeno_es_404_identico_a_inexistente(self):
        self.comparar_con_inexistente("post", "/chatbot/mensajes/5/cancelar", "/chatbot/mensajes/999/cancelar")
        self.assertEqual(self.estados.mensajes[5].estado, "error")

    def test_eliminar_ajeno_es_404_identico_a_inexistente_y_no_borra(self):
        self.comparar_con_inexistente("delete", "/chatbot/mensajes/5", "/chatbot/mensajes/999")
        self.assertIn(5, self.estados.mensajes)

    def test_el_404_ajeno_no_filtra_contenido_ni_estado(self):
        for metodo, ruta in (("post", "/chatbot/mensajes/5/reintentar"), ("post", "/chatbot/mensajes/5/cancelar"), ("delete", "/chatbot/mensajes/5")):
            cuerpo = getattr(self.cliente, metodo)(ruta).text
            self.assertNotIn("pregunta del usuario 7", cuerpo)
            self.assertNotIn("error", cuerpo.lower().replace("no encontrado", ""))

    def test_un_mensaje_ajeno_en_cualquier_estado_da_404_nunca_409(self):
        for estado in ("respondido", "procesando", "pendiente", "cancelado"):
            self.estados.mensajes.clear()
            self.estados.poner(_msg(5, "usuario", estado, duenio=7))
            for metodo, ruta in (("post", "/chatbot/mensajes/5/reintentar"), ("post", "/chatbot/mensajes/5/cancelar"), ("delete", "/chatbot/mensajes/5")):
                self.assertEqual(getattr(self.cliente, metodo)(ruta).status_code, 404, f"{estado} {ruta}")

    def test_el_dueño_si_puede(self):
        self.usuario_actual = USUARIO
        self.assertEqual(self.cliente.post("/chatbot/mensajes/5/cancelar").status_code, 200)


class TestSesionesYFallos(Base):
    def setUp(self):
        super().setUp()
        self.interruptor(True)

    def test_las_dos_sesiones_se_liberan_ANTES_del_rpc(self):
        self.enviar()
        self.assertIn("rpc", self.linea)
        antes_del_rpc = self.linea[: self.linea.index("rpc")]
        self.assertIn("rollback:vector", antes_del_rpc)
        self.assertIn("rollback:usuarios", antes_del_rpc)  # la de get_current_user tambien

    def test_reintentar_tambien_libera_las_dos_sesiones_antes_del_rpc(self):
        self.estados.poner(_msg(5, "usuario", "error"))
        self.cliente.post("/chatbot/mensajes/5/reintentar")
        antes_del_rpc = self.linea[: self.linea.index("rpc")]
        self.assertIn("rollback:vector", antes_del_rpc)
        self.assertIn("rollback:usuarios", antes_del_rpc)

    def test_respuesta_sin_el_campo_response_no_deja_el_mensaje_procesando(self):
        self.respuesta_asistente = {"status": "ok"}
        r = self.enviar()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["mensaje_usuario"]["estado"], "error")

    def test_respuesta_que_no_es_un_objeto_o_vacia_tampoco(self):
        for rara in ("texto suelto", None, {"status": "ok", "response": None}, {"status": "ok", "response": "   "}):
            self.estados.mensajes.clear()
            self.respuesta_asistente = rara
            r = self.enviar()
            self.assertEqual(r.status_code, 502, str(rara))
            self.assertEqual(r.json()["mensaje_usuario"]["estado"], "error", str(rara))
            self.assertIsInstance(r.json()["detail"], str)

    def test_fallo_de_la_bd_al_cerrar_deja_error_y_no_procesando(self):
        original = self.estados.cerrar_respondido

        def explota(*a, **k):
            raise RuntimeError("BD caida")

        self.estados.cerrar_respondido = explota
        try:
            r = self.enviar()
        finally:
            self.estados.cerrar_respondido = original
        self.assertEqual((r.status_code, r.json()["mensaje_usuario"]["estado"]), (502, "error"))

    def test_si_se_cancela_la_peticion_el_mensaje_no_queda_procesando(self):
        mensaje = self.estados.poner(_msg(5, "usuario", "procesando", intentos=1))
        self.respuesta_asistente = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(router_chatbot._procesar(self.db, self.db_usuarios, CONVERSACION, mensaje, USUARIO))
        self.assertEqual(self.estados.mensajes[5].estado, "error")

    def test_el_detalle_largo_del_worker_se_acota_en_el_servicio(self):
        self.assertEqual(estados_real.MAX_DETALLE, 500)


class TestCancelarYReintentarConRpcViejoVivo(Base):
    """Hallazgo 1: cancelar y reintentar con el RPC anterior aun en vuelo."""

    def setUp(self):
        super().setUp()
        self.interruptor(True)

    def _mensaje_en_proceso(self):
        return self.estados.poner(_msg(5, "usuario", "procesando", intentos=1))

    def test_el_error_tardio_del_rpc_viejo_no_pisa_el_reintento_en_curso(self):
        m = self._mensaje_en_proceso()
        self.estados.cancelar(None, 5, 7)
        self.estados.reintentar(None, 5, 7)  # intento 2 en vuelo
        r = router_chatbot._fallo(self.db, 5, 1, 504, "tarde", "tarde")  # el RPC del intento 1 falla ahora
        self.assertEqual((r.status_code, m.estado, m.intentos), (409, "procesando", 2))
        self.assertIn("más reciente", r.body.decode())

    def test_la_respuesta_tardia_del_rpc_viejo_se_descarta_y_el_reintento_cierra(self):
        m = self._mensaje_en_proceso()
        self.estados.cancelar(None, 5, 7)
        self.estados.reintentar(None, 5, 7)
        self.assertIsNone(self.estados.cerrar_respondido(None, 5, "respuesta vieja", 1))  # intento 1: descartada
        cerrado = self.estados.cerrar_respondido(None, 5, "respuesta nueva", 2)
        self.assertEqual((cerrado[0].estado, cerrado[1].contenido), ("respondido", "respuesta nueva"))
        self.assertEqual([x.contenido for x in self.estados.mensajes.values() if x.rol == "asistente"], ["respuesta nueva"])

    def test_el_409_dice_la_verdad_segun_el_estado_final(self):
        casos = {"cancelado": "cancelado", "error": "marcado con error", "respondido": "ya fue respondido", "procesando": "más reciente"}
        for estado, texto in casos.items():
            self.assertIn(texto, router_chatbot._texto_intento_descartado(SimpleNamespace(estado=estado)))
        self.assertIn("ya no existe", router_chatbot._texto_intento_descartado(None))


class TestListar(Base):
    def test_listar_barre_los_colgados_antes_de_devolver(self):
        r = self.cliente.get("/chatbot/conversaciones/1/mensajes")
        self.assertEqual(r.status_code, 200)
        self.assertIn("barrer_colgados", self.estados.llamadas)

    def test_los_mensajes_incluyen_estado_y_error_detalle_con_valores_por_defecto(self):
        modelo = router_chatbot.MensajeResponse.model_validate(
            SimpleNamespace(id=1, conversacion_id=1, rol="usuario", contenido="x", feedback_util=None, fecha=datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self.assertEqual((modelo.estado, modelo.error_detalle), ("respondido", None))  # backend/objeto anterior a la migracion


if __name__ == "__main__":
    unittest.main()
