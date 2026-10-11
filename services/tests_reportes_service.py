"""Pruebas del servicio de reportes sin base de datos ni RabbitMQ (dobles).

Uso:
    python -m services.tests_reportes_service
"""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from schemas.reporte import ReporteGenerarRequest
from services import reportes_pdf
from services import reportes_service as rs

ZONAS = [
    {"nivel": "departamento", "codigo": "68", "nombre": "Santander"},
    {"nivel": "municipio", "codigo": "68001", "nombre": "Bucaramanga"},
    {"nivel": "municipio", "codigo": "05001", "nombre": "Medellín"},
]


def _solicitud(formato="pdf", plantilla=None, **zonas):
    return ReporteGenerarRequest.model_validate(
        {
            "formato": formato,
            "periodo": {"anio_inicio": 2020, "anio_fin": 2025},
            "opciones": {"comentarios": {"68001": "=SUMA(1)"}, "plantilla": plantilla},
            "variables": ["score_privacion", "cobertura_acueducto"],
            "zonas": zonas or {"departamentos": ["68"], "municipios": ["68001", "05001"]},
        }
    )


def _origen(zona, anio):
    if anio >= 2024:
        return "proyeccion-regresion-lineal-4puntos"
    return "DANE-proyeccion-poblacion-2023" if zona["codigo"] == "05001" else "TerriData"


