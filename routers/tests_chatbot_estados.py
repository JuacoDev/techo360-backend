"""Pruebas de los endpoints del chatbot con estados (fase C) con DOBLES: sin base de
datos ni RabbitMQ. La logica de estados contra Postgres de verdad esta en
services/tests_chatbot_estados_pg.py; aqui se prueba la ORQUESTACION del router:
interruptor, codigos HTTP, cuerpos de error, y que la ruta de siempre no cambia.

Uso:
    python -m routers.tests_chatbot_estados
"""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import Settings
from db.session_vector import get_db_vector
from routers import chatbot as router_chatbot
from routers.auth import get_current_user
from services import chatbot_estados as estados_real
from services.chatbot_rpc_client import ChatbotTimeoutError

USUARIO = SimpleNamespace(id=7)
CONVERSACION = SimpleNamespace(id=1, usuario_id=7, consentimiento_entrenamiento=False)


def _msg(mid, rol, estado, contenido="hola", detalle=None):
    return SimpleNamespace(
        id=mid, conversacion_id=1, rol=rol, contenido=contenido, feedback_util=None,
        fecha=datetime(2026, 10, 4, tzinfo=timezone.utc), estado=estado, error_detalle=detalle,
    )


class EstadosFalso:
    """Doble en memoria de services.chatbot_estados con la misma semantica (compare-and-set)."""

    SIN_RESOLVER = estados_real.SIN_RESOLVER
    DETALLE_TIMEOUT = estados_real.DETALLE_TIMEOUT
    DETALLE_COLGADO = estados_real.DETALLE_COLGADO
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

    def _cambiar(self, mid, desde, hacia, detalle=None, usuario_id=None):
        m = self.mensajes.get(mid)
        if m is None or m.rol != "usuario" or m.estado not in desde:
            return None
        m.estado, m.error_detalle = hacia, detalle
        return m

    def marcar_procesando(self, db, mid):
        return self._cambiar(mid, ("pendiente",), "procesando")

    def reintentar(self, db, mid, uid):
        self.llamadas.append("reintentar")
        return self._cambiar(mid, ("error", "cancelado"), "procesando")

    def cancelar(self, db, mid, uid):
        return self._cambiar(mid, ("pendiente", "procesando", "error"), "cancelado")

    def marcar_error(self, db, mid, detalle):
        return self._cambiar(mid, ("pendiente", "procesando"), "error", detalle)

    def cerrar_respondido(self, db, mid, contenido):
        usuario = self._cambiar(mid, ("procesando",), "respondido")
        if usuario is None:
            return None
        return usuario, self.poner(_msg(self._nuevo_id(), "asistente", "respondido", contenido))

    def obtener_de_usuario(self, db, mid, uid):
        return self.mensajes.get(mid)

    def eliminar(self, db, mid, uid):
        m = self.mensajes.get(mid)
        if m is not None and m.rol == "usuario" and m.estado in ("pendiente", "error", "cancelado"):
            del self.mensajes[mid]
            return "eliminado"
        if m is not None and m.rol == "usuario" and m.estado == "procesando":
            return "en_proceso"
        return "no_encontrado"


class DbFalsa:
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
        self.db = DbFalsa()
        self.db._estados = self.estados
        app = FastAPI()
        app.include_router(router_chatbot.router)
        app.dependency_overrides[get_db_vector] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: USUARIO
        self.cliente = TestClient(app)
        self.respuesta_asistente = {"status": "ok", "response": "Respuesta del asistente."}
        self.llamadas_rpc = []

        async def rpc_falso(payload):
            self.llamadas_rpc.append(payload)
            if isinstance(self.respuesta_asistente, Exception):
                raise self.respuesta_asistente
            if callable(self.respuesta_asistente):
                return self.respuesta_asistente()
            return self.respuesta_asistente

        for p in (
            patch.object(router_chatbot, "chatbot_estados", self.estados),
            patch.object(router_chatbot, "solicitar_chatbot", rpc_falso),
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
