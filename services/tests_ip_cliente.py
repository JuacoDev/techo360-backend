"""Pruebas de la IP del cliente en /otp/request (X-Real-IP, no el primer X-Forwarded-For).

Antes se usaba el PRIMER valor de X-Forwarded-For, que escribe el cliente: mandando una
cabecera distinta en cada peticion se saltaba el limite por IP. Redis real en memoria
(fakeredis) y el envio del correo simulado: no toca ningun servicio real.

Uso:
    python -m services.tests_ip_cliente
"""
import unittest
from unittest.mock import AsyncMock, patch

import fakeredis
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from routers import otp as router_otp
from services import otp_service as modulo_otp
from core.config import settings
from services.otp_service import IP_RATE_LIMIT_MAX, otp_service


def _peticion(cabeceras: dict, cliente=("172.18.0.5", 1234)) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in cabeceras.items()],
        "client": cliente,
    }
    return Request(scope)


class PruebasObtenerIp(unittest.TestCase):
    def test_usa_x_real_ip_aunque_el_cliente_falsifique_x_forwarded_for(self):
        r = _peticion({"X-Real-IP": "181.236.186.204", "X-Forwarded-For": "6.6.6.6, 181.236.186.204"})
        self.assertEqual(router_otp._obtener_ip_cliente(r), "181.236.186.204")

    def test_sin_x_real_ip_usa_el_ultimo_x_forwarded_for_no_el_primero(self):
        r = _peticion({"X-Forwarded-For": "6.6.6.6, 1.2.3.4, 181.236.186.204"})
        self.assertEqual(router_otp._obtener_ip_cliente(r), "181.236.186.204")

    def test_sin_cabeceras_usa_la_ip_de_la_conexion(self):
        self.assertEqual(router_otp._obtener_ip_cliente(_peticion({})), "172.18.0.5")

    def test_cabeceras_que_no_son_ip_se_ignoran(self):
        r = _peticion({"X-Real-IP": "no-es-ip", "X-Forwarded-For": "tampoco"})
        self.assertEqual(router_otp._obtener_ip_cliente(r), "172.18.0.5")

    def test_ipv6_valida(self):
        self.assertEqual(router_otp._obtener_ip_cliente(_peticion({"X-Real-IP": "2800:484:1::1"})), "2800:484:1::1")

    def test_sin_cliente_ni_cabeceras(self):
        self.assertEqual(router_otp._obtener_ip_cliente(_peticion({}, cliente=None)), "desconocida")


class PruebasLimitePorIp(unittest.TestCase):
    """El limite por IP de /otp/request ya no se salta cambiando X-Forwarded-For."""

    def setUp(self):
        redis = fake_aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        for parche in (
            patch.object(otp_service, "_redis", redis),
            patch.object(settings, "otp_ip_rate_limit_max", IP_RATE_LIMIT_MAX),  # no depender de OTP_IP_RATE_LIMIT_MAX del entorno
            patch.object(modulo_otp.email_service, "send_otp", AsyncMock()),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        app = FastAPI()
        app.include_router(router_otp.router)
        self.cliente = TestClient(app)

    def _pedir(self, n, real="181.236.186.204", falsa=None):
        cabeceras = {"X-Real-IP": real, "X-Forwarded-For": f"{falsa or f'10.0.0.{n}'}, {real}"}
        return self.cliente.post("/otp/request", json={"email": f"u{n}@ejemplo.co"}, headers=cabeceras)

    def test_cambiar_x_forwarded_for_en_cada_peticion_no_evita_el_limite(self):
        codigos = [self._pedir(n).status_code for n in range(IP_RATE_LIMIT_MAX + 3)]
        self.assertEqual(codigos[:IP_RATE_LIMIT_MAX], [200] * IP_RATE_LIMIT_MAX)
        self.assertEqual(codigos[IP_RATE_LIMIT_MAX:], [429] * 3)

    def test_otra_ip_real_tiene_su_propio_cupo(self):
        for n in range(IP_RATE_LIMIT_MAX + 1):
            self._pedir(n)
        self.assertEqual(self._pedir(100, real="190.1.2.3").status_code, 200)


if __name__ == "__main__":
    unittest.main()
