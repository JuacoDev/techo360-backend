"""Pruebas de /api/proyecciones/zonas y /serie para los 6 indicadores MATERIALIZADOS (lectura directa de
mapcalordb, sin RPC, sin regresion, sin respaldo). No usan BD, Redis ni RabbitMQ: todo se mockea.

    python -m unittest services.tests_proyeccion_materializados
"""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.session import get_db
from routers import auth as rauth
from routers import proyecciones as rproy
from schemas.proyecciones import PuntoProyeccion, PuntoZona
from services import proyeccion_service as servicio
from services.proyeccion_cache import ProyeccionCache, VERSION_ZONAS

NUEVOS = [
    "cobertura_acueducto",
    "cobertura_alcantarillado",
    "deficit_habitacional_cuantitativo",
    "deficit_habitacional_cualitativo",
    "deficit_habitacional_total",
    "tenencia_informal_terrenos",
]


class FakeDB:
    """Session de mentira: devuelve `filas` para cualquier consulta y recuerda las llamadas."""

    def __init__(self, filas):
        self.filas = filas
        self.llamadas = []

    def execute(self, sql, params=None):
        self.llamadas.append((str(sql), params))
        filas = self.filas
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: filas))


def fila_zona(cod, valor, origen="proyeccion-techo360-ultimo-valor", ic=60):
    return {"codigodivipola": cod, "valuevar": valor, "originvar": origen, "intervaloconfianza": ic}


def fila_serie(anio, valor, origen="proyeccion-techo360-ultimo-valor", ic=60):
    return {"yearm": anio, "valuevar": valor, "originvar": origen, "intervaloconfianza": ic}


class TestPatrones(unittest.TestCase):
    def test_zonas_acepta_los_8_y_rechaza_el_resto(self):
        for ind in ["score_privacion", "densidad_poblacional", *NUEVOS]:
            self.assertTrue(re.match(rproy.PATRON_INDICADOR_ZONAS, ind), ind)
        for ind in ["", "score", "cobertura", "tenencia", "densidad_poblacionalx", "xcobertura_acueducto", "deficit_habitacional"]:
            self.assertFalse(re.match(rproy.PATRON_INDICADOR_ZONAS, ind), ind)

    def test_serie_acepta_densidad_y_los_6_pero_no_el_score(self):
        for ind in ["densidad_poblacional", *NUEVOS]:
            self.assertTrue(re.match(rproy.PATRON_INDICADOR_SERIE, ind), ind)
        self.assertFalse(re.match(rproy.PATRON_INDICADOR_SERIE, "score_privacion"))

    def test_nombres_exactos_de_mapcalordb(self):
        self.assertEqual(sorted(servicio.INDICADORES_MATERIALIZADOS), sorted(NUEVOS))


class TestLecturaDeZonas(unittest.TestCase):
    def test_un_select_con_contrato_y_etiquetado(self):
        db = FakeDB([
            fila_zona("05001", 12.5),
            fila_zona("05002", 40.0, origen="TerriData", ic=None),
            fila_zona("05003", 7.0, origen="DANE-proyeccion-poblacion-2030", ic=None),
            fila_zona("05004", 30.0, origen="proyeccion-techo360-modelo-tenencia", ic=None),
        ])
        zonas = servicio.leer_zonas_materializadas("tenencia_informal_terrenos", 2030, db)
        self.assertEqual(len(db.llamadas), 1)
        self.assertEqual(db.llamadas[0][1]["indicador"], "tenencia_informal_terrenos")
        self.assertEqual(db.llamadas[0][1]["anio"], 2030)
        self.assertEqual(db.llamadas[0][1]["excluidos"], ["00000", "01001"])
        por = {z.codigo_municipio: z for z in zonas}
        self.assertFalse(por["05001"].es_real)
        self.assertEqual(por["05001"].fiabilidad, 60.0)
        self.assertTrue(por["05002"].es_real)
        self.assertEqual(por["05002"].fiabilidad, 100.0)
        self.assertTrue(por["05003"].es_real)  # DANE-proyeccion-poblacion-* es dato real
        self.assertFalse(por["05004"].es_real)
        self.assertEqual(por["05004"].fiabilidad, 30.0)  # proyeccion sin intervalo -> minimo
        self.assertEqual(por["05001"].origen, "proyeccion-techo360-ultimo-valor")
        self.assertIsNone(por["05001"].componentes)

    def test_filas_sin_valor_se_omiten_sin_inventar_nada(self):
        db = FakeDB([fila_zona("05001", None), fila_zona("05002", float("nan")), fila_zona("05003", 1.0)])
        with self.assertLogs(servicio.logger, level="WARNING"):
            zonas = servicio.leer_zonas_materializadas("cobertura_acueducto", 2025, db)
        self.assertEqual([z.codigo_municipio for z in zonas], ["05003"])

    def test_indicador_no_materializado_se_rechaza(self):
        for ind in ("score_privacion", "densidad_poblacional", "otra"):
            with self.assertRaises(servicio.IndicadorNoSoportadoError):
                servicio.leer_zonas_materializadas(ind, 2030, FakeDB([]))

    def test_modelo_de_zonas(self):
        for ind in NUEVOS:
            self.assertEqual(servicio.modelo_de_zonas(ind), servicio.MODELO_MATERIALIZADO)
        self.assertEqual(servicio.modelo_de_zonas("densidad_poblacional"), servicio.MODELO_VIGENTE)
        self.assertEqual(servicio.modelo_de_zonas("score_privacion"), servicio.MODELO_SCORE_PRIVACION)


