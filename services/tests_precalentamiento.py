"""Verificacion de services/precalentamiento.py (D6). unittest (stdlib).
Redis simulado y sin base ni servicio de proyeccion reales: los calculos
pesados se sustituyen por dobles.

Uso:
    python -m services.tests_precalentamiento
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import precalentamiento as pre
from services.tests_zonas_cache import CONTENIDO, RedisFalso
from services.zonas_cache import ZonasCache

TOTAL_ITEMS = 2 + 1 + len(pre.ANIOS_PROYECCION)  # 2 niveles de zonas + limites + 4 años


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = RedisFalso()
        self.cache = ZonasCache(self.redis)
        patcher = patch.object(pre, "zonas_cache", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def parchear_items(self, zonas=None, limites=None, proyeccion=None):
        self.m_zonas = MagicMock(side_effect=zonas or (lambda nivel, anio, forzar: "ok"))
        self.m_limites = AsyncMock(side_effect=limites) if limites else AsyncMock(return_value="ok")
        self.m_proy = AsyncMock(side_effect=proyeccion) if proyeccion else AsyncMock(return_value="ok")
        for p in (
            patch.object(pre, "_precalentar_zonas", self.m_zonas),
            patch.object(pre, "_precalentar_limites_base", self.m_limites),
            patch.object(pre, "_precalentar_proyeccion", self.m_proy),
        ):
            p.start()
            self.addCleanup(p.stop)


class TestPrecalentar(Base):
    async def test_ejecuta_todos_los_items_de_a_uno_con_pausa(self):
        self.parchear_items()
        pausas = []

        async def dormir(segundos):
            pausas.append(segundos)

        with patch.object(pre.asyncio, "sleep", dormir):
            resumen = await pre.precalentar(forzar=False, pausa=2.0)
        self.assertEqual(len(resumen), TOTAL_ITEMS)
        self.assertEqual(pausas, [2.0] * (TOTAL_ITEMS - 1))
        self.assertTrue(all(valor == "ok" for valor in resumen.values()))
        self.m_zonas.assert_any_call("departamento", 2024, False)
        self.m_zonas.assert_any_call("municipio", 2024, False)
        self.assertEqual([c.args[0] for c in self.m_proy.await_args_list], [2027, 2028, 2029, 2030])

    async def test_que_se_precalienta_exactamente(self):
        self.assertEqual(pre.ANIOS_PROYECCION, (2027, 2028, 2029, 2030))
        self.assertEqual(pre.ANIO_ZONAS, 2024)
        self.assertEqual(pre.DEMORA_ARRANQUE_SEGUNDOS, 90)
        self.assertEqual(pre.PAUSA_ENTRE_ITEMS_SEGUNDOS, 2.0)

    async def test_forzar_se_propaga(self):
        self.parchear_items()
        await pre.precalentar(forzar=True, pausa=0)
        self.m_zonas.assert_any_call("municipio", 2024, True)
        self.m_limites.assert_awaited_once_with(True)
        self.m_proy.assert_any_await(2027, True)

    async def test_un_error_se_registra_y_se_sigue_con_el_resto(self):
        self.parchear_items(proyeccion=[RuntimeError("RPC caido"), "ok", "ok", "ok"])
        with self.assertLogs(pre.logger, level="ERROR"):
            resumen = await pre.precalentar(pausa=0)
        self.assertIn("error: RPC caido", resumen["proyeccion:score_privacion:2027"])
        self.assertEqual(resumen["proyeccion:score_privacion:2030"], "ok")
        self.assertEqual(len(resumen), TOTAL_ITEMS)

    async def test_nunca_lanza_aunque_todo_falle(self):
        self.parchear_items(
            zonas=RuntimeError("bd"), limites=RuntimeError("bd"), proyeccion=RuntimeError("rpc")
        )
        with self.assertLogs(pre.logger, level="ERROR"):
            resumen = await pre.precalentar(pausa=0)
        self.assertTrue(all(v.startswith("error") for v in resumen.values()))

    async def test_candado_global_ocupado_no_hace_nada(self):
        self.parchear_items()
        self.redis.datos[pre.LOCK_CLAVE] = b"1"
        resumen = await pre.precalentar(pausa=0)
        self.assertIn("otra instancia", resumen["precalentamiento"])
        self.m_zonas.assert_not_called()
        self.m_limites.assert_not_awaited()
        self.m_proy.assert_not_awaited()

    async def test_libera_el_candado_al_terminar_aunque_haya_errores(self):
        self.parchear_items(proyeccion=RuntimeError("rpc"))
        with self.assertLogs(pre.logger, level="ERROR"):
            await pre.precalentar(pausa=0)
        self.assertNotIn(pre.LOCK_CLAVE, self.redis.datos)

    async def test_dos_llamadas_simultaneas_solo_trabaja_una(self):
        self.parchear_items()
        resumenes = await asyncio.gather(pre.precalentar(pausa=0.01), pre.precalentar(pausa=0.01))
        omitidos = [r for r in resumenes if "precalentamiento" in r]
        self.assertEqual(len(omitidos), 1)
        self.assertEqual(self.m_limites.await_count, 1)

    async def test_redis_caido_se_omite_sin_lanzar(self):
        self.parchear_items()
        self.redis.caido = True
        resumen = await pre.precalentar(pausa=0)
        self.assertIn("Redis no disponible", resumen["precalentamiento"])
        self.m_zonas.assert_not_called()


class TestTrasArranque(Base):
    async def test_espera_la_demora_y_luego_precalienta_sin_forzar(self):
        llamadas = []

        async def dormir(segundos):
            llamadas.append(("sleep", segundos))

        async def precalentar_falso(forzar=False, pausa=0):
            llamadas.append(("precalentar", forzar))
            return {}

        with patch.object(pre.asyncio, "sleep", dormir), patch.object(pre, "precalentar", precalentar_falso):
            await pre.precalentar_tras_arranque()
        self.assertEqual(llamadas, [("sleep", 90), ("precalentar", False)])

    async def test_un_error_inesperado_no_tumba_el_arranque(self):
        async def dormir(segundos):
            return None

        with patch.object(pre.asyncio, "sleep", dormir), patch.object(
            pre, "precalentar", AsyncMock(side_effect=RuntimeError("inesperado"))
        ):
            with self.assertLogs(pre.logger, level="ERROR"):
                await pre.precalentar_tras_arranque()  # no debe lanzar


class TestItemsReales(Base):
    """Los items de verdad (sin parchear), con la base y el servicio simulados."""

    def parches_db(self, construir):
        sesion = MagicMock()
        return [
            patch.object(pre, "SessionLocal", return_value=sesion),
            patch("routers.zonas._construir_zonas", construir),
        ]

    def test_zonas_omite_lo_ya_cacheado_si_no_se_fuerza(self):
        llamadas = []
        for p in self.parches_db(lambda n, a, db: llamadas.append(1) or CONTENIDO):
            p.start()
            self.addCleanup(p.stop)
        self.assertEqual(pre._precalentar_zonas("municipio", 2024, False), "ok")
        self.assertEqual(pre._precalentar_zonas("municipio", 2024, False), "omitido (ya en cache)")
        self.assertEqual(len(llamadas), 1)

    def test_zonas_forzar_recalcula_y_sobrescribe(self):
        llamadas = []
        for p in self.parches_db(lambda n, a, db: llamadas.append(1) or CONTENIDO):
            p.start()
            self.addCleanup(p.stop)
        pre._precalentar_zonas("municipio", 2024, False)
        self.assertEqual(pre._precalentar_zonas("municipio", 2024, True), "ok")
        self.assertEqual(len(llamadas), 2)

    def test_zonas_no_calcula_si_otro_proceso_tiene_el_candado(self):
        llamadas = []
        for p in self.parches_db(lambda n, a, db: llamadas.append(1) or CONTENIDO):
            p.start()
            self.addCleanup(p.stop)
        self.redis.datos["zonas:v1:lock:municipio:2024"] = b"1"
        self.assertIn("otro proceso", pre._precalentar_zonas("municipio", 2024, False))
        self.assertEqual(llamadas, [])

    def test_zonas_libera_su_candado_aunque_falle(self):
        def falla(n, a, db):
            raise RuntimeError("bd")

        for p in self.parches_db(falla):
            p.start()
            self.addCleanup(p.stop)
        with self.assertRaises(RuntimeError):
            pre._precalentar_zonas("municipio", 2024, True)
        self.assertNotIn("zonas:v1:lock:municipio:2024", self.redis.datos)

    async def test_proyeccion_omite_lo_cacheado(self):
        with patch.object(pre.proyeccion_cache, "obtener_zonas", AsyncMock(return_value=[{"x": 1}])), patch.object(
            pre, "calcular_zonas_score_privacion", AsyncMock()
        ) as calcular:
            self.assertEqual(await pre._precalentar_proyeccion(2027, False), "omitido (ya en cache)")
            calcular.assert_not_awaited()

    async def test_proyeccion_calcula_y_guarda_si_falta(self):
        punto = MagicMock()
        punto.model_dump.return_value = {"codigo_municipio": "05001", "valor": 0.5}
        guardar = AsyncMock()
        with patch.object(pre.proyeccion_cache, "obtener_zonas", AsyncMock(return_value=None)), patch.object(
            pre.proyeccion_cache, "guardar_zonas", guardar
        ), patch.object(pre, "calcular_zonas_score_privacion", AsyncMock(return_value=[punto])), patch.object(
            pre, "SessionLocal", return_value=MagicMock()
        ):
            self.assertEqual(await pre._precalentar_proyeccion(2027, False), "ok")
        guardar.assert_awaited_once_with("score_privacion", 2027, [{"codigo_municipio": "05001", "valor": 0.5}])

    async def test_limites_base_omite_o_calcula(self):
        guardar = AsyncMock()
        with patch.object(pre.proyeccion_cache, "obtener_limites_base", AsyncMock(return_value={"a": 1})):
            self.assertEqual(await pre._precalentar_limites_base(False), "omitido (ya en cache)")
        with patch.object(pre.proyeccion_cache, "obtener_limites_base", AsyncMock(return_value={"a": 1})), patch.object(
            pre.proyeccion_cache, "guardar_limites_base", guardar
        ), patch.object(pre, "calcular_limites_base", MagicMock(return_value={"b": 2})), patch.object(
            pre, "SessionLocal", return_value=MagicMock()
        ):
            self.assertEqual(await pre._precalentar_limites_base(True), "ok")
        guardar.assert_awaited_once_with(2024, {"b": 2})


if __name__ == "__main__":
    unittest.main()
