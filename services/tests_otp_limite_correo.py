"""Pruebas del tope de 5 codigos por hora por correo en /otp/request (ademas del cooldown de
60 s y del limite por IP) y de la normalizacion del correo en las claves de Redis.

Redis real en memoria (fakeredis) y el envio del correo simulado: no toca ningun servicio real.

Uso:
    python -m services.tests_otp_limite_correo
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import fakeredis
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import otp as router_otp
from services import otp_service as modulo_otp
from services.otp_service import EMAIL_RATE_LIMIT_MAX, otp_service

CORREO = "ana@ejemplo.co"


class PruebasLimitePorCorreo(unittest.TestCase):
    def setUp(self):
        self.redis = fake_aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        self.enviar = AsyncMock()
        for parche in (
            patch.object(otp_service, "_redis", self.redis),
            patch.object(modulo_otp.email_service, "send_otp", self.enviar),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        app = FastAPI()
        app.include_router(router_otp.router)
        self.cm = TestClient(app)
        self.cliente = self.cm.__enter__()
        self.addCleanup(self.cm.__exit__, None, None, None)
        self.n_ip = 0

    # ---- ayudas ----
    def _redis_call(self, fn):
        return self.cliente.portal.call(fn)

    def _sin_cooldown(self, correo=CORREO):
        async def borrar():
            await self.redis.delete(f"otp:{correo.strip().lower()}:cooldown")
        self._redis_call(borrar)

    def _pedir(self, correo=CORREO, quitar_cooldown=True):
        """Una peticion desde una IP distinta cada vez (para no tocar el limite por IP)."""
        if quitar_cooldown:
            self._sin_cooldown(correo)
        self.n_ip += 1
        return self.cliente.post(
            "/otp/request", json={"email": correo}, headers={"X-Real-IP": f"10.1.{self.n_ip // 250}.{self.n_ip % 250 + 1}"}
        )

    # ---- limite por correo ----
    def test_hasta_5_por_hora_y_la_sexta_es_429_con_mensaje_especifico(self):
        codigos = [self._pedir().status_code for _ in range(EMAIL_RATE_LIMIT_MAX)]
        self.assertEqual(codigos, [200] * EMAIL_RATE_LIMIT_MAX)
        r = self._pedir()
        self.assertEqual(r.status_code, 429)
        self.assertIn("Ya se solicitaron 5 codigos para este correo en la ultima hora", r.json()["detail"])
        self.assertIn("min.", r.json()["detail"])
        self.assertEqual(self.enviar.await_count, EMAIL_RATE_LIMIT_MAX)  # el 6.o NO se envio

    def test_el_cooldown_de_60s_sigue_aplicando_y_no_gasta_el_cupo_por_hora(self):
        self.assertEqual(self._pedir().status_code, 200)
        r = self._pedir(quitar_cooldown=False)
        self.assertEqual(r.status_code, 429)
        self.assertIn("Espera", r.json()["detail"])  # mensaje del cooldown, no el de la hora
        cuenta = self._redis_call(lambda: self.redis.get(f"otp:{CORREO}:hourly"))
        self.assertEqual(cuenta, "1")

    def test_variantes_de_mayusculas_y_espacios_comparten_el_mismo_contador(self):
        variantes = ["ana@ejemplo.co", "Ana@ejemplo.co", "ANA@ejemplo.co", "aNa@ejemplo.co", "ana@EJEMPLO.co"]
        for v in variantes:
            self.assertEqual(self._pedir(correo=v).status_code, 200, v)
        self.assertEqual(self._pedir(correo="ANa@ejemplo.co").status_code, 429)

    def test_otro_correo_tiene_su_propio_cupo(self):
        for _ in range(EMAIL_RATE_LIMIT_MAX):
            self._pedir()
        self.assertEqual(self._pedir().status_code, 429)
        self.assertEqual(self._pedir(correo="otra@ejemplo.co").status_code, 200)

    def test_al_vencer_la_ventana_se_puede_pedir_de_nuevo(self):
        for _ in range(EMAIL_RATE_LIMIT_MAX):
            self._pedir()
        self.assertEqual(self._pedir().status_code, 429)

        async def vencer():
            await self.redis.pexpire(f"otp:{CORREO}:hourly", 50)
            await asyncio.sleep(0.15)
        self._redis_call(vencer)
        self.assertEqual(self._pedir().status_code, 200)

    def test_una_clave_sin_ttl_no_bloquea_para_siempre(self):
        async def sembrar():
            await self.redis.set(f"otp:{CORREO}:hourly", "9")  # sin TTL (caida entre INCR y EXPIRE)
        self._redis_call(sembrar)
        self.assertEqual(self._pedir().status_code, 429)
        ttl = self._redis_call(lambda: self.redis.ttl(f"otp:{CORREO}:hourly"))
        self.assertGreater(ttl, 0)  # se le puso vencimiento

    # ---- normalizacion en el resto del flujo ----
    def test_verificar_con_otra_capitalizacion_encuentra_el_codigo(self):
        self.assertEqual(self._pedir(correo="Ana@ejemplo.co").status_code, 200)
        codigo = self._redis_call(lambda: self.redis.get(f"otp:{CORREO}:code"))
        self.assertIsNotNone(codigo)  # guardado con el correo normalizado
        r = self.cliente.post("/otp/verify", json={"email": "ANA@ejemplo.co", "code": codigo})
        self.assertEqual(r.status_code, 200, r.text)

    def test_un_codigo_fallido_cuenta_en_el_mismo_contador_de_intentos(self):
        self._pedir(correo="Ana@ejemplo.co")
        for _ in range(2):
            self.cliente.post("/otp/verify", json={"email": "ana@ejemplo.co", "code": "000000"})
        self.cliente.post("/otp/verify", json={"email": "ANA@ejemplo.co", "code": "000000"})
        intentos = self._redis_call(lambda: self.redis.get(f"otp:{CORREO}:attempts"))
        self.assertEqual(intentos, "3")


if __name__ == "__main__":
    unittest.main()
