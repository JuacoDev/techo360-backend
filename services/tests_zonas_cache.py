"""Verificacion de services/zonas_cache.py y de GET /api/zonas con cache (D6).
unittest (stdlib), igual que services/tests_score_privacion.py. Usa un Redis
SIMULADO en memoria: no toca Redis ni la base reales.

Uso:
    python -m services.tests_zonas_cache
"""
import gzip
import inspect
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services import zonas_cache as modulo
from services.zonas_cache import ZonasCache, resolver, serializar


class RedisFalso:
    """Lo minimo de redis.Redis que usa ZonasCache."""

    def __init__(self):
        self.datos: dict[str, bytes] = {}
        self.vencimientos: dict[str, int] = {}
        self.caido = False
        self.escrituras = 0
        self._cerrojo = threading.Lock()

    def _revisar(self):
        if self.caido:
            raise redis.ConnectionError("Redis caido (simulado)")

    @staticmethod
    def _bytes(valor):
        return valor if isinstance(valor, bytes) else str(valor).encode()

    def get(self, clave):
        self._revisar()
        return self.datos.get(clave)

    def mget(self, *claves):
        self._revisar()
        return [self.datos.get(c) for c in claves]

    def set(self, clave, valor, nx=False, ex=None):
        self._revisar()
        with self._cerrojo:
            if nx and clave in self.datos:
                return None
            self.datos[clave] = self._bytes(valor)
            self.vencimientos[clave] = ex
            self.escrituras += 1
            return True

    def delete(self, *claves):
        self._revisar()
        with self._cerrojo:
            return sum(1 for c in claves if self.datos.pop(c, None) is not None)

    def scan_iter(self, match=None, count=None):
        self._revisar()
        prefijo = match.rstrip("*")
        return iter([c for c in list(self.datos) if c.startswith(prefijo)])

    def pipeline(self, transaction=True):
        return _PipelineFalso(self)


class _PipelineFalso:
    def __init__(self, redis_falso):
        self._r = redis_falso
        self._ops = []

    def set(self, clave, valor, ex=None):
        self._ops.append((clave, valor, ex))

    def execute(self):
        self._r._revisar()
        for clave, valor, ex in self._ops:
            self._r.set(clave, valor, ex=ex)


# Por defecto las pruebas usan el doble en memoria de arriba. Con
# TECHO360_TEST_REDIS=fakeredis se repiten contra fakeredis (un Redis simulado de
# verdad, solo para pruebas: NO es dependencia de produccion; ver
# services/tests_redis_fakeredis.py para como instalarlo en un entorno aparte).
BACKEND_REDIS = "doble en memoria"
if os.environ.get("TECHO360_TEST_REDIS") == "fakeredis":
    from services.tests_redis_fakeredis import RedisFalso  # noqa: F811

    BACKEND_REDIS = "fakeredis"

CONTENIDO = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"nombre": "Ñandú"}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.redis = RedisFalso()
        self.cache = ZonasCache(self.redis)
        self.llamadas = 0

    def construir(self):
        self.llamadas += 1
        return CONTENIDO


