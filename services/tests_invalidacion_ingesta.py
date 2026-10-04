"""Verificacion de que run() invalida el cache de /api/zonas SIEMPRE (D6,
hallazgo 6 de la revision): al terminar bien, y tambien si revienta a medias.
unittest (stdlib). No usa base de datos ni Redis reales.

Uso:
    python -m services.tests_invalidacion_ingesta
"""
import inspect
import unittest
from unittest.mock import patch

from ingestion import run as modulo_run


class TestRunInvalidaSiempre(unittest.TestCase):
    def test_invalida_al_terminar_bien_y_devuelve_el_resultado(self):
        with patch.object(modulo_run, "_ejecutar", return_value={"ok": []}) as ejecutar, patch(
            "services.zonas_cache.invalidar_cache_zonas"
        ) as invalidar:
            resultado = modulo_run.run(anio=2026, database_url="x")
        self.assertEqual(resultado, {"ok": []})
        ejecutar.assert_called_once_with(anio=2026, database_url="x")
        invalidar.assert_called_once()

    def test_invalida_tambien_si_la_corrida_revienta_a_medias(self):
        with patch.object(modulo_run, "_ejecutar", side_effect=RuntimeError("adapter roto")), patch(
            "services.zonas_cache.invalidar_cache_zonas"
        ) as invalidar:
            with self.assertRaises(RuntimeError):
                modulo_run.run(anio=2026)
        invalidar.assert_called_once()

    def test_invalida_despues_de_ejecutar_no_antes(self):
        orden = []
        with patch.object(modulo_run, "_ejecutar", side_effect=lambda *a, **k: orden.append("ejecutar") or {}), patch(
            "services.zonas_cache.invalidar_cache_zonas", side_effect=lambda: orden.append("invalidar")
        ):
            modulo_run.run()
        self.assertEqual(orden, ["ejecutar", "invalidar"])

    def test_la_firma_publica_de_run_no_cambio(self):
        # scheduler.py y backfill_historico llaman run(anio=..., notificar_admin=..., database_url=...)
        parametros = list(inspect.signature(modulo_run.run).parameters)
        self.assertEqual(parametros, ["anio", "notificar_admin", "valores_anio_anterior", "database_url", "variables"])
        self.assertIn("database_url", modulo_run.run.__doc__)


if __name__ == "__main__":
    unittest.main()
