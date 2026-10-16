"""Pruebas de services/indicador_service.py::obtener_historico_indicador -- clasificacion `tipo`
(backfill / observado / sin_dato) de GET /api/indicadores/{codigo}/{variable}/historico.

Contrato (2026-10-10): es "backfill" todo origen que es_proyeccion() (empieza por "proyeccion-" o termina en
"-proyeccion") o este en la lista historica ORIGENES_BACKFILL; "observado" en otro caso. Los origenes nuevos
proyeccion-techo360-* de la carga de proyecciones NO estaban en la lista cerrada y salian como "observado".
Sin BD: la sesion es un doble.

    python -m pytest services/tests_indicador_service.py      o      python -m services.tests_indicador_service
"""
import unittest
from unittest.mock import MagicMock

from services.indicador_service import ANIO_FIN_HISTORICO, ANIO_INICIO_HISTORICO, ORIGENES_BACKFILL, obtener_historico_indicador

ORIGENES_NUEVOS_CSV = [
    "proyeccion-techo360-aproximacion-externa",
    "proyeccion-techo360-aproximacion-externa-sspd2023",
    "proyeccion-techo360-delta-ridge-deficit",
    "proyeccion-techo360-modelo-deficit-nivel",
    "proyeccion-techo360-modelo-tenencia",
    "proyeccion-techo360-tendencia-saturacion-suave-2018-2024",
    "proyeccion-techo360-ultimo-valor",
    "proyeccion-techo360-ultimo-valor-igac-2026",
]


def _serie(filas):
    """filas: [(anio, valor, origen)] -> serie devuelta por el servicio, con una sesion falsa."""
    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = [
        {"yearm": a, "valuevar": v, "originvar": o, "intervaloconfianza": None} for a, v, o in filas
    ]
    return {p["anio"]: p for p in obtener_historico_indicador(db, "05001", "cobertura_acueducto")}


class TestTipoDelHistorico(unittest.TestCase):
    def test_un_origen_proyeccion_techo360_sale_como_backfill(self):
        p = _serie([(2024, 50.0, "proyeccion-techo360-ultimo-valor")])[2024]
        self.assertEqual(p["tipo"], "backfill")
        self.assertEqual(p["origen"], "proyeccion-techo360-ultimo-valor")

    def test_los_8_origenes_nuevos_del_csv_son_backfill(self):
        for o in ORIGENES_NUEVOS_CSV:
            self.assertEqual(_serie([(2024, 1.0, o)])[2024]["tipo"], "backfill", o)

    def test_los_origenes_historicos_de_la_lista_siguen_siendo_backfill(self):
        for o in ORIGENES_BACKFILL:
            self.assertEqual(_serie([(2020, 1.0, o)])[2020]["tipo"], "backfill", o)

    def test_origen_terminado_en_proyeccion_es_backfill(self):
        self.assertEqual(_serie([(2020, 1.0, "TerriData-proyeccion")])[2020]["tipo"], "backfill")

    def test_los_datos_reales_siguen_siendo_observados(self):
        for o in ("TerriData", "IGAC Catastro", "DANE-CNPV2018"):
            self.assertEqual(_serie([(2024, 1.0, o)])[2024]["tipo"], "observado", o)

    def test_inc001_densidad_proyectada_es_backfill(self):
        # INC-001: la poblacion proyectada DANE / area es proyeccion, con el nombre nuevo y con el legado
        for o in ("proyeccion-poblacion-sobre-area", "DANE-proyeccion-poblacion-2024"):
            self.assertEqual(_serie([(2024, 1.0, o)])[2024]["tipo"], "backfill", o)

    def test_anio_sin_fila_es_sin_dato_y_la_ventana_no_cambia(self):
        s = _serie([(2024, 1.0, "TerriData")])
        self.assertEqual(s[2023]["tipo"], "sin_dato")
        self.assertEqual(sorted(s), list(range(ANIO_INICIO_HISTORICO, ANIO_FIN_HISTORICO + 1)))
        self.assertEqual((ANIO_INICIO_HISTORICO, ANIO_FIN_HISTORICO), (2008, 2024))


if __name__ == "__main__":
    unittest.main()