class TestHitMiss(Base):
    def test_miss_calcula_y_hit_no_recalcula(self):
        r1 = resolver(self.cache, "municipio", 2024, self.construir)
        r2 = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(self.llamadas, 1)
        self.assertEqual(r1.status, 200)
        self.assertEqual(r1.encabezados["ETag"], r2.encabezados["ETag"])

    def test_claves_versionadas(self):
        resolver(self.cache, "departamento", 2024, self.construir)
        self.assertIn("zonas:v1:departamento:2024", self.redis.datos)
        self.assertIn("zonas:v1:departamento:2024:etag", self.redis.datos)

    def test_ttl_en_cuerpo_y_etag_y_candado_liberado(self):
        resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(self.redis.vencimientos["zonas:v1:municipio:2024"], modulo.TTL_SEGUNDOS)
        self.assertEqual(self.redis.vencimientos["zonas:v1:municipio:2024:etag"], modulo.TTL_SEGUNDOS)
        self.assertNotIn("zonas:v1:lock:municipio:2024", self.redis.datos)

    def test_candado_con_vencimiento_de_60_segundos(self):
        self.assertTrue(self.cache.adquirir_lock("municipio", 2024))
        self.assertEqual(self.redis.vencimientos["zonas:v1:lock:municipio:2024"], 60)
        self.assertFalse(self.cache.adquirir_lock("municipio", 2024))

    def test_cuerpo_con_gzip_y_sin_gzip_es_el_mismo_json(self):
        con = resolver(self.cache, "municipio", 2024, self.construir, accept_encoding="gzip, deflate, br")
        sin = resolver(self.cache, "municipio", 2024, self.construir, accept_encoding="identity")
        self.assertEqual(con.encabezados["Content-Encoding"], "gzip")
        self.assertNotIn("Content-Encoding", sin.encabezados)
        self.assertEqual(gzip.decompress(con.cuerpo), sin.cuerpo)
        self.assertEqual(json.loads(sin.cuerpo), CONTENIDO)
        self.assertEqual(self.llamadas, 1)

    def test_gzip_con_q0_no_se_considera_aceptado(self):
        r = resolver(self.cache, "municipio", 2024, self.construir, accept_encoding="gzip;q=0, identity")
        self.assertNotIn("Content-Encoding", r.encabezados)

    def test_x_cache_miss_luego_hit(self):
        r1 = resolver(self.cache, "municipio", 2024, self.construir)
        r2 = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(r1.encabezados["X-Cache"], "MISS")
        self.assertEqual(r2.encabezados["X-Cache"], "HIT")

    def test_x_cache_hit_tambien_con_gzip_y_sin_gzip(self):
        resolver(self.cache, "municipio", 2024, self.construir)
        for ae in ("gzip", "identity"):
            r = resolver(self.cache, "municipio", 2024, self.construir, accept_encoding=ae)
            self.assertEqual(r.encabezados["X-Cache"], "HIT")

    def test_x_cache_304_es_hit(self):
        etag = resolver(self.cache, "municipio", 2024, self.construir).encabezados["ETag"]
        r = resolver(self.cache, "municipio", 2024, self.construir, if_none_match=etag)
        self.assertEqual((r.status, r.encabezados["X-Cache"]), (304, "HIT"))

    def test_x_cache_bypass_si_el_anio_no_es_cacheable(self):
        for anio in (2025, 1999):
            r = resolver(self.cache, "municipio", anio, self.construir)
            self.assertEqual(r.encabezados["X-Cache"], "BYPASS")

    def test_x_cache_bypass_si_redis_esta_caido(self):
        self.redis.caido = True
        r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(r.encabezados["X-Cache"], "BYPASS")

    def test_x_cache_hit_para_quien_espera_el_candado(self):
        self.redis.datos["zonas:v1:lock:municipio:2024"] = b"1"

        def otro_proceso():
            time.sleep(0.2)
            self.cache.guardar("municipio", 2024, serializar(CONTENIDO))

        hilo = threading.Thread(target=otro_proceso)
        with patch.object(modulo, "INTERVALO_ESPERA_SEGUNDOS", 0.05):
            hilo.start()
            r = resolver(self.cache, "municipio", 2024, self.construir)
            hilo.join()
        self.assertEqual(r.encabezados["X-Cache"], "HIT")

    def test_x_cache_miss_si_el_candado_no_se_libero_y_se_calculo_y_guardo(self):
        self.redis.datos["zonas:v1:lock:municipio:2024"] = b"1"
        with patch.object(modulo, "ESPERA_MAXIMA_SEGUNDOS", 0.1), patch.object(modulo, "INTERVALO_ESPERA_SEGUNDOS", 0.05):
            r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(r.encabezados["X-Cache"], "MISS")

    def test_encabezados_de_cache(self):
        r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(r.encabezados["Cache-Control"], "private, no-cache")
        self.assertEqual(r.encabezados["Vary"], "Accept-Encoding")

    def test_serializar_igual_que_fastapi(self):
        self.assertEqual(serializar(CONTENIDO), JSONResponse(CONTENIDO).body)