class TestLecturaDeSerie(unittest.TestCase):
    def test_serie_completa_2024_2042(self):
        db = FakeDB([fila_serie(a, 10.0 + a - 2024) for a in range(2024, 2043)])
        serie = servicio.leer_serie_materializada("05001", "cobertura_acueducto", 2024, 2042, db)
        self.assertEqual(len(db.llamadas), 1)
        self.assertEqual([p.anio for p in serie], list(range(2024, 2043)))
        self.assertTrue(all(p.es_real is False and p.origen and p.fiabilidad == 60.0 for p in serie))

    def test_anios_faltantes_se_omiten_y_se_registran(self):
        db = FakeDB([fila_serie(2024, 1.0), fila_serie(2026, 3.0)])
        with self.assertLogs(servicio.logger, level="WARNING"):
            serie = servicio.leer_serie_materializada("05001", "tenencia_informal_terrenos", 2024, 2026, db)
        self.assertEqual([p.anio for p in serie], [2024, 2026])

    def test_sin_filas_es_lookup_error(self):
        with self.assertRaises(LookupError):
            servicio.leer_serie_materializada("99999", "cobertura_acueducto", 2024, 2042, FakeDB([]))

    def test_rangos_invalidos(self):
        for desde, hasta in ((2023, 2030), (2024, 2043), (2030, 2025)):
            with self.assertRaises(servicio.RangoProyeccionInvalidoError):
                servicio.leer_serie_materializada("05001", "cobertura_acueducto", desde, hasta, FakeDB([]))


class TestClavesDeCache(unittest.TestCase):
    def test_densidad_conserva_su_clave(self):
        self.assertEqual(ProyeccionCache._clave_serie("05001", 2027, 2030), "proyeccion:serie:05001:2027:2030")
        self.assertEqual(ProyeccionCache._clave_serie("05001", 2027, 2030, "densidad_poblacional"), "proyeccion:serie:05001:2027:2030")

    def test_los_nuevos_llevan_el_indicador_dentro_de_la_clave_v3(self):
        claves = {ProyeccionCache._clave_serie("05001", 2024, 2042, i) for i in NUEVOS}
        self.assertEqual(len(claves), 6)
        for c in claves:
            self.assertTrue(c.startswith(f"proyeccion:{VERSION_ZONAS}:serie:"), c)
        self.assertEqual(ProyeccionCache._clave_zonas("tenencia_informal_terrenos", 2030), "proyeccion:v3:zonas:tenencia_informal_terrenos:2030")


