"""Pruebas de la normalizacion min-max SOLO sobre el universo de municipios + recorte a [0, 1]
(2026-10-10). Sin BD ni Redis: la sesion de BD es un doble que contesta por el texto del SQL.

Fijan:
  * normalizar_minmax y calcular_score_privacion recortan a [0, 1];
  * los limites del score (calcular_limites_base y los dos bloques inline de routers/zonas.py)
    se calculan solo sobre los codigos de zonas_municipio sin 00000/01001: un codigo huerfano
    (area no municipalizada) con deficit 100 NO fija el maximo;
  * las filas que se leen no cambian (cualquier origen cuenta).

    python -m pytest services/tests_minmax_universo.py     o     python -m services.tests_minmax_universo
"""
import json
import unittest

from routers import zonas
from services.score_privacion import (
    CODIGOS_FUERA_DEL_UNIVERSO,
    calcular_limites_base,
    calcular_limites_desde_filas,
    calcular_score_privacion,
    normalizar_minmax,
    obtener_codigos_universo,
)

ACU, ALC, DEF = "cobertura_acueducto", "cobertura_alcantarillado", "deficit_habitacional_cuantitativo"


class TestRecorteA01(unittest.TestCase):
    def test_dentro_del_rango_no_cambia(self):
        self.assertEqual(normalizar_minmax(75, 50, 100), 0.5)
        self.assertEqual(normalizar_minmax(50, 50, 100), 0.0)
        self.assertEqual(normalizar_minmax(100, 50, 100), 1.0)

    def test_por_encima_del_maximo_se_recorta_a_1(self):
        self.assertEqual(normalizar_minmax(120, 50, 100), 1.0)

    def test_por_debajo_del_minimo_se_recorta_a_0(self):
        self.assertEqual(normalizar_minmax(0.2, 0.47, 100), 0.0)  # el caso de Sabaneta (deficit < minimo de 2024)

    def test_nan_es_dato_faltante_no_un_cero(self):
        # sin esta guarda, el recorte convertia NaN en 0.0 (max(0.0, nan) == 0.0)
        self.assertIsNone(normalizar_minmax(float("nan"), 0.0, 100.0))

    def test_sigue_devolviendo_none_si_falta_algo_o_el_rango_es_degenerado(self):
        self.assertIsNone(normalizar_minmax(None, 0, 1))
        self.assertIsNone(normalizar_minmax(5, None, 1))
        self.assertIsNone(normalizar_minmax(5, 0, None))
        self.assertIsNone(normalizar_minmax(5, 7, 7))

    def test_score_siempre_en_0_1_aunque_llegue_un_normalizado_sin_recortar(self):
        def z(a, b, d):
            return {ACU: {"score_normalizado": a}, ALC: {"score_normalizado": b}, DEF: {"score_normalizado": d}}
        for caso in (z(-0.5, -0.5, 1.5), z(1.5, 1.5, -0.5), z(0.0, 0.0, 1.0), z(1.0, 1.0, 0.0)):
            s = calcular_score_privacion(caso)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)
        self.assertEqual(calcular_score_privacion(z(0.0, 0.0, 1.0)), 1.0)
        self.assertEqual(calcular_score_privacion(z(1.0, 1.0, 0.0)), 0.0)

    def test_score_con_insumos_normales_no_cambia(self):
        z = {ACU: {"score_normalizado": 0.5}, ALC: {"score_normalizado": 0.5}, DEF: {"score_normalizado": 0.5}}
        self.assertEqual(calcular_score_privacion(z), 0.5)


FILAS = [
    # universo: 05001 y 05002
    {"codigodivipola": "05001", "namevar": DEF, "valuevar": 50.0},
    {"codigodivipola": "05002", "namevar": DEF, "valuevar": 10.0},
    {"codigodivipola": "05001", "namevar": ACU, "valuevar": 90.0},
    {"codigodivipola": "05002", "namevar": ACU, "valuevar": 70.0},
    {"codigodivipola": "05001", "namevar": ALC, "valuevar": 80.0},
    {"codigodivipola": "05002", "namevar": ALC, "valuevar": 60.0},
    # fuera del universo: huerfano (area no municipalizada) saturado, litigio y agregado nacional
    {"codigodivipola": "91263", "namevar": DEF, "valuevar": 100.0},
    {"codigodivipola": "00000", "namevar": DEF, "valuevar": 0.0},
    {"codigodivipola": "01001", "namevar": ACU, "valuevar": 100.0},
]


class TestLimitesSoloDelUniverso(unittest.TestCase):
    def test_el_huerfano_no_fija_el_maximo(self):
        lim = calcular_limites_desde_filas(FILAS, {"05001", "05002"})
        self.assertEqual(lim[DEF], (10.0, 50.0))   # no (0.0, 100.0)
        self.assertEqual(lim[ACU], (70.0, 90.0))   # el 100 del agregado nacional tampoco
        self.assertEqual(lim[ALC], (60.0, 80.0))

    def test_sin_restriccion_el_resultado_seria_el_viejo(self):
        todos = {f["codigodivipola"] for f in FILAS}
        self.assertEqual(calcular_limites_desde_filas(FILAS, todos)[DEF], (0.0, 100.0))

    def test_cualquier_origen_cuenta_solo_cambia_sobre_que_municipios(self):
        # las filas no llevan origen: no hay filtro por origen en el calculo de limites
        filas = FILAS + [{"codigodivipola": "05001", "namevar": DEF, "valuevar": 60.0}]
        self.assertEqual(calcular_limites_desde_filas(filas, {"05001", "05002"})[DEF], (10.0, 60.0))

    def test_valores_nulos_se_ignoran_y_variable_sin_datos_da_none(self):
        filas = [{"codigodivipola": "05001", "namevar": DEF, "valuevar": None}]
        lim = calcular_limites_desde_filas(filas, {"05001"})
        self.assertEqual(lim[DEF], (None, None))

    def test_las_exclusiones_son_00000_y_01001(self):
        self.assertEqual(set(CODIGOS_FUERA_DEL_UNIVERSO), {"00000", "01001"})