class TestSerializarNaN(unittest.TestCase):
    """Hallazgo 7: antes (Pydantic) NaN/Infinity salian como null; no debe dar 500."""

    def test_nan_e_infinito_salen_como_null(self):
        cuerpo = {"valor": float("nan"), "lista": [1.5, float("inf"), float("-inf")], "anidado": {"x": float("nan")}}
        self.assertEqual(
            json.loads(serializar(cuerpo)),
            {"valor": None, "lista": [1.5, None, None], "anidado": {"x": None}},
        )

    def test_sin_nan_el_resultado_es_el_de_siempre(self):
        self.assertEqual(serializar(CONTENIDO), JSONResponse(CONTENIDO).body)

    def test_el_endpoint_con_nan_en_los_datos_responde_200_con_null(self):
        cache = ZonasCache(RedisFalso())
        r = resolver(cache, "municipio", 2024, lambda: {"features": [{"properties": {"score_privacion": float("nan")}}]})
        self.assertEqual(r.status, 200)
        self.assertIsNone(json.loads(gzip.decompress(r.cuerpo) if "Content-Encoding" in r.encabezados else r.cuerpo)["features"][0]["properties"]["score_privacion"])


class TestETag(Base):
    def test_if_none_match_devuelve_304_sin_cuerpo(self):
        etag = resolver(self.cache, "municipio", 2024, self.construir).encabezados["ETag"]
        r = resolver(self.cache, "municipio", 2024, self.construir, if_none_match=etag)
        self.assertEqual((r.status, r.cuerpo), (304, b""))
        self.assertEqual(r.encabezados["ETag"], etag)

    def test_etag_debil_y_comodin(self):
        etag = resolver(self.cache, "municipio", 2024, self.construir).encabezados["ETag"]
        self.assertEqual(resolver(self.cache, "municipio", 2024, self.construir, if_none_match="W/" + etag).status, 304)
        self.assertEqual(resolver(self.cache, "municipio", 2024, self.construir, if_none_match="*").status, 304)
        self.assertEqual(resolver(self.cache, "municipio", 2024, self.construir, if_none_match='"otro", ' + etag).status, 304)

    def test_etag_distinto_devuelve_200_completo(self):
        resolver(self.cache, "municipio", 2024, self.construir)
        r = resolver(self.cache, "municipio", 2024, self.construir, if_none_match='"viejo"')
        self.assertEqual(r.status, 200)
        self.assertTrue(r.cuerpo)

    def test_primera_peticion_con_if_none_match_calcula_y_responde_200(self):
        r = resolver(self.cache, "municipio", 2024, self.construir, if_none_match='"viejo"')
        self.assertEqual(r.status, 200)
        self.assertEqual(self.llamadas, 1)


class TestAniosNoValidos(Base):
    def test_anio_no_cacheable_no_escribe_en_redis_y_calcula_siempre(self):
        for anio in (2025, 2030, 1999, 99999):
            resolver(self.cache, "municipio", anio, self.construir)
        self.assertEqual(self.llamadas, 4)
        self.assertEqual(self.redis.datos, {})
        self.assertEqual(self.redis.escrituras, 0)

    def test_anio_no_cacheable_responde_igual_que_siempre_sin_etag(self):
        r = resolver(self.cache, "municipio", 2025, self.construir)
        self.assertEqual(r.status, 200)
        self.assertNotIn("ETag", r.encabezados)
        self.assertEqual(json.loads(r.cuerpo), CONTENIDO)

    def test_nivel_desconocido_no_se_cachea(self):
        resolver(self.cache, "region", 2024, self.construir)
        self.assertEqual(self.redis.datos, {})

    def test_solo_2024_es_cacheable(self):
        self.assertEqual(modulo.ANIOS_CACHEABLES, frozenset({2024}))