class TestRouter(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(rproy.router)
        app.dependency_overrides[get_db] = lambda: FakeDB(self.filas)
        app.dependency_overrides[rauth.get_current_user] = lambda: SimpleNamespace(id=0)
        self.filas = []
        self.cli = TestClient(app)
        self.cache = SimpleNamespace(
            obtener_zonas=AsyncMock(return_value=None),
            guardar_zonas=AsyncMock(),
            obtener_serie=AsyncMock(return_value=None),
            guardar_serie=AsyncMock(),
        )
        self.parche = patch.object(rproy, "proyeccion_cache", self.cache)
        self.parche.start()
        self.addCleanup(self.parche.stop)

    def test_zonas_de_cada_indicador_nuevo(self):
        self.filas = [fila_zona("05001", 5.0), fila_zona("05002", 6.0, origen="TerriData", ic=None)]
        for ind in NUEVOS:
            r = self.cli.get("/api/proyecciones/zonas", params={"anio": 2030, "indicador": ind})
            self.assertEqual(r.status_code, 200, (ind, r.text))
            cuerpo = r.json()
            self.assertEqual(cuerpo["modelo"], servicio.MODELO_MATERIALIZADO)
            self.assertEqual([z["es_real"] for z in cuerpo["zonas"]], [False, True])
            self.assertEqual(self.cache.guardar_zonas.await_args.args[:2], (ind, 2030))

    def test_zonas_anios_e_indicadores_invalidos(self):
        for anio in (2024, 2043):
            r = self.cli.get("/api/proyecciones/zonas", params={"anio": anio, "indicador": "cobertura_acueducto"})
            self.assertEqual(r.status_code, 422, anio)
        for ind in ("desconocido", "deficit", ""):
            r = self.cli.get("/api/proyecciones/zonas", params={"anio": 2030, "indicador": ind})
            self.assertEqual(r.status_code, 422, ind)

    def test_zonas_cache_hit_no_toca_la_bd(self):
        self.cache.obtener_zonas = AsyncMock(return_value=[PuntoZona(codigo_municipio="05001", valor=1.0, es_real=False, origen="proyeccion-x", fiabilidad=30.0).model_dump()])
        self.filas = [fila_zona("99999", 9.0)]
        r = self.cli.get("/api/proyecciones/zonas", params={"anio": 2030, "indicador": "tenencia_informal_terrenos"})
        self.assertEqual([z["codigo_municipio"] for z in r.json()["zonas"]], ["05001"])
        self.cache.guardar_zonas.assert_not_awaited()

    def test_serie_de_cada_indicador_nuevo(self):
        self.filas = [fila_serie(a, float(a - 2000)) for a in range(2024, 2043)]
        for ind in NUEVOS:
            r = self.cli.get("/api/proyecciones/serie", params={"codigo_municipio": "05001", "indicador": ind, "desde": 2024, "hasta": 2042})
            self.assertEqual(r.status_code, 200, (ind, r.text))
            cuerpo = r.json()
            self.assertEqual(len(cuerpo["serie"]), 19)
            self.assertEqual(cuerpo["modelo"], servicio.MODELO_MATERIALIZADO)
            self.assertEqual(set(cuerpo["serie"][0]), {"anio", "valor", "es_real", "origen", "fiabilidad"})
            self.assertEqual(self.cache.guardar_serie.await_args.args[-1], ind)

    def test_serie_errores(self):
        base = {"codigo_municipio": "05001", "indicador": "cobertura_acueducto"}
        self.assertEqual(self.cli.get("/api/proyecciones/serie", params={**base, "desde": 2023, "hasta": 2030}).status_code, 422)
        self.assertEqual(self.cli.get("/api/proyecciones/serie", params={**base, "desde": 2024, "hasta": 2043}).status_code, 422)
        self.assertEqual(self.cli.get("/api/proyecciones/serie", params={**base, "indicador": "x", "desde": 2024, "hasta": 2030}).status_code, 422)
        self.assertEqual(self.cli.get("/api/proyecciones/serie", params={**base, "indicador": "score_privacion", "desde": 2024, "hasta": 2030}).status_code, 422)
        self.filas = []
        self.assertEqual(self.cli.get("/api/proyecciones/serie", params={**base, "desde": 2024, "hasta": 2030}).status_code, 404)

    def test_serie_de_densidad_no_cambia_de_camino_ni_de_forma(self):
        with patch.object(rproy, "calcular_proyeccion", new=AsyncMock(return_value=[PuntoProyeccion(anio=2027, valor=1.5)])) as rpc:
            r = self.cli.get("/api/proyecciones/serie", params={"codigo_municipio": "05001", "indicador": "densidad_poblacional", "desde": 2027, "hasta": 2027})
        self.assertEqual(r.status_code, 200)
        rpc.assert_awaited_once()
        cuerpo = r.json()
        self.assertEqual(cuerpo["serie"], [{"anio": 2027, "valor": 1.5}])  # sin campos nulos nuevos
        self.assertEqual(cuerpo["modelo"], servicio.MODELO_VIGENTE)
        self.assertEqual(cuerpo["origen"], servicio.ORIGEN_PROYECCION)

    def test_zonas_de_densidad_y_score_siguen_su_camino(self):
        with patch.object(rproy, "calcular_zonas", new=AsyncMock(return_value=[])) as rpc, \
             patch.object(rproy, "calcular_zonas_score_privacion", new=AsyncMock(return_value=[])) as sc:
            self.assertEqual(self.cli.get("/api/proyecciones/zonas", params={"anio": 2030, "indicador": "densidad_poblacional"}).status_code, 200)
            self.assertEqual(self.cli.get("/api/proyecciones/zonas", params={"anio": 2030, "indicador": "score_privacion"}).status_code, 200)
        rpc.assert_awaited_once()
        sc.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
