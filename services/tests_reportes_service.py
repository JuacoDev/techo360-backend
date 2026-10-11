"""Pruebas del servicio de reportes sin base de datos ni RabbitMQ (dobles).

Uso:
    python -m services.tests_reportes_service
"""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from schemas.reporte import ReporteGenerarRequest
from services import reportes_service as rs

ZONAS = [
    {"nivel": "departamento", "codigo": "68", "nombre": "Santander"},
    {"nivel": "municipio", "codigo": "68001", "nombre": "Bucaramanga"},
    {"nivel": "municipio", "codigo": "05001", "nombre": "Medellín"},
]


def _solicitud(formato="pdf", **zonas):
    return ReporteGenerarRequest.model_validate(
        {
            "formato": formato,
            "periodo": {"anio_inicio": 2020, "anio_fin": 2025},
            "opciones": {"comentarios": {"68001": "=SUMA(1)"}},
            "variables": ["score_privacion", "cobertura_acueducto"],
            "zonas": zonas or {"departamentos": ["68"], "municipios": ["68001", "05001"]},
        }
    )


def _series(solicitud):
    return [
        {**zona, "anio": anio, "variable": variable, "origen": "TerriData", "comentario": "",
         "valor": None if (zona["codigo"] == "68" and anio == 2022) else 0.1 * (anio - 2019)}
        for zona in ZONAS
        for anio in range(2020, 2026)
        for variable in solicitud.variables
    ]


class GeneradoresTest(unittest.TestCase):
    def test_pdf_y_excel_se_generan_con_valores_vacios_y_comentario_tipo_formula(self):
        with tempfile.TemporaryDirectory() as tmp:
            for formato, crear, firma in (
                ("pdf", rs._crear_pdf, b"%PDF"),
                ("excel", rs._crear_excel, b"PK"),
            ):
                solicitud = _solicitud(formato)
                destino = Path(tmp) / f"r.{formato}"
                crear(destino, solicitud, ZONAS, _series(solicitud))
                self.assertTrue(destino.read_bytes().startswith(firma))

    def test_grafica_pdf_lleva_una_serie_por_zona_con_color_y_leyenda(self):
        anios, datos = rs._valores_por_grafica(ZONAS, _series(_solicitud()), "cobertura_acueducto", 2020, 2025)
        grafica = rs.ReporteGrafica("cobertura_acueducto", anios, datos)
        self.assertEqual(len(grafica.datos), 3)
        self.assertFalse(grafica.recortada)
        self.assertGreaterEqual(len(rs.PALETA_GRAFICA), rs.MAX_SERIES_LEYENDA)
        self.assertEqual(len(set(rs.PALETA_GRAFICA)), len(rs.PALETA_GRAFICA))

    def test_grafica_pdf_recorta_a_las_series_que_caben_en_la_leyenda(self):
        datos = [(f"Zona {i}", [1.0, 2.0]) for i in range(rs.MAX_SERIES_LEYENDA + 5)]
        grafica = rs.ReporteGrafica("cobertura_acueducto", ["2020", "2021"], datos)
        self.assertEqual(len(grafica.datos), rs.MAX_SERIES_LEYENDA)
        self.assertTrue(grafica.recortada)


class ValoresNoFinitosTest(unittest.TestCase):
    """Regresión: un NaN/inf en los datos reventaba SOLO las gráficas del PDF (el reporte sin
    gráficas salía bien), porque reportlab no puede dibujar un valor no finito."""

    def test_finito_trata_nan_e_inf_como_dato_faltante(self):
        self.assertIsNone(rs._finito(None))
        self.assertIsNone(rs._finito(float("nan")))
        self.assertIsNone(rs._finito(float("inf")))
        self.assertIsNone(rs._finito(float("-inf")))
        self.assertEqual(rs._finito("1.5"), 1.5)
        self.assertEqual(rs._finito(0), 0.0)

    def test_valores_por_grafica_descarta_no_finitos(self):
        series = [
            {**ZONAS[1], "anio": a, "variable": "cobertura_acueducto", "valor": v}
            for a, v in zip(range(2020, 2023), (1.0, float("nan"), float("inf")))
        ]
        _, datos = rs._valores_por_grafica(ZONAS[1:2], series, "cobertura_acueducto", 2020, 2022)
        self.assertEqual(datos[0][1], [1.0, None, None])

    def test_pdf_y_excel_con_graficas_no_fallan_si_llegan_valores_no_finitos(self):
        for valor in (float("nan"), float("inf")):
            for formato, crear in (("pdf", rs._crear_pdf), ("excel", rs._crear_excel)):
                solicitud = _solicitud(formato)
                series = [{**fila, "valor": valor} for fila in _series(solicitud)]
                with tempfile.TemporaryDirectory() as tmp:
                    destino = Path(tmp) / f"r.{formato}"
                    crear(destino, solicitud, ZONAS, series)
                    self.assertGreater(destino.stat().st_size, 0)

    def test_una_grafica_que_no_se_puede_dibujar_no_tumba_el_pdf(self):
        solicitud = _solicitud("pdf")
        with patch.object(rs.ReporteGrafica, "_dibujar", side_effect=RuntimeError("boom")):
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "r.pdf"
                rs._crear_pdf(destino, solicitud, ZONAS, _series(solicitud))
                self.assertTrue(destino.read_bytes().startswith(b"%PDF"))


class PreviaGraficasTest(unittest.TestCase):
    def test_previa_devuelve_una_serie_por_zona_y_variable_con_el_tope_de_la_leyenda(self):
        solicitud = _solicitud()
        with patch.object(rs, "obtener_datos_reporte", return_value=(ZONAS, _series(solicitud))):
            previa = rs.construir_previa_graficas(solicitud, MagicMock())
        self.assertEqual(previa["anios"], [2020, 2021, 2022, 2023, 2024, 2025])
        self.assertEqual([g["variable"] for g in previa["graficas"]], ["score_privacion", "cobertura_acueducto"])
        grafica = previa["graficas"][1]
        self.assertEqual(grafica["total_series"], 3)
        self.assertEqual(len(grafica["series"]), 3)
        self.assertEqual(grafica["unidad"], "%")
        santander = next(s for s in grafica["series"] if s["nombre"].startswith("Santander"))
        self.assertIsNone(santander["valores"][2])  # 2022 sin dato
        self.assertFalse(any(isinstance(v, float) and math.isnan(v) for v in santander["valores"]))


class ValidarZonasTest(unittest.TestCase):
    def _db(self, departamentos=(), municipios=()):
        db = MagicMock()
        db.execute.side_effect = [[(c,) for c in departamentos], [(c,) for c in municipios]]
        return db

    def test_zonas_existentes_no_falla(self):
        rs.validar_zonas_existentes(self._db(["68"], ["68001"]), _solicitud(departamentos=["68"], municipios=["68001"]))

    def test_departamento_inexistente_se_rechaza_antes_de_encolar(self):
        with self.assertRaisesRegex(ValueError, "Departamentos inexistentes: 99"):
            rs.validar_zonas_existentes(self._db([], []), _solicitud(departamentos=["99"], municipios=[]))

    def test_municipio_inexistente_se_rechaza_antes_de_encolar(self):
        with self.assertRaisesRegex(ValueError, "Municipios inexistentes: 68999"):
            rs.validar_zonas_existentes(self._db([], []), _solicitud(departamentos=[], municipios=["68999"]))


if __name__ == "__main__":
    unittest.main()
