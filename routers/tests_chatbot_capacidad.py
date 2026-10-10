"""Pruebas de la admision de mensajes al asistente (tope por usuario y cola global) con
DOBLES: sin base de datos ni RabbitMQ. El conteo contra Postgres de verdad esta en
services/tests_chatbot_capacidad_pg.py.

Uso:
    python -m routers.tests_chatbot_capacidad
"""
import unittest
from unittest.mock import patch

from core.config import Settings
from routers import chatbot as router_chatbot
from routers.tests_chatbot_estados import Base, _msg


def _en_proceso(estados, mid, estado="procesando", duenio=7):
    return estados.poner(_msg(mid, "usuario", estado, duenio=duenio))


class BaseCapacidad(Base):
    def setUp(self):
        super().setUp()
        self.interruptor(True)

    def limites(self, por_usuario=2, cola=10):
        for nombre, valor in (("chatbot_max_en_proceso_por_usuario", por_usuario), ("chatbot_max_cola", cola)):
            p = patch.object(router_chatbot.settings, nombre, valor)
            p.start()
            self.addCleanup(p.stop)


class TestPorDefecto(unittest.TestCase):
    def test_valores_por_defecto(self):
        self.assertEqual(Settings.model_fields["chatbot_max_en_proceso_por_usuario"].default, 2)
        self.assertEqual(Settings.model_fields["chatbot_max_cola"].default, 10)


class TestTopePorUsuario(BaseCapacidad):
    def test_con_dos_en_proceso_el_tercero_es_429_y_no_llega_al_asistente(self):
        self.limites()
        _en_proceso(self.estados, 1, "procesando")
        _en_proceso(self.estados, 2, "pendiente")
        r = self.enviar()
        self.assertEqual(r.status_code, 429)
        self.assertIn("Ya tienes 2 mensajes en proceso", r.json()["detail"])
        self.assertEqual(r.headers["Retry-After"], "30")
        self.assertIsNone(r.json()["mensaje_usuario"])
        self.assertNotIn("crear_pendiente", self.estados.llamadas)  # no se guardo nada
        self.assertEqual(self.llamadas_rpc, [])

    def test_con_uno_en_proceso_todavia_se_admite(self):
        self.limites()
        _en_proceso(self.estados, 1, "procesando")
        # el del doble bloquea la conversacion (409), pero pasa el control de capacidad: no es 429
        self.assertNotEqual(self.enviar().status_code, 429)

    def test_sin_mensajes_en_proceso_responde_201(self):
        self.limites()
        self.assertEqual(self.enviar().status_code, 201)

    def test_error_cancelado_y_respondido_no_cuentan(self):
        self.limites()
        for i, estado in enumerate(("error", "cancelado", "respondido", "error"), start=1):
            _en_proceso(self.estados, i, estado)
        # no hay 429 (si habra 409 por tener mensajes sin resolver en el doble)
        self.assertNotEqual(self.enviar().status_code, 429)

    def test_los_mensajes_de_otro_usuario_no_cuentan_para_el_tope_por_usuario(self):
        self.limites(por_usuario=2, cola=100)
        _en_proceso(self.estados, 1, "procesando", duenio=99)
        _en_proceso(self.estados, 2, "procesando", duenio=99)
        _en_proceso(self.estados, 3, "pendiente", duenio=99)
        self.assertNotEqual(self.enviar().status_code, 429)

    def test_cero_desactiva_el_tope_por_usuario(self):
        self.limites(por_usuario=0, cola=0)
        for i in range(1, 6):
            _en_proceso(self.estados, i, "procesando")
        self.assertNotEqual(self.enviar().status_code, 429)

    def test_con_el_interruptor_apagado_no_aplica(self):
        """Sin estados (ruta de siempre) no hay forma de contar mensajes en proceso."""
        self.limites()
        self.interruptor(False)
        _en_proceso(self.estados, 1, "procesando")
        _en_proceso(self.estados, 2, "procesando")
        with patch.object(router_chatbot.chatbot_service, "agregar_mensaje", lambda db, c, rol, t: _msg(9, rol, "respondido", t)):
            self.assertEqual(self.enviar().status_code, 201)
        self.assertNotIn("verificar_capacidad", self.estados.llamadas)


class TestColaGlobal(BaseCapacidad):
    def test_cola_llena_503_con_mensaje_y_retry_after(self):
        self.limites(por_usuario=2, cola=10)
        for i in range(1, 11):
            _en_proceso(self.estados, i, "procesando", duenio=100 + i)
        r = self.enviar()
        self.assertEqual(r.status_code, 503)
        self.assertIn("atendiendo muchas solicitudes", r.json()["detail"])
        self.assertEqual(r.headers["Retry-After"], "60")
        self.assertIsNone(r.json()["mensaje_usuario"])
        self.assertNotIn("crear_pendiente", self.estados.llamadas)

    def test_con_9_en_cola_todavia_hay_lugar(self):
        self.limites(por_usuario=2, cola=10)
        for i in range(1, 10):
            _en_proceso(self.estados, i, "procesando", duenio=100 + i)
        self.assertNotIn(self.enviar().status_code, (429, 503))

    def test_el_tope_por_usuario_se_avisa_antes_que_el_de_cola(self):
        self.limites(por_usuario=2, cola=3)
        _en_proceso(self.estados, 1, "procesando")
        _en_proceso(self.estados, 2, "procesando")
        _en_proceso(self.estados, 3, "procesando", duenio=100)
        self.assertEqual(self.enviar().status_code, 429)


class TestReintentar(BaseCapacidad):
    def test_reintentar_con_el_tope_alcanzado_es_429_y_el_mensaje_sigue_en_error(self):
        self.limites()
        _en_proceso(self.estados, 1, "procesando")
        _en_proceso(self.estados, 2, "procesando")
        _en_proceso(self.estados, 3, "error")
        r = self.cliente.post("/chatbot/mensajes/3/reintentar")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.estados.mensajes[3].estado, "error")
        self.assertNotIn("reintentar", self.estados.llamadas)
        self.assertEqual(self.llamadas_rpc, [])

    def test_reintentar_con_lugar_funciona(self):
        self.limites()
        _en_proceso(self.estados, 3, "error")
        r = self.cliente.post("/chatbot/mensajes/3/reintentar")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.estados.mensajes[3].estado, "respondido")


if __name__ == "__main__":
    unittest.main()
