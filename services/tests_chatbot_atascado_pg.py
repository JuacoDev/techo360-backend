"""Mensajes ATASCADOS y el tope de mensajes en proceso, contra un PostgreSQL DESECHABLE.

Un mensaje que se queda en 'pendiente'/'procesando' (consumidor caido, respuesta que nunca
llega, backend muerto a mitad) no debe dejar al usuario sin poder escribir:
  - si el backend sigue vivo, el RPC se rinde a los 200 s (ChatbotTimeoutError -> 'error');
  - si el backend murio, nadie espera la fila: barrer_colgados la pasa a 'error' al pasar
    4 minutos sin cambios (MINUTOS_COLGADO), y se llama ANTES de contar la capacidad.

Se saltan solas si no hay PG_BIN / MIGRACION_0002_SQL.

Uso:
    PG_BIN=C:/ruta/pgsql/bin MIGRACION_0002_SQL=C:/ruta/0002_mensajes_estado.sql \
        python -m services.tests_chatbot_atascado_pg
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from services import chatbot_estados as est
# Base: una base desechable por prueba (conversaciones 1 y 2 del usuario 7, la 3 del usuario 8).
from services.tests_chatbot_estados_pg import Base, setUpModule, tearDownModule  # noqa: F401


class TestMensajeAtascado(Base):
    def setUp(self):
        super().setUp()
        from db.session import get_db
        from db.session_vector import get_db_vector
        from routers import chatbot as router_chatbot
        from routers.auth import get_current_user
        from services.chatbot_rpc_client import ChatbotTimeoutError

        self.ChatbotTimeoutError = ChatbotTimeoutError
        Sesion = sessionmaker(bind=self.engine, autoflush=False)

        def db_vector():
            sesion = Sesion()
            try:
                yield sesion
            finally:
                sesion.close()

        self.usuario = SimpleNamespace(id=7)
        app = FastAPI()
        app.include_router(router_chatbot.router)
        app.dependency_overrides[get_db_vector] = db_vector
        app.dependency_overrides[get_db] = lambda: SimpleNamespace(rollback=lambda: None)
        app.dependency_overrides[get_current_user] = lambda: self.usuario
        self.cliente = TestClient(app)
        self.rpc = []  # un comportamiento por llamada: una excepcion se lanza, otra cosa se devuelve

        async def rpc(payload):
            comportamiento = self.rpc.pop(0) if self.rpc else {"status": "ok", "response": "ok"}
            if isinstance(comportamiento, BaseException):
                raise comportamiento
            return comportamiento

        for p in (
            patch.object(router_chatbot, "solicitar_chatbot", rpc),
            patch.object(router_chatbot.settings, "chatbot_bloqueo_pendientes", True),
            patch.object(router_chatbot.settings, "chatbot_max_en_proceso_por_usuario", 2),
            patch.object(router_chatbot.settings, "chatbot_max_cola", 10),
        ):
            p.start()
            self.addCleanup(p.stop)

    # ---- ayudas ----
    def enviar(self, conversacion, texto="hola"):
        return self.cliente.post(f"/chatbot/conversaciones/{conversacion}/mensajes/enviar", json={"contenido": texto})

    def atascar(self, conversacion, estado, minutos):
        """Inserta un mensaje de usuario 'estado' cuyo ultimo cambio fue hace `minutos`."""
        with self.engine.begin() as con:
            con.execute(
                text(
                    "INSERT INTO mensajes (conversacion_id, rol, contenido, estado, actualizado_en) "
                    "VALUES (:c, 'usuario', 'atascado', :e, now() - make_interval(mins => :m))"
                ),
                {"c": conversacion, "e": estado, "m": minutos},
            )

    def estados(self, conversacion):
        return self.db.execute(
            text("SELECT estado FROM mensajes WHERE conversacion_id = :c AND rol = 'usuario' ORDER BY id"), {"c": conversacion}
        ).scalars().all()

    def envejecer(self, minutos, donde="true"):
        with self.engine.begin() as con:
            con.execute(text(f"UPDATE mensajes SET actualizado_en = now() - make_interval(mins => :m) WHERE {donde}"), {"m": minutos})

    def en_proceso(self):
        return self.db.execute(text("SELECT count(*) FROM mensajes WHERE estado IN ('pendiente', 'procesando')")).scalar()

    # ---- el RPC se rinde solo (consumidor caido / respuesta que nunca llega) ----
    def test_consumidor_caido_el_mensaje_pasa_a_error_y_no_ocupa_lugar(self):
        self.rpc += [self.ChatbotTimeoutError("Ningun worker respondio a tiempo."), self.ChatbotTimeoutError("otra vez")]
        self.assertEqual(self.enviar(1).status_code, 504)
        self.assertEqual(self.enviar(2).status_code, 504)
        self.assertEqual((self.estados(1), self.estados(2)), (["error"], ["error"]))
        self.assertEqual(self.en_proceso(), 0)
        est.verificar_capacidad(self.db, 7, 2, 10)  # dos fallos seguidos NO agotan el tope
        mid = self.db.execute(text("SELECT id FROM mensajes WHERE conversacion_id = 1 AND rol = 'usuario'")).scalar()
        self.assertEqual(self.cliente.post(f"/chatbot/mensajes/{mid}/reintentar").status_code, 200)  # y puede reintentar

    # ---- el backend murio a mitad: solo queda la fila, nadie espera ----
    def test_dos_atascados_de_5_minutos_no_dan_429_se_barren_y_el_usuario_puede_seguir(self):
        self.atascar(1, "procesando", 5)
        self.atascar(2, "pendiente", 5)
        r = self.enviar(1, "otra pregunta")
        self.assertNotEqual(r.status_code, 429)  # sin barrido habria sido 429
        self.assertEqual(r.status_code, 409)  # la conversacion 1 tiene ese mensaje, ya en 'error': hay que resolverlo
        self.assertEqual((self.estados(1), self.estados(2)), (["error"], ["error"]))  # el barrido toco las dos
        self.assertEqual(self.en_proceso(), 0)
        mid = self.db.execute(text("SELECT id FROM mensajes WHERE conversacion_id = 1")).scalar()
        self.assertEqual(self.cliente.delete(f"/chatbot/mensajes/{mid}").status_code, 204)  # lo elimina...
        self.assertEqual(self.enviar(1, "ahora si").status_code, 201)  # ...y escribe sin problema

    def test_reintentar_un_atascado_barrido_funciona(self):
        self.atascar(1, "procesando", 5)
        self.atascar(2, "procesando", 5)
        mid = self.db.execute(text("SELECT id FROM mensajes WHERE conversacion_id = 2")).scalar()
        self.assertEqual(self.cliente.post(f"/chatbot/mensajes/{mid}/reintentar").status_code, 200)
        self.assertEqual(self.estados(2), ["respondido"])

    # ---- limites exactos: 4 minutos ----
    def test_antes_de_4_minutos_sigue_contando_y_despues_ya_no(self):
        self.atascar(1, "procesando", 1)
        self.atascar(2, "procesando", 3)  # menos de 4 min: el asistente aun podria estar respondiendo
        est.barrer_colgados(self.db)
        with self.assertRaises(est.CapacidadAgotada):
            est.verificar_capacidad(self.db, 7, 2, 10)
        self.assertEqual((self.estados(1), self.estados(2)), (["procesando"], ["procesando"]))
        self.envejecer(5, "conversacion_id = 2")  # ahora el de la conversacion 2 lleva 5 min sin cambios
        est.barrer_colgados(self.db)
        est.verificar_capacidad(self.db, 7, 2, 10)  # queda 1 en proceso: hay lugar
        self.assertEqual((self.estados(1), self.estados(2)), (["procesando"], ["error"]))

    def test_http_dos_recientes_dan_429_y_pasados_5_minutos_ya_no(self):
        self.atascar(1, "procesando", 1)
        self.atascar(2, "procesando", 1)
        r = self.enviar(1)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["Retry-After"], "30")
        self.envejecer(5)
        self.assertNotEqual(self.enviar(1).status_code, 429)  # el barrido libero los lugares (aqui: 409 por tener un 'error')

    # ---- cola global llena de mensajes atascados de otros ----
    def test_cola_global_llena_de_atascados_se_libera_sola(self):
        with self.engine.begin() as con:
            for i in range(10):
                con.execute(
                    text("INSERT INTO conversaciones (id, usuario_id, consentimiento_entrenamiento) VALUES (:i, :u, false)"),
                    {"i": 100 + i, "u": 1000 + i},
                )
        for i in range(10):
            self.atascar(100 + i, "procesando", 1)
        self.assertEqual(self.enviar(1).status_code, 503)  # recientes: de verdad hay 10 esperando
        self.envejecer(6)
        self.assertEqual(self.enviar(1).status_code, 201)  # el barrido (global) los paso a 'error'


if __name__ == "__main__":
    unittest.main()