class _Res:
    def __init__(self, filas):
        self.filas = filas

    def mappings(self):
        return self

    def all(self):
        return list(self.filas)


class _DbPorTexto:
    """execute() contesta segun el texto del SQL (no segun el orden)."""

    def __init__(self, zonas_filas=(), indicadores=(), universo=(), depto_ultimo=()):
        self.zonas_filas, self.indicadores, self.universo, self.depto_ultimo = zonas_filas, indicadores, universo, depto_ultimo
        self.consultas = []

    def execute(self, query, params=None):
        sql = str(query)
        self.consultas.append((sql, params or {}))
        if "FROM public.zonas_municipio" in sql:
            return _Res([(c,) for c in self.universo])
        if "FROM zonas_municipio" in sql or "FROM zonas_departamento" in sql:
            return _Res(self.zonas_filas)
        if "DISTINCT ON" in sql:
            return _Res(self.depto_ultimo)
        if "FROM public.mapcalordb" in sql:
            return _Res(self.indicadores)
        raise AssertionError("consulta inesperada: " + sql[:80])


def _indicadores():
    return [{"codigodivipola": f["codigodivipola"], "namevar": f["namevar"], "valuevar": f["valuevar"], "originvar": "TerriData", "intervaloconfianza": None}
            for f in FILAS if f["codigodivipola"] != "01001"]


class TestCalcularLimitesBase(unittest.TestCase):
    def test_consulta_los_mismos_datos_y_restringe_al_universo(self):
        db = _DbPorTexto(indicadores=FILAS, universo=["05001", "05002"])
        lim = calcular_limites_base(db, 2024)
        self.assertEqual(lim[DEF], (10.0, 50.0))
        sql_filas = [c for c in db.consultas if "FROM public.mapcalordb" in c[0]][0][0]
        self.assertNotIn("originvar", sql_filas.lower())  # sigue leyendo cualquier origen
        # el SELECT debe traer el codigo (hace falta para restringir al universo; el doble no lo notaria
        # si faltara, asi que se comprueba el texto: este fue un bug real atrapado con datos reales)
        self.assertIn("codigodivipola", sql_filas.lower().split("from")[0])
        sql_universo = [c for c in db.consultas if "zonas_municipio" in c[0]][0]
        self.assertEqual(sorted(sql_universo[1]["excluidos"]), ["00000", "01001"])

    def test_obtener_codigos_universo(self):
        self.assertEqual(obtener_codigos_universo(_DbPorTexto(universo=["05001", "05002"])), {"05001", "05002"})


def _zonas_municipio_filas():
    geom = json.dumps({"type": "Point", "coordinates": [0, 0]})
    return [{"codigo": "05001", "codigo_departamento": "05", "nombre": "A", "geometry": geom},
            {"codigo": "05002", "codigo_departamento": "05", "nombre": "B", "geometry": geom}]


class TestZonasRealSoloUniverso(unittest.TestCase):
    def test_municipio_normaliza_con_el_universo_no_con_el_huerfano(self):
        db = _DbPorTexto(zonas_filas=_zonas_municipio_filas(), indicadores=_indicadores())
        feats = {f["properties"]["codigo"]: f["properties"] for f in zonas._construir_zonas("municipio", 2024, db)["features"]}
        # deficit 50 es el maximo del universo -> 1.0 (con el huerfano en 100 habria sido 0.4444)
        self.assertEqual(feats["05001"]["indicadores"][DEF]["score_normalizado"], 1.0)
        self.assertEqual(feats["05002"]["indicadores"][DEF]["score_normalizado"], 0.0)
        for p in feats.values():
            self.assertGreaterEqual(p["score_privacion"], 0.0)
            self.assertLessEqual(p["score_privacion"], 1.0)
        self.assertEqual(feats["05001"]["score_privacion"], 0.3333)
        self.assertEqual(feats["05002"]["score_privacion"], 0.6667)

    def test_departamento_promedia_con_el_mismo_universo(self):
        deptos = [{"codigo": "05", "nombre": "Antioquia", "geometry": json.dumps({"type": "Point", "coordinates": [0, 0]})}]
        db = _DbPorTexto(zonas_filas=deptos, indicadores=_indicadores(), universo=["05001", "05002"])
        feats = {f["properties"]["codigo"]: f["properties"] for f in zonas._construir_zonas("departamento", 2024, db)["features"]}
        self.assertEqual(feats["05"]["score_privacion"], 0.5)  # (0.3333 + 0.6667) / 2; con el huerfano seria otro


if __name__ == "__main__":
    unittest.main()
