"""Pruebas de la marca "correo verificado": /otp/verify la deja en Redis y /auth/register la
exige y la consume (SEGURIDAD: el backend no confiaba en que el frontend hubiera verificado
el OTP antes de registrar). Redis real en memoria (fakeredis, solo para pruebas) y dobles
para la base de datos y el correo: no toca ningun servicio real.

Uso:
    python -m services.tests_otp_verificado
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.session import get_db
from routers import auth as router_auth
from routers import otp as router_otp
from services.otp_service import OTPNotVerifiedError, OTPVerificationUsedError, otp_service

CORREO = "ana@ejemplo.co"
CODIGO = "123456"


class BaseFalsa:
    """Doble minimo de la Session: sin usuarios previos salvo que se pasen en `existentes`."""

    def __init__(self, existentes=()):
        self.existentes = list(existentes)
        self.agregados = []

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def first(self):
        return self.existentes[0] if self.existentes else None

    def add(self, usuario):
        self.agregados.append(usuario)

    def commit(self):
        pass

    def refresh(self, usuario):
        usuario.id = 99


class PruebasMarcaVerificado(unittest.TestCase):
    def setUp(self):
        servidor = fakeredis.FakeServer()
        self.redis = fake_aioredis.FakeRedis(server=servidor, decode_responses=True)
        parche = patch.object(otp_service, "_redis", self.redis)
        parche.start()
        self.addCleanup(parche.stop)
        self.db = BaseFalsa()
        app = FastAPI()
        app.include_router(router_otp.router)
        app.include_router(router_auth.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.cliente_cm = TestClient(app)
        self.cliente = self.cliente_cm.__enter__()
        self.addCleanup(self.cliente_cm.__exit__, None, None, None)

    # ---- ayudas ----
    def _poner_codigo(self, correo=CORREO, codigo=CODIGO):
        async def poner():
            await self.redis.set(f"otp:{correo}:code", codigo, ex=600)
        self.cliente.portal.call(poner)

    def _verificar(self, correo=CORREO, codigo=CODIGO):
        return self.cliente.post("/otp/verify", json={"email": correo, "code": codigo})

    def _registrar(self, correo=CORREO, nombre="ana"):
        return self.cliente.post(
            "/auth/register", json={"correo": correo, "namevar": nombre, "password": "clave-larga-1"}
        )

    def _existe(self, clave):
        async def existe():
            return await self.redis.exists(clave)
        return bool(self.cliente.portal.call(existe))

    # ---- casos pedidos ----
    def test_sin_marca_el_registro_es_rechazado_con_mensaje_especifico(self):
        r = self._registrar()
        self.assertEqual(r.status_code, 403)
        self.assertIn("no esta verificado o la verificacion expiro", r.json()["detail"])
        self.assertEqual(self.db.agregados, [])

    def test_con_marca_el_registro_funciona_y_consume_la_marca(self):
        self._poner_codigo()
        self.assertEqual(self._verificar().status_code, 200)
        self.assertTrue(self._existe(f"otp:{CORREO}:verified"))
        r = self._registrar()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["id"], 99)
        self.assertEqual(len(self.db.agregados), 1)
        self.assertFalse(self._existe(f"otp:{CORREO}:verified"))

    def test_marca_consumida_un_segundo_registro_es_rechazado_como_ya_usada(self):
        self._poner_codigo()
        self._verificar()
        self.assertEqual(self._registrar().status_code, 201)
        r = self._registrar(nombre="ana2")
        self.assertEqual(r.status_code, 403)
        self.assertIn("ya se uso", r.json()["detail"])
        self.assertEqual(len(self.db.agregados), 1)

    def test_marca_expirada_el_registro_es_rechazado(self):
        self._poner_codigo()
        self._verificar()

        async def acortar():
            await self.redis.pexpire(f"otp:{CORREO}:verified", 50)
            await asyncio.sleep(0.15)
        self.cliente.portal.call(acortar)
        r = self._registrar()
        self.assertEqual(r.status_code, 403)
        self.assertIn("expiro", r.json()["detail"])
        self.assertEqual(self.db.agregados, [])

    # ---- casos de borde ----
    def test_codigo_incorrecto_no_deja_marca(self):
        self._poner_codigo()
        self.assertEqual(self._verificar(codigo="000000").status_code, 400)
        self.assertFalse(self._existe(f"otp:{CORREO}:verified"))
        self.assertEqual(self._registrar().status_code, 403)

    def test_la_marca_es_por_correo(self):
        self._poner_codigo()
        self._verificar()
        self.assertEqual(self._registrar(correo="otra@ejemplo.co", nombre="otra").status_code, 403)
        self.assertEqual(self._registrar().status_code, 201)

    def test_mayusculas_y_espacios_no_evitan_ni_rompen_la_marca(self):
        self._poner_codigo(correo="ana@ejemplo.co")  # las claves de Redis usan el correo normalizado
        self.assertEqual(self._verificar(correo="Ana@Ejemplo.co").status_code, 200)
        self.assertEqual(self._registrar(correo="ana@ejemplo.co").status_code, 201)

    def test_un_correo_o_nombre_ya_tomado_no_gasta_la_marca(self):
        self._poner_codigo()
        self._verificar()
        self.db.existentes = [SimpleNamespace(id=1)]
        r = self._registrar()
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self._existe(f"otp:{CORREO}:verified"))
        self.db.existentes = []
        self.assertEqual(self._registrar(nombre="ana-nueva").status_code, 201)

    def test_reset_password_no_deja_marca_de_registro(self):
        """/auth/reset-password usa otp_service.verify_otp: no debe habilitar registros."""
        self._poner_codigo()
        self.cliente.portal.call(lambda: otp_service.verify_otp(CORREO, CODIGO))
        self.assertFalse(self._existe(f"otp:{CORREO}:verified"))
        self.assertEqual(self._registrar().status_code, 403)

    def test_sin_marca_no_se_revela_si_el_correo_ya_existe(self):
        self.db.existentes = [SimpleNamespace(id=1)]
        r = self._registrar()
        self.assertEqual(r.status_code, 403)  # no 400 "ya esta registrado"

    def test_dos_registros_simultaneos_con_una_marca_solo_uno_pasa(self):
        async def carrera():
            await otp_service.marcar_verificado(CORREO)
            return await asyncio.gather(
                otp_service.consumir_verificado(CORREO),
                otp_service.consumir_verificado(CORREO),
                return_exceptions=True,
            )
        resultados = self.cliente.portal.call(carrera)
        exitos = [x for x in resultados if x is None]
        errores = [x for x in resultados if isinstance(x, (OTPNotVerifiedError, OTPVerificationUsedError))]
        self.assertEqual((len(exitos), len(errores)), (1, 1), resultados)

    def test_pedir_otro_codigo_y_verificarlo_de_nuevo_rehabilita_el_registro(self):
        self._poner_codigo()
        self._verificar()
        self.assertEqual(self._registrar().status_code, 201)
        self._poner_codigo()
        self._verificar()
        self.assertEqual(self._registrar(nombre="ana3").status_code, 201)


if __name__ == "__main__":
    unittest.main()
