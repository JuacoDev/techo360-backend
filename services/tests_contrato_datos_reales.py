"""Contrato del score de privacion proyectado contra los DATOS REALES de produccion, en
MODO LECTURA. NO corre por defecto: exige TECHO360_DATOS_REALES=1, DATABASE_URL y el
codigo de techo360-proyeccion montado en PROYECCION_PATH (el RPC por RabbitMQ se
sustituye por una llamada directa a app.services.proyeccion.calcular_zonas, que es lo que
el consumer ejecutaria). Sin Redis: el cache se mockea. Sesion de solo lectura forzada.

Pensado para un contenedor temporal `:verify` (Tailscale -> postgres-principal):

    (TECHO360_CSV_M4=<ruta al CSV M4 montado en el contenedor> activa ademas las pruebas del CSV simulado)
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
    from services.score_privacion import (
        calcular_limites_base,
        calcular_limites_desde_filas,
        calcular_score_privacion,
        normalizar_minmax,
        obtener_codigos_universo,
        obtener_deficit_para_anio,
    )


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
        # INC-001: el nombre antiguo "DANE-proyeccion-poblacion-" ya no esta en la base; el backend lo trata como
        # proyeccion (red de seguridad) y techo360-proyeccion/app/origen.py aun no, asi que NO se compara aqui.
        for o in ("TerriData", "proyeccion-poblacion-sobre-area", "proyeccion-x", "TerriData-proyeccion", None):
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


RUTA_CSV_M4 = os.environ.get("TECHO360_CSV_M4")
VARIABLES_SCORE = ("cobertura_acueducto", "cobertura_alcantarillado", "deficit_habitacional_cuantitativo")


@unittest.skipUnless(HABILITADO, "requiere TECHO360_DATOS_REALES=1, DATABASE_URL y PROYECCION_PATH (solo lectura)")
class TestMinMaxSoloUniversoConDatosReales(unittest.TestCase):
    """Min-max del score solo sobre los municipios de zonas_municipio + recorte a [0, 1],
    con las filas REALES de la BD (lectura) y, si se monta, el CSV M4 simulado encima."""

    @classmethod
    def setUpClass(cls):
        import pandas as pd

        cls.pd = pd
        cls.engine = create_engine(os.environ["DATABASE_URL"], connect_args={"options": "-c default_transaction_read_only=on"})
        with cls.engine.connect() as con:
            assert con.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
        with Session(cls.engine) as db:
            cls.universo = obtener_codigos_universo(db)
        with cls.engine.connect() as con:
            cls.filas = pd.read_sql(
                text("""SELECT codigodivipola, namevar, yearm, valuevar FROM public.mapcalordb
                        WHERE namevar = ANY(:v) AND yearm BETWEEN 2024 AND 2042 AND codigodivipola IS NOT NULL
                          AND codigodivipola != '01001' AND valuevar IS NOT NULL"""),
                con, params={"v": list(VARIABLES_SCORE)})
        cls.filas["valuevar"] = cls.filas.valuevar.astype(float)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @staticmethod
    def _filas_2024(df, codigos=None):
        d = df[df.yearm == 2024]
        if codigos is not None:
            d = d[d.codigodivipola.isin(codigos)]
        return d.to_dict("records")

    def _con_csv_m4(self):
        pd = self.pd
        csv = pd.read_csv(RUTA_CSV_M4, dtype={"codigodivipola": str})
        csv = csv[csv.nameVar.isin(VARIABLES_SCORE)].rename(columns={"nameVar": "namevar", "yearM": "yearm"})[["codigodivipola", "namevar", "yearm", "valuevar"]]
        clave = ["codigodivipola", "namevar", "yearm"]
        base = self.filas.merge(csv[clave].assign(_x=1), on=clave, how="left")
        base = base[base._x.isna()].drop(columns="_x")
        return pd.concat([base, csv], ignore_index=True)

    # ---- universo
    def test_el_universo_son_los_1104_municipios_sin_litigio_ni_agregado(self):
        self.assertEqual(len(self.universo), 1104)
        self.assertFalse({"00000", "01001"} & self.universo)

    def test_hay_huerfanos_con_deficit_y_no_estan_en_el_universo(self):
        d = self.filas[(self.filas.yearm == 2024) & (self.filas.namevar == "deficit_habitacional_cuantitativo")]
        huerfanos = set(d.codigodivipola) - self.universo
        self.assertGreaterEqual(len(huerfanos), 18)  # las areas no municipalizadas: 91263, 94883, 97511...

    # ---- limites con los datos actuales
    def test_limites_actuales_son_los_del_universo_y_coinciden_con_un_sql_independiente(self):
        with Session(self.engine) as db:
            lim = calcular_limites_base(db, 2024)
            sql = db.execute(text("""SELECT m.namevar, min(m.valuevar), max(m.valuevar) FROM public.mapcalordb m
                                     JOIN public.zonas_municipio z ON z.codigo_municipio = m.codigodivipola
                                     WHERE m.yearm = 2024 AND m.namevar = ANY(:v) AND z.codigo_municipio NOT IN ('00000', '01001')
                                     GROUP BY m.namevar"""), {"v": list(VARIABLES_SCORE)}).all()
        for variable, minimo, maximo in sql:
            self.assertAlmostEqual(lim[variable][0], float(minimo), places=6, msg=variable)
            self.assertAlmostEqual(lim[variable][1], float(maximo), places=6, msg=variable)
        # y el calculo de la funcion pura sobre las filas leidas da lo mismo
        self.assertEqual(lim, calcular_limites_desde_filas(self._filas_2024(self.filas), self.universo))

    def test_el_maximo_del_deficit_es_el_de_los_1104_municipios(self):
        d = self.filas[(self.filas.yearm == 2024) & (self.filas.namevar == "deficit_habitacional_cuantitativo")]
        maximo_universo = float(d[d.codigodivipola.isin(self.universo)].valuevar.max())
        lim = calcular_limites_desde_filas(self._filas_2024(self.filas), self.universo)
        self.assertEqual(lim["deficit_habitacional_cuantitativo"][1], maximo_universo)

    # ---- CSV M4 simulado
    @unittest.skipUnless(RUTA_CSV_M4 and os.path.exists(RUTA_CSV_M4 or ""), "requiere TECHO360_CSV_M4 (ruta al CSV M4)")
    def test_con_csv_m4_el_maximo_ya_no_es_100_por_los_huerfanos(self):
        estado = self._con_csv_m4()
        fil24 = self._filas_2024(estado)
        con_universo = calcular_limites_desde_filas(fil24, self.universo)["deficit_habitacional_cuantitativo"]
        sin_restringir = calcular_limites_desde_filas(fil24, {f["codigodivipola"] for f in fil24})["deficit_habitacional_cuantitativo"]
        self.assertEqual(sin_restringir[1], 100.0)       # los huerfanos (sin fila en el CSV) siguen en 100
        self.assertLess(con_universo[1], 100.0)          # el universo, con M4, ya no llega a 100
        self.assertAlmostEqual(con_universo[1], 97.80, places=2)

    @unittest.skipUnless(RUTA_CSV_M4 and os.path.exists(RUTA_CSV_M4 or ""), "requiere TECHO360_CSV_M4 (ruta al CSV M4)")
    def test_con_csv_m4_ningun_insumo_ni_score_sale_de_0_1_en_2024_2042(self):
        pd = self.pd
        estado = self._con_csv_m4()
        lim = calcular_limites_desde_filas(self._filas_2024(estado), self.universo)
        fuera, total, sabaneta = [], 0, {}
        for anio in range(2024, 2043):
            sub = estado[(estado.yearm == anio) & estado.codigodivipola.isin(self.universo)]
            por_var = {v: sub[sub.namevar == v].set_index("codigodivipola").valuevar for v in VARIABLES_SCORE}
            self.assertEqual(set(por_var[VARIABLES_SCORE[0]].index), self.universo, anio)  # los 1,104 con insumos
            for codigo in self.universo:
                norm = {v: normalizar_minmax(float(por_var[v][codigo]), *lim[v]) for v in VARIABLES_SCORE}
                score = calcular_score_privacion({v: {"score_normalizado": n} for v, n in norm.items()})
                total += 1
                if any(n is None or not 0.0 <= n <= 1.0 for n in norm.values()) or score is None or not 0.0 <= score <= 1.0:
                    fuera.append((anio, codigo, norm, score))
                if codigo == "05631":
                    sabaneta[anio] = score
        self.assertEqual(fuera, [])
        self.assertEqual(total, 1104 * 19)
        self.assertEqual(len(sabaneta), 19)
        for anio, s in sabaneta.items():
            self.assertGreaterEqual(s, 0.0, anio)  # Sabaneta (05631) llegaba a -0.0014
            self.assertLessEqual(s, 1.0, anio)


if __name__ == "__main__":
    unittest.main()
