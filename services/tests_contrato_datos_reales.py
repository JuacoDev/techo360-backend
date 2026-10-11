"""Contrato del score de privacion proyectado contra los DATOS REALES de produccion, en
MODO LECTURA. NO corre por defecto: exige TECHO360_DATOS_REALES=1, DATABASE_URL y el
codigo de techo360-proyeccion montado en PROYECCION_PATH (el RPC por RabbitMQ se
sustituye por una llamada directa a app.services.proyeccion.calcular_zonas, que es lo que
el consumer ejecutaria). Sin Redis: el cache se mockea. Sesion de solo lectura forzada.

Pensado para un contenedor temporal `:verify` (Tailscale -> postgres-principal):

    docker run --rm --env-file <solo DATABASE_URL + JWT_SECRET_KEY dummy> \
        -e TECHO360_DATOS_REALES=1 -e PROYECCION_PATH=/proy \
        -v <techo360-proyeccion>:/proy:ro techo360/backend:verify \
        sh -c "pip install -q pytest pandas==2.2.3 numpy && python -m pytest -q services/tests_contrato_datos_reales.py"
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

HABILITADO = (
    os.environ.get("TECHO360_DATOS_REALES") == "1"
    and bool(os.environ.get("DATABASE_URL"))
    and bool(os.environ.get("PROYECCION_PATH"))
)
if HABILITADO:
    sys.path.insert(0, os.environ["PROYECCION_PATH"])

if HABILITADO:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.origen import es_proyeccion as es_proyeccion_servicio  # techo360-proyeccion (montado)
    from app.repositories import proyeccion as repo_proyeccion      # noqa: F401  (usa DATABASE_URL, solo lectura)
    from app.services import proyeccion as servicio_proyeccion
    from services import proyeccion_service as servicio
    from services.origen import es_proyeccion
    from services.score_privacion import obtener_deficit_para_anio


async def _rpc_directo(payload, timeout=200.0):
    """Lo que haria el consumer de techo360-proyeccion con una solicitud tipo 'zonas'."""
    zonas = await asyncio.to_thread(servicio_proyeccion.calcular_zonas, payload["indicador"], payload["anio"])
    return {"status": "ok", "indicador": payload["indicador"], "anio": payload["anio"], "zonas": zonas}


@unittest.skipUnless(HABILITADO, "requiere TECHO360_DATOS_REALES=1, DATABASE_URL y PROYECCION_PATH (solo lectura)")
class TestScoreConDatosReales(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["DATABASE_URL"], connect_args={"options": "-c default_transaction_read_only=on"})
        with cls.engine.connect() as con:
            assert con.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
        # el repositorio de techo360-proyeccion tambien debe leer en modo lectura
        repo_proyeccion._engine = cls.engine
        cls.cache = {}

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    async def score(self, anio):
        if anio not in self.cache:
            with Session(self.engine) as db, \
                 patch.object(servicio, "solicitar_proyeccion", new=_rpc_directo), \
                 patch.object(servicio.proyeccion_cache, "obtener_limites_base", new=AsyncMock(return_value=None)), \
                 patch.object(servicio.proyeccion_cache, "guardar_limites_base", new=AsyncMock()):
                self.cache[anio] = await servicio.calcular_zonas_score_privacion(anio, db)
        return self.cache[anio]

    async def test_misma_regla_en_los_dos_repos(self):
        for o in ("TerriData", "DANE-proyeccion-poblacion-2030", "proyeccion-x", "TerriData-proyeccion", None):
            self.assertEqual(es_proyeccion(o), es_proyeccion_servicio(o), o)

    async def test_contrato_del_score_2025_2026_2030_2042(self):
        for anio in (2025, 2026, 2030, 2042):
            zonas = await self.score(anio)
            con_valor = [z for z in zonas if z.valor is not None]
            self.assertGreater(len(con_valor), 1000, anio)  # ~1,052 con los tres insumos
            for z in zonas:
                self.assertEqual(len(z.componentes), 3, (anio, z.codigo_municipio))
                if z.valor is None:
                    self.assertFalse(z.es_real)
                    self.assertIsNone(z.fiabilidad)
                    continue
                self.assertGreaterEqual(z.fiabilidad, 0.0)
                self.assertLessEqual(z.fiabilidad, 100.0)
                self.assertEqual(z.fiabilidad, min(c.fiabilidad for c in z.componentes))
                self.assertEqual(z.es_real, all(c.es_real for c in z.componentes))
                for c in z.componentes:
                    self.assertGreaterEqual(c.fiabilidad, 0.0)
                    self.assertLessEqual(c.fiabilidad, 100.0)
                    if c.origen and es_proyeccion(c.origen):
                        self.assertFalse(c.es_real, (anio, z.codigo_municipio, c.variable, c.origen))

    async def test_hoy_ningun_score_proyectado_es_real(self):
        """El deficit no tiene ninguna fila real posterior a 2018 y la cobertura real termina
        en 2024: para 2025+ NINGUN score puede salir es_real=True."""
        for anio in (2025, 2026, 2030, 2042):
            self.assertFalse(any(z.es_real for z in await self.score(anio)), anio)

    async def test_cobertura_2025_2026_sale_como_proyeccion_en_los_componentes(self):
        for anio in (2025, 2026):
            componentes = [c for z in await self.score(anio) for c in z.componentes if c.variable.startswith("cobertura_") and c.valor is not None]
            self.assertGreater(len(componentes), 2000)
            self.assertFalse(any(c.es_real for c in componentes))
            origenes = {c.origen for c in componentes}
            self.assertTrue(all(es_proyeccion(o) for o in origenes), origenes)
            self.assertIn("proyeccion-ultimo-valor-observado-2024", origenes)  # las filas que YA existen en BD

    async def test_deficit_congelado_2024_es_proyeccion(self):
        with Session(self.engine) as db:
            d2030 = obtener_deficit_para_anio(db, 2030, 2024)
            d2024 = obtener_deficit_para_anio(db, 2024, 2024)
        self.assertGreater(len(d2030), 1000)
        self.assertFalse(any(r["es_real"] for r in d2030.values()))
        self.assertTrue(all(r["anio_fuente"] == 2024 for r in d2030.values()))
        # y el de 2024 trae origen proyeccion-tendencia-nacional-2018-2024 -> tampoco es real
        self.assertFalse(any(r["es_real"] for r in d2024.values()))
        self.assertIn("proyeccion-tendencia-nacional-2018-2024", {r["origen"] for r in d2024.values()})


if __name__ == "__main__":
    unittest.main()