class TestInvalidacion(Base):
    def test_invalida_solo_zonas_y_no_toca_candados_ni_otras_claves(self):
        resolver(self.cache, "municipio", 2024, self.construir)
        resolver(self.cache, "departamento", 2024, self.construir)
        self.redis.datos["proyeccion:v2:zonas:score_privacion:2027"] = b"x"
        self.redis.datos["zonas:v1:lock:municipio:2024"] = b"1"
        borradas = self.cache.invalidar()
        self.assertEqual(borradas, 4)  # 2 niveles x (cuerpo + etag)
        self.assertEqual(
            sorted(self.redis.datos),
            ["proyeccion:v2:zonas:score_privacion:2027", "zonas:v1:lock:municipio:2024"],
        )

    def test_tras_invalidar_se_recalcula(self):
        resolver(self.cache, "municipio", 2024, self.construir)
        self.cache.invalidar()
        resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(self.llamadas, 2)

    def test_invalidar_cache_zonas_nunca_lanza(self):
        self.redis.caido = True
        with patch.object(modulo, "zonas_cache", self.cache):
            self.assertEqual(modulo.invalidar_cache_zonas(), 0)

    def test_invalidar_cache_zonas_devuelve_cuantas_borro(self):
        resolver(self.cache, "municipio", 2024, self.construir)
        with patch.object(modulo, "zonas_cache", self.cache):
            self.assertEqual(modulo.invalidar_cache_zonas(), 2)


