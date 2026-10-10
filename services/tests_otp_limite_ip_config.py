"""Limite de /otp/request por IP configurable (OTP_IP_RATE_LIMIT_MAX, por defecto 10) y tope diario por IP
(OTP_IP_DAILY_MAX, por defecto 150).

- Un valor vacio, no numerico o <= 0 cae SIEMPRE al valor por defecto: nunca desactiva el limite.
- Con el tope en 40 (demo con registro en vivo) el limite por CORREO (5/hora) y el cooldown siguen activos.
- El tope diario no lo gastan las solicitudes que el limite de 15 min ya rechazo.

Redis real en memoria (fakeredis) y envio de correo simulado: no toca ningun servicio real.

Uso:
    python -m services.tests_otp_limite_ip_config
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

import fakeredis
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import (
    OTP_IP_DAILY_MAX_MAXIMO,
    OTP_IP_RATE_LIMIT_MAX_MAXIMO,
    Settings,
    entero_positivo_o_defecto,
    settings,
)
from routers import otp as router_otp
from services import otp_service as modulo_otp
from services.otp_service import (
    EMAIL_RATE_LIMIT_MAX,
    IP_DAILY_LIMIT_MAX,
    IP_DAILY_WINDOW_SECONDS,
    IP_RATE_LIMIT_MAX,
    otp_service,
)

IP = "181.236.186.204"


def _ajustes(**entorno) -> Settings:
    """Settings leidos de un entorno controlado (sin .env ni OTP_* heredados)."""
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith("OTP_IP_")}
    base.update({"BREVO_API_KEY": "x", "JWT_SECRET_KEY": "x"})
    base.update({k.upper(): v for k, v in entorno.items()})
    with patch.dict(os.environ, base, clear=True):
        return Settings(_env_file=None)


class PruebasValoresPorDefectoYEntorno(unittest.TestCase):
    def test_los_valores_por_defecto_son_10_por_15_min_y_150_por_dia(self):
        s = _ajustes()
        self.assertEqual((s.otp_ip_rate_limit_max, s.otp_ip_daily_max), (10, 150))
        self.assertEqual((IP_RATE_LIMIT_MAX, IP_DAILY_LIMIT_MAX, IP_DAILY_WINDOW_SECONDS), (10, 150, 86400))

    def test_la_variable_en_40_se_respeta(self):
        self.assertEqual(_ajustes(otp_ip_rate_limit_max="40").otp_ip_rate_limit_max, 40)
        self.assertEqual(_ajustes(otp_ip_rate_limit_max=" 40 ").otp_ip_rate_limit_max, 40)

    def test_valor_vacio_no_numerico_cero_o_negativo_cae_a_10(self):
        for malo in ("", "   ", "abc", "40x", "4.5", "0", "-5", "-1", "nan", "true"):
            with self.subTest(valor=malo):
                self.assertEqual(_ajustes(otp_ip_rate_limit_max=malo).otp_ip_rate_limit_max, 10)

    def test_el_tope_diario_tambien_cae_a_su_defecto(self):
        self.assertEqual(_ajustes(otp_ip_daily_max="300").otp_ip_daily_max, 300)
        for malo in ("", "abc", "0", "-10", "1.5"):
            with self.subTest(valor=malo):
                self.assertEqual(_ajustes(otp_ip_daily_max=malo).otp_ip_daily_max, 150)

    def test_funcion_auxiliar(self):
        self.assertEqual(entero_positivo_o_defecto(None, 7), 7)
        self.assertEqual(entero_positivo_o_defecto(True, 7), 7)
        self.assertEqual(entero_positivo_o_defecto(12, 7), 12)
        self.assertEqual(entero_positivo_o_defecto("12", 7), 12)

    def test_maximos_100_por_15_min_y_500_por_dia(self):
        self.assertEqual((OTP_IP_RATE_LIMIT_MAX_MAXIMO, OTP_IP_DAILY_MAX_MAXIMO), (100, 500))

    def test_el_limite_de_15_min_acepta_hasta_100_y_lo_que_pasa_de_100_cae_al_defecto_no_al_maximo(self):
        for ok_ in ("1", "40", "99", "100", " 100 "):
            with self.subTest(valor=ok_):
                self.assertEqual(_ajustes(otp_ip_rate_limit_max=ok_).otp_ip_rate_limit_max, int(ok_))
        for mayor in ("101", "150", "1000", "999999", "4000", "100000000000000000000"):
            with self.subTest(valor=mayor):
                s = _ajustes(otp_ip_rate_limit_max=mayor)
                self.assertEqual(s.otp_ip_rate_limit_max, 10)       # el defecto
                self.assertNotEqual(s.otp_ip_rate_limit_max, 100)   # no el maximo

    def test_el_tope_diario_acepta_hasta_500_y_lo_que_pasa_de_500_cae_al_defecto_no_al_maximo(self):
        for ok_ in ("1", "150", "499", "500", " 500 "):
            with self.subTest(valor=ok_):
                self.assertEqual(_ajustes(otp_ip_daily_max=ok_).otp_ip_daily_max, int(ok_))
        for mayor in ("501", "600", "5000", "999999"):
            with self.subTest(valor=mayor):
                s = _ajustes(otp_ip_daily_max=mayor)
                self.assertEqual(s.otp_ip_daily_max, 150)           # el defecto
                self.assertNotEqual(s.otp_ip_daily_max, 500)        # no el maximo

    def test_invalido_en_cualquiera_de_las_dos_cae_al_defecto(self):
        for malo in ("", "abc", "0", "-1", "4.5", "1e2"):
            with self.subTest(valor=malo):
                s = _ajustes(otp_ip_rate_limit_max=malo, otp_ip_daily_max=malo)
                self.assertEqual((s.otp_ip_rate_limit_max, s.otp_ip_daily_max), (10, 150))

    def test_el_maximo_aplica_solo_si_se_pasa_a_la_funcion(self):
        self.assertEqual(entero_positivo_o_defecto("1000", 7), 1000)           # sin maximo: se respeta
        self.assertEqual(entero_positivo_o_defecto("1000", 7, 100), 7)         # con maximo: defecto
        self.assertEqual(entero_positivo_o_defecto("100", 7, 100), 100)        # el propio maximo es valido

    def test_las_dos_variables_son_independientes(self):
        s = _ajustes(otp_ip_rate_limit_max="40", otp_ip_daily_max="abc")
        self.assertEqual((s.otp_ip_rate_limit_max, s.otp_ip_daily_max), (40, 150))


class BaseHttp(unittest.TestCase):
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

    def limites(self, ip_max=10, diario=150):
        for nombre, valor in (("otp_ip_rate_limit_max", ip_max), ("otp_ip_daily_max", diario)):
            p = patch.object(settings, nombre, valor)
            p.start()
            self.addCleanup(p.stop)

    def pedir(self, n, ip=IP, correo=None):
        return self.cliente.post("/otp/request", json={"email": correo or f"u{n}@ejemplo.co"}, headers={"X-Real-IP": ip})

    def redis_call(self, fn):
        return self.cliente.portal.call(fn)


class PruebasLimiteDeIpConfigurable(BaseHttp):
    def test_defecto_10_las_primeras_10_pasan_y_la_11_es_429(self):
        self.limites(ip_max=_ajustes().otp_ip_rate_limit_max)
        codigos = [self.pedir(n).status_code for n in range(12)]
        self.assertEqual(codigos, [200] * 10 + [429, 429])

    def test_variable_en_40_las_40_pasan_y_la_41_es_429(self):
        self.limites(ip_max=_ajustes(otp_ip_rate_limit_max="40").otp_ip_rate_limit_max)
        codigos = [self.pedir(n).status_code for n in range(42)]
        self.assertEqual(codigos[:40], [200] * 40)
        self.assertEqual(codigos[40:], [429, 429])
        r = self.pedir(99)
        self.assertIn("Demasiadas solicitudes de OTP desde esta IP", r.json()["detail"])

    def test_variable_en_el_maximo_100_las_100_pasan_y_la_101_es_429(self):
        self.limites(ip_max=_ajustes(otp_ip_rate_limit_max="100").otp_ip_rate_limit_max, diario=500)
        codigos = [self.pedir(n).status_code for n in range(102)]
        self.assertEqual(codigos[:100], [200] * 100)
        self.assertEqual(codigos[100:], [429, 429])

    def test_variable_por_encima_del_maximo_cae_a_10_no_a_100(self):
        for demasiado in ("101", "4000"):
            with self.subTest(valor=demasiado):
                self.redis_call(self.redis.flushall)
                with patch.object(settings, "otp_ip_rate_limit_max", _ajustes(otp_ip_rate_limit_max=demasiado).otp_ip_rate_limit_max):
                    codigos = [self.pedir(n).status_code for n in range(12)]
                self.assertEqual(codigos, [200] * 10 + [429, 429], demasiado)

    def test_valor_invalido_en_el_entorno_cae_a_10_nunca_desactiva_el_limite(self):
        for malo in ("", "abc", "0", "-3"):
            with self.subTest(valor=malo):
                self.redis_call(self.redis.flushall)
                with patch.object(settings, "otp_ip_rate_limit_max", _ajustes(otp_ip_rate_limit_max=malo).otp_ip_rate_limit_max):
                    codigos = [self.pedir(n).status_code for n in range(12)]
                self.assertEqual(codigos, [200] * 10 + [429, 429], malo)

    def test_otra_ip_tiene_su_propio_cupo_con_el_tope_en_40(self):
        self.limites(ip_max=40)
        for n in range(41):
            self.pedir(n)
        self.assertEqual(self.pedir(100, ip="190.1.2.3").status_code, 200)

    def test_con_el_tope_de_ip_en_40_el_limite_de_5_por_hora_por_correo_sigue_activo(self):
        self.limites(ip_max=40)
        codigos = []
        for _ in range(EMAIL_RATE_LIMIT_MAX + 1):
            self.redis_call(lambda: self.redis.delete("otp:ana@ejemplo.co:cooldown"))
            codigos.append(self.pedir(0, correo="ana@ejemplo.co"))
        self.assertEqual([c.status_code for c in codigos], [200] * EMAIL_RATE_LIMIT_MAX + [429])
        self.assertIn("Ya se solicitaron 5 codigos para este correo", codigos[-1].json()["detail"])  # el del correo, no el de la IP

    def test_con_el_tope_de_ip_en_40_el_cooldown_de_60_s_sigue_activo(self):
        self.limites(ip_max=40)
        self.assertEqual(self.pedir(0, correo="ana@ejemplo.co").status_code, 200)
        r = self.pedir(0, correo="ana@ejemplo.co")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Espera", r.json()["detail"])


class PruebasTopeDiarioPorIp(BaseHttp):
    def test_el_tope_diario_se_aplica_aunque_el_de_15_min_no_se_alcance(self):
        self.limites(ip_max=40, diario=3)
        codigos = [self.pedir(n) for n in range(5)]
        self.assertEqual([c.status_code for c in codigos], [200, 200, 200, 429, 429])
        self.assertIn("limite diario", codigos[3].json()["detail"])
        self.assertRegex(codigos[3].json()["detail"], r"en \d+ h\.")

    def test_otra_ip_no_se_ve_afectada_por_el_tope_diario_de_la_primera(self):
        self.limites(ip_max=40, diario=2)
        for n in range(3):
            self.pedir(n)
        self.assertEqual(self.pedir(10, ip="190.1.2.3").status_code, 200)

    def test_las_solicitudes_rechazadas_por_el_limite_de_15_min_no_gastan_el_tope_diario(self):
        self.limites(ip_max=2, diario=5)
        codigos = [self.pedir(n).status_code for n in range(6)]
        self.assertEqual(codigos, [200, 200, 429, 429, 429, 429])
        self.assertEqual(self.redis_call(lambda: self.redis.get(f"otp:ip_daily:{IP}")), "2")

    def test_el_contador_diario_vence_a_las_24_horas(self):
        self.limites(ip_max=40, diario=150)
        self.pedir(0)
        ttl = self.redis_call(lambda: self.redis.ttl(f"otp:ip_daily:{IP}"))
        self.assertTrue(86300 < ttl <= 86400, ttl)

    def test_una_clave_diaria_sin_ttl_no_bloquea_para_siempre(self):
        self.limites(ip_max=40, diario=5)

        async def sembrar():
            await self.redis.set(f"otp:ip_daily:{IP}", "9")  # sin TTL (caida entre INCR y EXPIRE)
        self.redis_call(sembrar)
        self.assertEqual(self.pedir(0).status_code, 429)
        self.assertGreater(self.redis_call(lambda: self.redis.ttl(f"otp:ip_daily:{IP}")), 0)

    def test_al_vencer_el_dia_se_puede_pedir_de_nuevo(self):
        self.limites(ip_max=40, diario=2)
        for n in range(3):
            self.pedir(n)
        self.assertEqual(self.pedir(3).status_code, 429)

        async def vencer():
            import asyncio
            await self.redis.pexpire(f"otp:ip_daily:{IP}", 50)
            await asyncio.sleep(0.15)
        self.redis_call(vencer)
        self.assertEqual(self.pedir(4).status_code, 200)

    def test_tope_diario_por_encima_de_500_cae_al_defecto_150(self):
        self.limites(ip_max=10000, diario=_ajustes(otp_ip_daily_max="501").otp_ip_daily_max)  # ventana de 15 min holgada: aqui solo importa el tope diario
        codigos = [self.pedir(n).status_code for n in range(152)]
        self.assertEqual(codigos[:150], [200] * 150)
        self.assertEqual(codigos[150:], [429, 429])   # 150, no 500

    def test_el_defecto_de_150_por_dia_no_molesta_con_las_40_de_una_demo(self):
        self.limites(ip_max=40, diario=_ajustes().otp_ip_daily_max)
        codigos = [self.pedir(n).status_code for n in range(40)]
        self.assertEqual(codigos, [200] * 40)


if __name__ == "__main__":
    unittest.main()
