"""Pruebas de CONTRATO del etiquetado real/proyeccion (2026-10-10, "opcion A") del
lado del backend. No usan BD, Redis ni RabbitMQ: todo se mockea.

Contrato que fijan:
  * es_proyeccion(origen): empieza por "proyeccion-" o termina en "-proyeccion"
    (DANE-proyeccion-poblacion-AAAA es REAL).
  * Un deficit de la fila del año pedido es real solo si su origen lo es; el congelado
    de 2024 usado para otros años es SIEMPRE es_real=False.
  * score_privacion.es_real = True solo si los TRES insumos son reales; su fiabilidad es
    el minimo de las de sus componentes y esta en [0, 100]; incluye `componentes`.
  * Los campos viejos de PuntoZona (codigo_municipio, valor, es_real) no cambian.
  * La clave de cache de zonas es proyeccion:v3:... (v2 sirve el contrato viejo).

Se corre con pytest o con unittest:
    python -m pytest services/tests_contrato_proyeccion.py
    python -m services.tests_contrato_proyeccion
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.proyecciones import ComponenteScore, PuntoZona
from services import proyeccion_service as servicio
from services.origen import FIABILIDAD_DATO_REAL, FIABILIDAD_MINIMA, es_proyeccion, es_real, fiabilidad_de_origen
from services.proyeccion_cache import ProyeccionCache, VERSION_LIMITES, VERSION_ZONAS
from services.score_privacion import obtener_deficit_para_anio

LIMITES = {
    "cobertura_acueducto": (0.0, 100.0),
    "cobertura_alcantarillado": (0.0, 100.0),
    "deficit_habitacional_cuantitativo": (0.0, 100.0),
}


class TestEsProyeccion(unittest.TestCase):
    def test_prefijo_y_sufijo(self):
        self.assertTrue(es_proyeccion("proyeccion-ultimo-valor-observado-2024"))
        self.assertTrue(es_proyeccion("proyeccion-techo360-ultimo-valor"))
        self.assertTrue(es_proyeccion("TerriData-proyeccion"))

    def test_fuentes_reales_no_son_proyeccion(self):
        for origen in ("TerriData", "IGAC Catastro", "DANE-CNPV2018", "DANE-proyeccion-poblacion-2030"):
            self.assertFalse(es_proyeccion(origen), origen)
            self.assertTrue(es_real(origen), origen)

    def test_sin_origen_se_trata_como_real(self):
        self.assertFalse(es_proyeccion(None))
        self.assertFalse(es_proyeccion(""))

    def test_es_sensible_a_mayusculas_como_el_like_de_postgres(self):
        self.assertFalse(es_proyeccion("Proyeccion-x"))

    def test_fiabilidad_siempre_entre_0_y_100(self):
        casos = [("TerriData", None), ("TerriData", 5), ("proyeccion-x", None), ("proyeccion-x", 60), ("proyeccion-x", 250), ("proyeccion-x", -3)]
        for origen, ic in casos:
            f = fiabilidad_de_origen(origen, ic)
            self.assertGreaterEqual(f, 0.0)
            self.assertLessEqual(f, 100.0)
        self.assertEqual(fiabilidad_de_origen("TerriData", None), FIABILIDAD_DATO_REAL)
        self.assertEqual(fiabilidad_de_origen("proyeccion-x", None), FIABILIDAD_MINIMA)
        self.assertEqual(fiabilidad_de_origen("proyeccion-x", 60), 60.0)


class TestCompatibilidadDeEsquema(unittest.TestCase):
    def test_payload_viejo_sigue_validando(self):
        z = PuntoZona(codigo_municipio="05001", valor=0.5, es_real=False)
        self.assertIsNone(z.origen)
        self.assertIsNone(z.fiabilidad)
        self.assertIsNone(z.componentes)

    def test_dump_conserva_los_campos_viejos(self):
        d = PuntoZona(codigo_municipio="05001", valor=0.5, es_real=True, origen="TerriData", fiabilidad=100.0).model_dump()
        for campo in ("codigo_municipio", "valor", "es_real"):
            self.assertIn(campo, d)

    def test_roundtrip_por_el_cache(self):
        z = PuntoZona(
            codigo_municipio="05001", valor=0.5, es_real=False, origen="proyeccion-score-privacion", fiabilidad=60.0,
            componentes=[ComponenteScore(variable="cobertura_acueducto", valor=90.0, origen="TerriData", es_real=True, fiabilidad=100.0)],
        )
        z2 = PuntoZona(**z.model_dump())
        self.assertEqual(z2.componentes[0].variable, "cobertura_acueducto")


class TestClavesDeCache(unittest.TestCase):
    def test_zonas_pasa_a_v3(self):
        self.assertEqual(VERSION_ZONAS, "v3")
        self.assertEqual(ProyeccionCache._clave_zonas("score_privacion", 2030), "proyeccion:v3:zonas:score_privacion:2030")

    def test_ya_no_se_lee_el_prefijo_viejo(self):
        self.assertNotIn(":v2:zonas:", ProyeccionCache._clave_zonas("densidad_poblacional", 2027))

    def test_claves_sin_cambio_de_contenido_conservan_prefijo(self):  # serie; limites_base paso a v3
        # limites_base SI cambio de contenido el 2026-10-10 (min-max solo sobre el universo): v3
        self.assertEqual(ProyeccionCache._clave_limites_base(2024), "proyeccion:v3:limites_base:2024")
        self.assertEqual(ProyeccionCache._clave_serie("05001", 2027, 2030), "proyeccion:serie:05001:2027:2030")


class TestCalcularZonasPasaOrigenYFiabilidad(unittest.IsolatedAsyncioTestCase):
    async def test_pasa_los_campos_nuevos(self):
        respuesta = {"status": "ok", "zonas": [
            {"codigo_municipio": "05001", "valor": 90.0, "es_real": False, "origen": "proyeccion-techo360-ultimo-valor", "fiabilidad": 50.0}]}
        with patch.object(servicio, "solicitar_proyeccion", new=AsyncMock(return_value=respuesta)):
            zonas = await servicio.calcular_zonas("cobertura_acueducto", 2026)
        self.assertEqual(zonas[0].origen, "proyeccion-techo360-ultimo-valor")
        self.assertEqual(zonas[0].fiabilidad, 50.0)

    async def test_servicio_viejo_sin_campos_nuevos_no_rompe(self):
        respuesta = {"status": "ok", "zonas": [{"codigo_municipio": "05001", "valor": 90.0, "es_real": True}]}
        with patch.object(servicio, "solicitar_proyeccion", new=AsyncMock(return_value=respuesta)):
            zonas = await servicio.calcular_zonas("cobertura_acueducto", 2026)
        self.assertIsNone(zonas[0].origen)


def _reg_deficit(valor=5.0, es_real_=False, fiab=70.0):
    return {"valor": valor, "origen": "TerriData" if es_real_ else "proyeccion-tendencia-nacional-2018-2024",
            "es_real": es_real_, "fiabilidad": fiab, "anio_fuente": 2024}


async def _score(acu, alc, deficit):
    """Corre calcular_zonas_score_privacion para UN municipio con los puntos dados."""
    with patch.object(servicio, "calcular_limites_base", return_value=LIMITES), \
         patch.object(servicio, "obtener_deficit_para_anio", return_value={"05001": deficit} if deficit else {}), \
         patch.object(servicio.proyeccion_cache, "obtener_limites_base", new=AsyncMock(return_value=LIMITES)), \
         patch.object(servicio, "calcular_zonas", new=AsyncMock(side_effect=[[acu] if acu else [], [alc] if alc else []])):
        zonas = await servicio.calcular_zonas_score_privacion(2030, db=None)
    return zonas[0]


def _punto(valor, real, origen=None, fiab=None):
    origen = origen or ("TerriData" if real else "proyeccion-techo360-ultimo-valor")
    return PuntoZona(codigo_municipio="05001", valor=valor, es_real=real, origen=origen, fiabilidad=fiab)


class TestContratoDelScore(unittest.IsolatedAsyncioTestCase):
    async def test_tres_insumos_reales_dan_score_real(self):
        z = await _score(_punto(90.0, True, fiab=100.0), _punto(80.0, True, fiab=100.0), _reg_deficit(5.0, True, 100.0))
        self.assertTrue(z.es_real)
        self.assertEqual(z.origen, "score-privacion")
        self.assertEqual(z.fiabilidad, 100.0)
        self.assertEqual(len(z.componentes), 3)

    async def test_un_insumo_proyectado_basta_para_es_real_false(self):
        reales = lambda: (_punto(90.0, True, fiab=100.0), _punto(80.0, True, fiab=100.0), _reg_deficit(5.0, True, 100.0))  # noqa: E731
        acu, alc, d = reales()
        casos = {
            "acueducto proyectado": (_punto(90.0, False, fiab=60.0), alc, d),
            "alcantarillado proyectado": (acu, _punto(80.0, False, fiab=60.0), d),
            "deficit proyectado": (acu, alc, _reg_deficit(5.0, False, 70.0)),
        }
        for nombre, (a, b, c) in casos.items():
            z = await _score(a, b, c)
            self.assertIsNotNone(z.valor, nombre)
            self.assertFalse(z.es_real, nombre)
            self.assertEqual(z.origen, "proyeccion-score-privacion", nombre)

    async def test_fiabilidad_del_score_es_el_minimo_de_los_componentes(self):
        z = await _score(_punto(90.0, False, fiab=60.0), _punto(80.0, True, fiab=100.0), _reg_deficit(5.0, False, 70.0))
        self.assertEqual(z.fiabilidad, 60.0)
        self.assertEqual(min(c.fiabilidad for c in z.componentes), z.fiabilidad)

    async def test_fiabilidad_entre_0_y_100(self):
        z = await _score(_punto(90.0, False, fiab=30.0), _punto(80.0, False, fiab=30.0), _reg_deficit(5.0, False, 70.0))
        self.assertGreaterEqual(z.fiabilidad, 0.0)
        self.assertLessEqual(z.fiabilidad, 100.0)

    async def test_componentes_traen_variable_valor_origen_es_real_fiabilidad(self):
        z = await _score(_punto(90.0, True, fiab=100.0), _punto(80.0, False, "proyeccion-ultimo-valor-observado-2024", 50.0), _reg_deficit(5.0, False, 70.0))
        por_variable = {c.variable: c for c in z.componentes}
        self.assertEqual(set(por_variable), {"cobertura_acueducto", "cobertura_alcantarillado", "deficit_habitacional_cuantitativo"})
        self.assertEqual(por_variable["cobertura_alcantarillado"].origen, "proyeccion-ultimo-valor-observado-2024")
        self.assertFalse(por_variable["cobertura_alcantarillado"].es_real)
        self.assertEqual(por_variable["cobertura_acueducto"].valor, 90.0)

    async def test_falta_un_insumo_no_hay_score_ni_fiabilidad(self):
        z = await _score(_punto(90.0, True, fiab=100.0), None, _reg_deficit(5.0, True, 100.0))
        self.assertIsNone(z.valor)
        self.assertFalse(z.es_real)
        self.assertIsNone(z.fiabilidad)
        self.assertIsNone(z.origen)

    async def test_respuesta_vieja_sin_fiabilidad_se_deriva_de_es_real(self):
        z = await _score(_punto(90.0, False), _punto(80.0, True), _reg_deficit(5.0, True, 100.0))
        fiabs = {c.variable: c.fiabilidad for c in z.componentes}
        self.assertEqual(fiabs["cobertura_acueducto"], FIABILIDAD_MINIMA)
        self.assertEqual(fiabs["cobertura_alcantarillado"], FIABILIDAD_DATO_REAL)


class TestDeficitPorAnio(unittest.TestCase):
    """obtener_deficit_para_anio con una sesion falsa que devuelve filas fijas."""

    @staticmethod
    def _db(filas):
        db = MagicMock()
        db.execute.return_value.mappings.return_value.all.return_value = [
            {"codigodivipola": c, "yearm": y, "valuevar": v, "originvar": o, "intervaloconfianza": ic} for c, y, v, o, ic in filas
        ]
        return db

    def test_sin_fila_del_anio_usa_el_congelado_de_2024_como_proyeccion(self):
        db = self._db([("05001", 2024, 5.0, "TerriData", None)])  # incluso si el 2024 fuera "real"
        r = obtener_deficit_para_anio(db, 2030, 2024)["05001"]
        self.assertEqual(r["valor"], 5.0)
        self.assertFalse(r["es_real"])
        self.assertEqual(r["anio_fuente"], 2024)

    def test_la_fila_del_anio_pedido_manda_y_su_origen_decide(self):
        db = self._db([
            ("05001", 2024, 5.0, "proyeccion-tendencia-nacional-2018-2024", 70.0),
            ("05001", 2030, 9.0, "DANE-CNPV2030", None),
            ("05002", 2030, 4.0, "proyeccion-techo360-delta-ridge-deficit", 40.0),
        ])
        out = obtener_deficit_para_anio(db, 2030, 2024)
        self.assertEqual(out["05001"]["valor"], 9.0)
        self.assertTrue(out["05001"]["es_real"])
        self.assertEqual(out["05001"]["fiabilidad"], 100.0)
        self.assertFalse(out["05002"]["es_real"])
        self.assertEqual(out["05002"]["fiabilidad"], 40.0)

    def test_en_el_anio_base_la_fila_proyectada_sale_como_proyeccion(self):
        db = self._db([("05001", 2024, 5.0, "proyeccion-tendencia-nacional-2018-2024", 70.0)])
        r = obtener_deficit_para_anio(db, 2024, 2024)["05001"]
        self.assertFalse(r["es_real"])
        self.assertEqual(r["fiabilidad"], 70.0)


class TestScriptDeLimpiezaDeCache(unittest.TestCase):
    def test_por_defecto_solo_lista_y_no_borra(self):
        import importlib.util
        from pathlib import Path
        ruta = Path(__file__).resolve().parents[1] / "scripts" / "limpiar_cache_proyeccion.py"
        spec = importlib.util.spec_from_file_location("limpiar_cache_proyeccion", ruta)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)

        cliente = MagicMock()
        cliente.scan_iter.side_effect = [
            iter(["proyeccion:v2:zonas:score_privacion:2027", "proyeccion:v2:zonas:densidad_poblacional:2030"]),
            iter(["proyeccion:v2:limites_base:2024"]),
        ]
        encontradas, borradas = modulo.limpiar(cliente, version="v2", ejecutar=False)
        self.assertEqual(len(encontradas), 3)
        self.assertEqual(borradas, 0)
        cliente.delete.assert_not_called()
        self.assertEqual([c.kwargs["match"] for c in cliente.scan_iter.call_args_list],
                         ["proyeccion:v2:zonas:*", "proyeccion:v2:limites_base:*"])

        cliente2 = MagicMock()
        cliente2.scan_iter.side_effect = [iter(["proyeccion:v2:zonas:score_privacion:2027"]), iter([])]
        cliente2.delete.return_value = 1
        _, borradas2 = modulo.limpiar(cliente2, version="v2", ejecutar=True)
        self.assertEqual(borradas2, 1)

    def test_se_niega_a_borrar_la_version_vigente(self):
        import importlib.util
        from pathlib import Path
        ruta = Path(__file__).resolve().parents[1] / "scripts" / "limpiar_cache_proyeccion.py"
        spec = importlib.util.spec_from_file_location("limpiar_cache_proyeccion", ruta)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        for vigente in (VERSION_ZONAS, VERSION_LIMITES):
            with self.assertRaises(ValueError):
                modulo.limpiar(MagicMock(), version=vigente, ejecutar=True)


if __name__ == "__main__":
    unittest.main()
