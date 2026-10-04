"""Verificacion de la duracion del token de acceso (D5): 8 h, con una
constante unica usada por routers/auth.py y services/auth_service.py, y el
login existente intacto. unittest (stdlib); sin base de datos real.

Uso:
    python -m services.tests_sesion_token
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import config
from core.config import ACCESS_TOKEN_EXPIRE_HOURS, settings
from routers import auth as router_auth
from services import auth_service

OCHO_HORAS = timedelta(hours=8)


def _decodificar(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])


class TestDuracionDelToken(unittest.TestCase):
    def test_la_constante_es_de_8_horas(self):
        self.assertEqual(ACCESS_TOKEN_EXPIRE_HOURS, 8)

    def test_token_de_routers_auth_dura_8_horas(self):
        payload = _decodificar(router_auth.crear_access_token(1, "a@b.co", "ana"))
        self.assertEqual(payload["exp"] - payload["iat"], int(OCHO_HORAS.total_seconds()))

    def test_token_de_auth_service_dura_8_horas(self):
        payload = _decodificar(auth_service.crear_access_token(1, "a@b.co", "ana"))
        self.assertEqual(payload["exp"] - payload["iat"], int(OCHO_HORAS.total_seconds()))

    def test_ambos_archivos_usan_la_misma_constante(self):
        # Si alguien cambia el valor en core/config.py, ambos cambian: no hay
        # ningun timedelta(hours=<literal>) propio en esos archivos.
        with patch.object(router_auth, "ACCESS_TOKEN_EXPIRE_HOURS", 3), patch.object(
            auth_service, "ACCESS_TOKEN_EXPIRE_HOURS", 3
        ):
            for crear in (router_auth.crear_access_token, auth_service.crear_access_token):
                payload = _decodificar(crear(1, "a@b.co", "ana"))
                self.assertEqual(payload["exp"] - payload["iat"], 3 * 3600)
        self.assertIs(router_auth.ACCESS_TOKEN_EXPIRE_HOURS, config.ACCESS_TOKEN_EXPIRE_HOURS)
        self.assertIs(auth_service.ACCESS_TOKEN_EXPIRE_HOURS, config.ACCESS_TOKEN_EXPIRE_HOURS)

    def test_el_token_expira_a_las_8_horas_desde_ahora(self):
        antes = datetime.now(timezone.utc)
        exp = datetime.fromtimestamp(
            _decodificar(router_auth.crear_access_token(1, "a@b.co", "ana"))["exp"], timezone.utc
        )
        self.assertAlmostEqual((exp - antes).total_seconds(), OCHO_HORAS.total_seconds(), delta=5)


class _FakeQuery:
    def __init__(self, usuario):
        self._usuario = usuario

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._usuario


class _FakeDb:
    def __init__(self, usuario):
        self._usuario = usuario

    def query(self, *a, **k):
        return _FakeQuery(self._usuario)


class TestLoginYValidacion(unittest.TestCase):
    def setUp(self):
        self.usuario = SimpleNamespace(id=7, correo="ana@upb.edu.co", namevar="ana")
        app = FastAPI()
        app.include_router(router_auth.router)
        app.dependency_overrides[router_auth.get_db] = lambda: _FakeDb(self.usuario)
        self.cliente = TestClient(app)

    def test_login_existente_sigue_funcionando_y_devuelve_token_de_8_h(self):
        with patch.object(router_auth, "autenticar_usuario", return_value=self.usuario):
            r = self.cliente.post("/auth/login", json={"identifier": "ana", "password": "x"})
        self.assertEqual(r.status_code, 200)
        cuerpo = r.json()
        self.assertEqual(cuerpo["token_type"], "bearer")
        payload = _decodificar(cuerpo["access_token"])
        self.assertEqual(payload["sub"], "7")
        self.assertEqual(payload["exp"] - payload["iat"], 8 * 3600)

    def test_login_con_credenciales_malas_sigue_dando_401(self):
        with patch.object(router_auth, "autenticar_usuario", return_value=None):
            r = self.cliente.post("/auth/login", json={"identifier": "ana", "password": "mala"})
        self.assertEqual(r.status_code, 401)

    def _token(self, emitido_hace: timedelta, dura: timedelta) -> str:
        iat = datetime.now(timezone.utc) - emitido_hace
        return jwt.encode(
            {"sub": "7", "iat": iat, "exp": iat + dura}, settings.jwt_secret_key, algorithm="HS256"
        )

    def test_un_token_nuevo_sigue_valido_a_las_7_horas(self):
        token = self._token(timedelta(hours=7), OCHO_HORAS)
        r = self.cliente.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)

    def test_un_token_nuevo_expira_pasadas_las_8_horas(self):
        token = self._token(timedelta(hours=8, minutes=1), OCHO_HORAS)
        r = self.cliente.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["detail"], "Token expirado")

    def test_un_token_viejo_de_1_hora_conserva_su_caducidad_original(self):
        # Tokens ya emitidos antes de D5: siguen siendo de 1 h.
        vigente = self._token(timedelta(minutes=30), timedelta(hours=1))
        vencido = self._token(timedelta(hours=2), timedelta(hours=1))
        ok = self.cliente.get("/auth/me", headers={"Authorization": f"Bearer {vigente}"})
        caducado = self.cliente.get("/auth/me", headers={"Authorization": f"Bearer {vencido}"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(caducado.status_code, 401)


if __name__ == "__main__":
    unittest.main()