class TestAntiEstampida(Base):
    def test_quien_no_consigue_el_candado_espera_y_no_recalcula(self):
        self.redis.datos["zonas:v1:lock:municipio:2024"] = b"1"  # otro proceso calculando

        def otro_proceso():
            time.sleep(0.3)
            self.cache.guardar("municipio", 2024, serializar(CONTENIDO))

        hilo = threading.Thread(target=otro_proceso)
        with patch.object(modulo, "INTERVALO_ESPERA_SEGUNDOS", 0.05):
            hilo.start()
            r = resolver(self.cache, "municipio", 2024, self.construir)
            hilo.join()
        self.assertEqual(self.llamadas, 0)
        self.assertEqual(r.status, 200)

    def test_si_el_candado_no_se_libera_calcula_tras_el_tope(self):
        self.redis.datos["zonas:v1:lock:municipio:2024"] = b"1"
        with patch.object(modulo, "ESPERA_MAXIMA_SEGUNDOS", 0.2), patch.object(modulo, "INTERVALO_ESPERA_SEGUNDOS", 0.05):
            r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(self.llamadas, 1)
        self.assertEqual(r.status, 200)

    def test_cuatro_peticiones_simultaneas_calculan_una_sola_vez(self):
        resultados = []

        def lenta():
            self.llamadas += 1
            time.sleep(0.3)
            return CONTENIDO

        def peticion():
            resultados.append(resolver(self.cache, "municipio", 2024, lenta))

        with patch.object(modulo, "INTERVALO_ESPERA_SEGUNDOS", 0.05):
            hilos = [threading.Thread(target=peticion) for _ in range(4)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join()
        self.assertEqual(self.llamadas, 1)
        self.assertEqual(len(resultados), 4)
        self.assertEqual({r.encabezados["ETag"] for r in resultados}, {resultados[0].encabezados["ETag"]})

    def test_el_candado_se_libera_aunque_construir_falle(self):
        def falla():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            resolver(self.cache, "municipio", 2024, falla)
        self.assertNotIn("zonas:v1:lock:municipio:2024", self.redis.datos)


class TestCandadoYGuardado(Base):
    """Hallazgos 3a y 3b de la revision."""

    def test_si_el_dueño_falla_los_que_esperan_toman_el_relevo_sin_esperar_el_tope(self):
        resultados = {}
        estado = {"llamadas": 0}

        def construir_dueño_falla():
            estado["llamadas"] += 1
            if estado["llamadas"] == 1:
                time.sleep(0.2)
                raise RuntimeError("BD caida")
            return CONTENIDO

        def dueño():
            try:
                resolver(self.cache, "municipio", 2024, construir_dueño_falla)
            except RuntimeError as error:
                resultados["dueño"] = error

        def espera():
            time.sleep(0.05)  # llega cuando el dueño ya tiene el candado
            t0 = time.monotonic()
            resultados["espera"] = resolver(self.cache, "municipio", 2024, construir_dueño_falla)
            resultados["espera_segundos"] = time.monotonic() - t0

        with patch.object(modulo, "INTERVALO_ESPERA_SEGUNDOS", 0.05), patch.object(modulo, "ESPERA_MAXIMA_SEGUNDOS", 10):
            h1, h2 = threading.Thread(target=dueño), threading.Thread(target=espera)
            h1.start(); h2.start(); h1.join(); h2.join()
        self.assertIsInstance(resultados["dueño"], RuntimeError)
        self.assertEqual(resultados["espera"].status, 200)
        self.assertEqual(resultados["espera"].encabezados["X-Cache"], "MISS")
        self.assertLess(resultados["espera_segundos"], 2)  # NO tuvo que agotar los 10 s de tope
        self.assertEqual(estado["llamadas"], 2)

    def test_si_redis_falla_al_guardar_no_se_calcula_dos_veces(self):
        def guardar_falla(*args, **kwargs):
            raise redis.ConnectionError("Redis cayo justo despues de calcular")

        self.cache.guardar = guardar_falla
        r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(self.llamadas, 1)  # antes de la correccion: 2
        self.assertEqual(r.status, 200)
        self.assertEqual(r.encabezados["X-Cache"], "BYPASS")
        self.assertEqual(json.loads(r.cuerpo), CONTENIDO)
        self.assertFalse(self.cache.redis_disponible())  # y se abre el circuito

    def test_el_candado_se_libera_tambien_si_el_guardado_falla(self):
        def guardar_falla(*args, **kwargs):
            raise redis.ConnectionError("x")

        self.cache.guardar = guardar_falla
        resolver(self.cache, "municipio", 2024, self.construir)
        self.assertNotIn("zonas:v1:lock:municipio:2024", self.redis.datos)


class TestRedisCaido(Base):
    def test_sirve_sin_cache_si_redis_falla(self):
        self.redis.caido = True
        r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(r.status, 200)
        self.assertNotIn("ETag", r.encabezados)
        self.assertEqual(json.loads(r.cuerpo), CONTENIDO)


class TestCircuitoRedis(Base):
    """Hallazgo 2: con Redis inalcanzable no se paga el timeout en CADA peticion."""

    def test_tras_un_fallo_las_siguientes_peticiones_no_tocan_redis(self):
        self.redis.caido = True
        resolver(self.cache, "municipio", 2024, self.construir)  # primer fallo: abre el circuito
        llamadas = []
        original = self.redis.mget
        self.redis.mget = lambda *a, **k: (llamadas.append(1), original(*a, **k))[1]
        self.redis.get = lambda *a, **k: llamadas.append(1)
        for _ in range(5):
            r = resolver(self.cache, "municipio", 2024, self.construir)
            self.assertEqual(r.encabezados["X-Cache"], "BYPASS")
        self.assertEqual(llamadas, [])  # ni una sola llamada a Redis
        self.assertEqual(self.llamadas, 6)  # se calculo cada vez, como antes del cache

    def test_el_circuito_se_cierra_pasado_el_tiempo_y_se_recupera(self):
        self.redis.caido = True
        resolver(self.cache, "municipio", 2024, self.construir)
        self.redis.caido = False  # Redis vuelve
        r = resolver(self.cache, "municipio", 2024, self.construir)
        self.assertEqual(r.encabezados["X-Cache"], "BYPASS")  # aun abierto
        inicio = time.monotonic()
        with patch.object(modulo.time, "monotonic", lambda: inicio + modulo.CIRCUITO_SEGUNDOS + 1):
            r = resolver(self.cache, "municipio", 2024, self.construir)
            self.assertEqual(r.encabezados["X-Cache"], "MISS")
            r = resolver(self.cache, "municipio", 2024, self.construir)
            self.assertEqual(r.encabezados["X-Cache"], "HIT")

    def test_timeouts_de_redis_acotados(self):
        self.assertLessEqual(modulo.TIMEOUT_CONEXION_SEGUNDOS, 1)
        self.assertLessEqual(modulo.TIMEOUT_LECTURA_SEGUNDOS, 3)
        self.assertEqual(modulo.CIRCUITO_SEGUNDOS, 30)


class TestNadaDelUsuario(unittest.TestCase):
    def test_construir_zonas_no_recibe_usuario(self):
        from routers import zonas

        self.assertEqual(list(inspect.signature(zonas._construir_zonas).parameters), ["nivel", "anio", "db"])


class TestEndpoint(Base):
    """GET /api/zonas completo (FastAPI TestClient) con cache simulada."""

    def setUp(self):
        super().setUp()
        from db.session import get_db
        from routers import zonas
        from routers.auth import get_current_user

        self.eventos = []
        outer = self

        class DbFalsa:
            def rollback(self):
                outer.eventos.append("rollback")

        app = FastAPI()
        app.include_router(zonas.router)
        app.dependency_overrides[get_db] = lambda: DbFalsa()
        app.dependency_overrides[get_current_user] = lambda: None
        self.cliente = TestClient(app)
        for p in (
            patch.object(zonas, "zonas_cache", self.cache),
            patch.object(zonas, "_construir_zonas", lambda nivel, anio, db: (self.eventos.append("construir"), self.construir())[1]),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_primera_200_segunda_304_y_encabezados(self):
        r1 = self.cliente.get("/api/zonas?nivel=municipio&anio=2024")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json(), CONTENIDO)
        self.assertEqual(r1.headers["content-type"], "application/json")
        self.assertEqual(r1.headers["cache-control"], "private, no-cache")
        etag = r1.headers["etag"]
        r2 = self.cliente.get("/api/zonas?nivel=municipio&anio=2024", headers={"If-None-Match": etag})
        self.assertEqual((r2.status_code, r2.content), (304, b""))
        self.assertEqual(self.llamadas, 1)

    def test_cabecera_x_cache_en_el_endpoint(self):
        url = "/api/zonas?nivel=municipio&anio=2024"
        self.assertEqual(self.cliente.get(url).headers["x-cache"], "MISS")
        self.assertEqual(self.cliente.get(url).headers["x-cache"], "HIT")
        self.assertEqual(self.cliente.get(url, headers={"Accept-Encoding": "gzip"}).headers["x-cache"], "HIT")
        etag = self.cliente.get(url).headers["etag"]
        self.assertEqual(self.cliente.get(url, headers={"If-None-Match": etag}).headers["x-cache"], "HIT")
        self.assertEqual(self.cliente.get("/api/zonas?nivel=municipio&anio=2025").headers["x-cache"], "BYPASS")
        self.redis.caido = True
        self.assertEqual(self.cliente.get(url).headers["x-cache"], "BYPASS")

    def test_sin_anio_usa_2024_y_comparte_entrada(self):
        self.cliente.get("/api/zonas?nivel=departamento")
        self.cliente.get("/api/zonas?nivel=departamento&anio=2024")
        self.assertEqual(self.llamadas, 1)

    def test_respuesta_gzip_la_entrega_el_cliente_ya_descomprimida(self):
        r = self.cliente.get("/api/zonas?nivel=municipio&anio=2024", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.headers.get("content-encoding"), "gzip")
        self.assertEqual(r.json(), CONTENIDO)

    def test_libera_la_conexion_de_bd_antes_de_calcular(self):
        # Hallazgo 1 de la revision: la sesion (compartida con get_current_user)
        # no debe retener una conexion del pool mientras se espera Redis/candado.
        self.cliente.get("/api/zonas?nivel=municipio&anio=2024")
        self.assertEqual(self.eventos[:2], ["rollback", "construir"])

    def test_nivel_invalido_sigue_dando_422(self):
        self.assertEqual(self.cliente.get("/api/zonas?nivel=region").status_code, 422)

    def test_mismo_cuerpo_para_usuarios_distintos(self):
        from routers.auth import get_current_user

        a = self.cliente.get("/api/zonas?nivel=municipio&anio=2024").json()
        self.cliente.app.dependency_overrides[get_current_user] = lambda: object()
        b = self.cliente.get("/api/zonas?nivel=municipio&anio=2024").json()
        self.assertEqual(a, b)
        self.assertEqual(self.llamadas, 1)


if __name__ == "__main__":
    print(f"[Redis de las pruebas: {BACKEND_REDIS}]")
    unittest.main()