def _series(solicitud):
    return [
        {**zona, "anio": anio, "variable": variable, "origen": _origen(zona, anio), "comentario": "",
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
        grafica = reportes_pdf.GraficaLineasPDF("cobertura_acueducto", anios, datos, 400, 150)
        self.assertEqual(len(grafica.datos), 3)
        self.assertFalse(grafica.recortada)
        self.assertGreaterEqual(len(rs.PALETA_GRAFICA), rs.MAX_SERIES_LEYENDA)
        self.assertGreaterEqual(len(reportes_pdf.TRAZOS), rs.MAX_SERIES_LEYENDA)
        self.assertEqual(len(set(rs.PALETA_GRAFICA)), len(rs.PALETA_GRAFICA))

    def test_grafica_pdf_recorta_a_las_series_que_caben_en_la_leyenda(self):
        datos = [(f"Zona {i}", [1.0, 2.0]) for i in range(rs.MAX_SERIES_LEYENDA + 5)]
        grafica = reportes_pdf.GraficaLineasPDF("cobertura_acueducto", ["2020", "2021"], datos, 400, 150)
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
        with patch.object(reportes_pdf.GraficaLineasPDF, "_dibujar", side_effect=RuntimeError("boom")):
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "r.pdf"
                rs._crear_pdf(destino, solicitud, ZONAS, _series(solicitud))
                self.assertTrue(destino.read_bytes().startswith(b"%PDF"))


class PlantillasTest(unittest.TestCase):
    def test_plantilla_por_defecto_segun_el_formato(self):
        self.assertEqual(_solicitud("pdf").opciones.plantilla, "apa")
        self.assertEqual(_solicitud("excel").opciones.plantilla, "completo")

    def test_plantilla_incompatible_con_el_formato_se_rechaza(self):
        for formato, plantilla in (("pdf", "completo"), ("pdf", "comparativo"), ("excel", "apa"), ("excel", "ieee")):
            with self.assertRaises(ValidationError, msg=f"{formato}+{plantilla}"):
                _solicitud(formato, plantilla)
        with self.assertRaises(ValidationError):
            _solicitud("pdf", "mla")

    def test_apa_e_ieee_se_generan_con_y_sin_graficas_y_con_valores_no_finitos(self):
        for plantilla in ("apa", "ieee"):
            for graficas in (True, False):
                for valor in (None, float("nan"), float("inf")):
                    solicitud = _solicitud("pdf", plantilla)
                    solicitud.opciones.incluir_graficas = graficas
                    series = [{**f, "valor": f["valor"] if valor is None else valor} for f in _series(solicitud)]
                    with tempfile.TemporaryDirectory() as tmp:
                        destino = Path(tmp) / "r.pdf"
                        rs._crear_pdf(destino, solicitud, ZONAS, series, "Ana Pérez")
                        self.assertTrue(destino.read_bytes().startswith(b"%PDF"), f"{plantilla} graficas={graficas} valor={valor}")

    def test_apa_estructura_y_reglas_de_maquetacion(self):
        import pymupdf

        solicitud = _solicitud("pdf", "apa")
        solicitud.opciones.introduccion = "Introducción de prueba."
        solicitud.opciones.conclusiones = "Conclusión de prueba."
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "apa.pdf"
            rs._crear_pdf(destino, solicitud, ZONAS, _series(solicitud), "Ana Pérez")
            documento = pymupdf.open(destino)
            textos = [pagina.get_text() for pagina in documento]
            self.assertEqual((documento[0].rect.width, documento[0].rect.height), (612, 792))  # carta
            # portada: título, autor, afiliación y fecha; número de página en todas las páginas
            self.assertIn("Ana Pérez", textos[0])
            self.assertIn("Techo 360", textos[0])
            for numero, texto in enumerate(textos, start=1):
                self.assertEqual(texto.split()[0], str(numero), f"página {numero}: el número va arriba a la derecha")
            completo = "\n".join(textos)
            for esperado in ("Método", "Resultados", "Conclusiones", "Referencias", "Tabla 1", "Tabla 3", "Figura 1", "Nota.", "(s. f.)"):
                self.assertIn(esperado, completo)
            self.assertNotIn("Introducción\n", textos[1])  # la introducción de APA no lleva encabezado
            # fuente de 12 pt Times en el cuerpo
            fuentes = {(span["font"], round(span["size"])) for pagina in documento for bloque in pagina.get_text("dict")["blocks"] if "lines" in bloque for linea in bloque["lines"] for span in linea["spans"]}
            self.assertTrue(any("Times" in nombre and tamano == 12 for nombre, tamano in fuentes), fuentes)
            # referencias en orden alfabético
            referencias = textos[-1]
            orden = [referencias.find(titulo) for titulo in ("Proyecciones de población", "TerriData: Sistema", "Proyecciones de indicadores")]
            self.assertTrue(all(i >= 0 for i in orden), referencias)
            self.assertEqual(orden, sorted(orden))
            documento.close()

    def test_ieee_estructura_dos_columnas_y_referencias_numeradas(self):
        import pymupdf

        solicitud = _solicitud("pdf", "ieee")
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "ieee.pdf"
            rs._crear_pdf(destino, solicitud, ZONAS, _series(solicitud), "Ana Pérez")
            documento = pymupdf.open(destino)
            completo = "\n".join(pagina.get_text() for pagina in documento)
            for esperado in ("Resumen—", "Palabras clave—", "INTRODUCCI", "TABLA I", "Fig. 1.", "[1]", "[En línea]"):
                self.assertIn(esperado, completo.replace("\n", " ") if esperado == "I. " else completo)
            # dos columnas: los bloques de texto del cuerpo caen en dos x iniciales distintas
            xs = {round(bloque[0]) for bloque in documento[0].get_text("blocks") if bloque[6] == 0}
            self.assertTrue([x for x in xs if 40 <= x < 306], xs)
            self.assertTrue([x for x in xs if x >= 306], xs)
            documento.close()

    def test_excel_comparativo_una_hoja_por_variable_con_zonas_en_filas(self):
        import openpyxl

        solicitud = _solicitud("excel", "comparativo")
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "c.xlsx"
            rs._crear_excel(destino, solicitud, ZONAS, _series(solicitud))
            libro = openpyxl.load_workbook(destino)
            self.assertEqual(libro.sheetnames[0], "Resumen")
            self.assertEqual(len(libro.sheetnames), 1 + len(solicitud.variables))
            self.assertNotIn("Datos", libro.sheetnames)
            hoja = libro["Cobertura de Acueducto"]
            filas = list(hoja.iter_rows(values_only=True))
            self.assertEqual(filas[0][:3], ("Nivel", "Código", "Zona"))
            self.assertEqual(list(filas[0][3:9]), [2020, 2021, 2022, 2023, 2024, 2025])
            self.assertEqual(filas[0][9], "Variación del periodo")
            self.assertEqual(len(filas), 1 + len(ZONAS))
            self.assertEqual(len(hoja._charts), 1)
            santander = next(f for f in filas[1:] if f[1] == "68")
            self.assertIsNone(santander[5])  # 2022 sin dato
            self.assertAlmostEqual(santander[9], santander[8] - santander[3])

    def test_excel_comparativo_sin_graficas_no_agrega_graficas(self):
        import openpyxl

        solicitud = _solicitud("excel", "comparativo")
        solicitud.opciones.incluir_graficas = False
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "c.xlsx"
            rs._crear_excel(destino, solicitud, ZONAS, _series(solicitud))
            libro = openpyxl.load_workbook(destino)
            self.assertTrue(all(len(libro[nombre]._charts) == 0 for nombre in libro.sheetnames))

    def test_formato_de_valores_y_numeracion(self):
        self.assertEqual(reportes_pdf.formato_valor(None, "score_privacion"), "n.d.")
        self.assertEqual(reportes_pdf.formato_valor(0.3456, "score_privacion"), "0,35")
        self.assertEqual(reportes_pdf.formato_valor(1234.56, "densidad_poblacional"), "1.234,6")
        self.assertEqual(reportes_pdf.formato_valor(55.04, "cobertura_acueducto"), "55,0")
        self.assertEqual([reportes_pdf.romano(n) for n in (1, 4, 9, 14, 40)], ["I", "IV", "IX", "XIV", "XL"])
        self.assertEqual([reportes_pdf.letra(n) for n in (0, 25, 26, 27)], ["A", "Z", "AA", "AB"])

    def test_fuentes_se_detectan_por_origen(self):
        series = [{"origen": "TerriData"}, {"origen": "DANE-proyeccion-poblacion-2023"}, {"origen": "proyeccion-regresion-lineal-4puntos"}]
        self.assertEqual(reportes_pdf.detectar_fuentes(series), ["terridata", "dane", "techo360"])
        self.assertEqual(reportes_pdf.detectar_fuentes([{"origen": "TerriData-proyeccion"}]), ["techo360"])
        self.assertEqual(reportes_pdf.detectar_fuentes([{"origen": None}]), [])


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
