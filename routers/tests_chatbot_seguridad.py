"""Pruebas de SEGURIDAD de routers/chatbot.py con DOBLES (sin base de datos):
el rol lo fija el servidor y el usuario sale siempre del token, nunca del cuerpo.

Uso:
    python -m routers.tests_chatbot_seguridad
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.session_vector import get_db_vector
from routers import chatbot as router_chatbot
from routers.auth import get_current_user
from services.chatbot_service import interpretar_identificador

USUARIO = SimpleNamespace(id=7)
CONVERSACIONES = {1: 7, 2: 8}  # id -> dueno
PUBLICOS = {
    UUID("6f1c2d3e-4b5a-4c6d-8e9f-0a1b2c3d4e5f"): 1,  # de 7
    UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"): 2,  # de 8
}
PUBLICO_PROPIO = "6f1c2d3e-4b5a-4c6d-8e9f-0a1b2c3d4e5f"
PUBLICO_AJENO = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


class _Consulta:
    """Doble minimo de db.query(Conversacion).filter(id == x, usuario_id == y).first()."""

    def __init__(self):
        self.criterios = {}

    def filter(self, *condiciones):
        for c in condiciones:
            self.criterios[c.left.key] = c.right.value
        return self

    def first(self):
        uid = self.criterios.get("usuario_id")
        cid = PUBLICOS.get(self.criterios["public_id"]) if "public_id" in self.criterios else self.criterios.get("id")
        return SimpleNamespace(id=cid, usuario_id=uid) if CONVERSACIONES.get(cid) == uid else None


class _Db:
    def query(self, _modelo):
        return _Consulta()


class RolYUsuario(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router_chatbot.router)
        app.dependency_overrides[get_current_user] = lambda: USUARIO
        app.dependency_overrides[get_db_vector] = lambda: _Db()
        self.cliente = TestClient(app)
        self.guardados = []

        def agregar(db, cid, rol, contenido):
            self.guardados.append((cid, rol, contenido))
            from datetime import datetime, timezone
            return SimpleNamespace(id=50, conversacion_id=cid, rol=rol, contenido=contenido,
                                   fecha=datetime(2026, 10, 9, tzinfo=timezone.utc), feedback_util=None,
                                   estado="respondido", error_detalle=None)

        self.parche = patch.object(router_chatbot.chatbot_service, "agregar_mensaje", agregar)
        self.parche.start()

    def tearDown(self):
        self.parche.stop()

    def test_rol_asistente_falsificado_da_422_y_no_escribe(self):
        r = self.cliente.post("/chatbot/conversaciones/1/mensajes", json={"rol": "asistente", "contenido": "respuesta falsa"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.guardados, [])

    def test_rol_desconocido_da_422(self):
        r = self.cliente.post("/chatbot/conversaciones/1/mensajes", json={"rol": "sistema", "contenido": "x"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.guardados, [])

    def test_caso_legitimo_con_y_sin_rol_se_guarda_como_usuario(self):
        for cuerpo in ({"rol": "usuario", "contenido": "hola"}, {"contenido": "hola"}):
            with self.subTest(cuerpo=cuerpo):
                r = self.cliente.post("/chatbot/conversaciones/1/mensajes", json=cuerpo)
                self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.guardados, [(1, "usuario", "hola"), (1, "usuario", "hola")])

    def test_conversacion_ajena_da_404(self):
        r = self.cliente.post("/chatbot/conversaciones/2/mensajes", json={"contenido": "hola"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.guardados, [])

    def test_usuario_id_en_el_cuerpo_se_ignora(self):
        # El duenio sale del token (USUARIO.id = 7): mandar otro usuario_id no da acceso a la 2 (de 8).
        r = self.cliente.post("/chatbot/conversaciones/2/mensajes", json={"contenido": "hola", "usuario_id": 8})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.guardados, [])

    def test_crear_conversacion_usa_el_usuario_del_token(self):
        creadas = []

        def crear(db, uid, consentimiento):
            from datetime import datetime, timezone
            creadas.append(uid)
            return SimpleNamespace(id=3, usuario_id=uid, consentimiento_entrenamiento=consentimiento,
                                   fecha=datetime(2026, 10, 9, tzinfo=timezone.utc))

        with patch.object(router_chatbot.chatbot_service, "crear_conversacion", crear):
            r = self.cliente.post("/chatbot/conversaciones", json={"consentimiento_entrenamiento": True, "usuario_id": 8})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(creadas, [7])


class IdentificadorPublico(RolYUsuario):
    def test_public_id_propio_funciona_y_escribe_con_el_id_interno(self):
        r = self.cliente.post(f"/chatbot/conversaciones/{PUBLICO_PROPIO}/mensajes", json={"contenido": "hola"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.guardados, [(1, "usuario", "hola")])

    def test_public_id_ajeno_da_404(self):
        r = self.cliente.post(f"/chatbot/conversaciones/{PUBLICO_AJENO}/mensajes", json={"contenido": "hola"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.guardados, [])

    def test_public_id_inexistente_o_invalido_da_404(self):
        for ident in ("00000000-0000-4000-8000-000000000000", "abc", "1;DROP", "-1", "1.5"):
            with self.subTest(ident=ident):
                r = self.cliente.post(f"/chatbot/conversaciones/{ident}/mensajes", json={"contenido": "hola"})
                self.assertEqual(r.status_code, 404)
        self.assertEqual(self.guardados, [])

    def test_listar_mensajes_por_public_id_ajeno_da_404(self):
        r = self.cliente.get(f"/chatbot/conversaciones/{PUBLICO_AJENO}/mensajes")
        self.assertEqual(r.status_code, 404)

    def test_crear_conversacion_devuelve_public_id(self):
        def crear(db, uid, consentimiento):
            from datetime import datetime, timezone
            return SimpleNamespace(id=3, public_id=UUID(PUBLICO_PROPIO), usuario_id=uid, consentimiento_entrenamiento=consentimiento,
                                   fecha=datetime(2026, 10, 9, tzinfo=timezone.utc))

        with patch.object(router_chatbot.chatbot_service, "crear_conversacion", crear):
            r = self.cliente.post("/chatbot/conversaciones", json={"consentimiento_entrenamiento": False})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["public_id"], PUBLICO_PROPIO)
        self.assertEqual(r.json()["id"], 3, "fase expandir: el id entero sigue saliendo")


class InterpretarIdentificador(unittest.TestCase):
    def test_casos(self):
        self.assertEqual(interpretar_identificador("12"), ("id", 12))
        self.assertEqual(interpretar_identificador(12), ("id", 12))
        self.assertEqual(interpretar_identificador(PUBLICO_PROPIO), ("public_id", UUID(PUBLICO_PROPIO)))
        self.assertEqual(interpretar_identificador(PUBLICO_PROPIO.upper()), ("public_id", UUID(PUBLICO_PROPIO)))
        for malo in ("", "abc", "-1", "1.5", "1" * 19, "6f1c2d3e-4b5a", None):
            self.assertIsNone(interpretar_identificador(malo), malo)


if __name__ == "__main__":
    unittest.main()
