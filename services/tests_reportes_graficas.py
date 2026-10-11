"""Pruebas de las gráficas del reporte (catálogo, escenas, PDF/Excel con gráficas) y de la caché de 24 h,
sin BD ni Redis (dobles).

Uso:
    python -m services.tests_reportes_graficas
"""
import json
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from core.config import settings
from schemas.reporte import ReporteGenerarRequest
from services import reportes_cache, reportes_escenas as esc, reportes_service as rs
from services.reportes_graficas_catalogo import TIPOS, VARIABLES, validar_combinacion

ANIOS = list(range(2020, 2026))
NOMBRES = ["Bucaramanga, Santander", "Floridablanca, Santander", "Girón, Santander", "Medellín, Antioquia", "Bello, Antioquia", "Cali, Valle del Cauca", "Bogotá, D.C."]


def _zonas():
    zonas = [{"nivel": "municipio", "codigo": f"{10000 + i * 111}", "nombre": n.split(",")[0], "etiqueta": n} for i, n in enumerate(NOMBRES)]
    zonas.insert(0, {"nivel": "departamento", "codigo": "68", "nombre": "Santander", "etiqueta": "Santander"})
    return zonas


def _valor(variable, i):
    return {
        "score_privacion": 0.25 + 0.05 * (i % 8),
        "cobertura_acueducto": 95 - 3 * (i % 9),
        "cobertura_alcantarillado": 90 - 5 * (i % 8),
        "deficit_habitacional_cuantitativo": 6 + 2 * (i % 6),
        "deficit_habitacional_cualitativo": 12 + 3 * ((i * 3) % 7),
        "densidad_poblacional": 30 * (10 ** (i % 4)) + 7 * i,
    }[variable]


def _datos(con_geometria=True, con_nacional=True):
    zonas = _zonas()
    anual, medias, geometrias = {}, {}, {}
    for i, zona in enumerate(zonas):
        for variable in VARIABLES:
            serie = [round(_valor(variable, i) * (1 + 0.01 * k), 4) for k in range(len(ANIOS))]
            if i == 2 and variable == "cobertura_acueducto":
                serie[2] = None
            anual[(zona["codigo"], variable)] = serie
            presentes = [v for v in serie if v is not None]
            medias[(zona["codigo"], variable)] = sum(presentes) / len(presentes)
        if con_geometria:
            cx, cy = -74 + (i % 4) * 0.5, 5 + (i // 4) * 0.5
            anillo = [[cx + 0.2 * math.cos(a), cy + 0.18 * math.sin(a)] for a in [k * math.pi / 6 for k in range(12)]]
            anillo.append(anillo[0])
            geometrias[zona["codigo"]] = {"type": "Polygon", "coordinates": [anillo]}
    nacional = {}
    if con_nacional:
        for variable in VARIABLES:
            valores = sorted(_valor(variable, k) * 1.05 for k in range(60))
            q1, mediana, q3 = esc.statistics.quantiles(valores, n=4, method="inclusive")
            nacional[variable] = {"media": sum(valores) / len(valores), "min": valores[0], "q1": q1, "mediana": mediana, "q3": q3, "max": valores[-1], "n": len(valores)}
    return esc.DatosGraficas(anios=ANIOS, zonas=zonas, medias=medias, anual=anual, nacional=nacional, geometrias=geometrias)


def _spec(tipo, modo, variables, zonas=(), id_="g1"):
    return SimpleNamespace(id=id_, tipo=tipo, modo=modo, variables=list(variables), zonas=list(zonas), titulo="")


CASOS = [
    ("coropletico", "general", ["score_privacion"], []),
    ("barras_ranking", "general", ["score_privacion"], []),
    ("barras_agrupadas", "comparar", ["cobertura_acueducto", "cobertura_alcantarillado"], ["10111", "10222", "10333"]),
    ("barras_agrupadas", "individual", ["cobertura_acueducto", "cobertura_alcantarillado"], ["10111"]),
    ("barras_apiladas", "general", ["deficit_habitacional_cuantitativo", "deficit_habitacional_cualitativo"], []),
    ("barras_apiladas", "comparar", ["cobertura_acueducto"], ["10111", "10222"]),
    ("lineas", "comparar", ["cobertura_acueducto"], ["10111", "10222", "10333"]),
    ("radar", "comparar", ["score_privacion", "cobertura_acueducto", "cobertura_alcantarillado", "densidad_poblacional"], ["10111", "10222"]),
    ("dispersion", "general", ["score_privacion", "cobertura_acueducto", "densidad_poblacional"], []),
    ("histograma", "general", ["cobertura_acueducto"], []),
    ("boxplot", "general", ["score_privacion"], []),
    ("bullet", "individual", ["cobertura_acueducto"], ["10111"]),
    ("gauge", "individual", ["score_privacion"], ["10111"]),
    ("dumbbell", "general", ["cobertura_acueducto", "cobertura_alcantarillado"], []),
    ("donut", "individual", ["deficit_habitacional_cuantitativo", "deficit_habitacional_cualitativo"], ["10111"]),
    ("treemap", "general", ["deficit_habitacional_cuantitativo"], []),
    ("correlaciones", "general", ["score_privacion", "cobertura_acueducto", "cobertura_alcantarillado"], []),
    ("paneles", "general", ["score_privacion", "cobertura_acueducto", "densidad_poblacional"], []),
]


class CatalogoTest(unittest.TestCase):
    def test_todos_los_tipos_tienen_constructor_y_un_caso_valido(self):
        self.assertEqual(set(esc.CONSTRUCTORES), set(TIPOS))
        self.assertEqual({caso[0] for caso in CASOS}, set(TIPOS), "cada tipo debe tener al menos un caso en CASOS")
        for tipo, modo, variables, zonas in CASOS:
            error = validar_combinacion(tipo, modo, variables, 8 if modo == "general" else len(zonas))
            self.assertIsNone(error, f"{tipo}/{modo}: {error}")

    def test_reglas_de_compatibilidad(self):
        incompatibles = [
            ("barras_agrupadas", "general", ["score_privacion", "densidad_poblacional"], 8),  # unidades distintas
            ("barras_agrupadas", "general", ["cobertura_acueducto"], 8),  # una sola variable
            ("barras_apiladas", "general", ["cobertura_acueducto", "cobertura_alcantarillado"], 8),  # no son los déficits
            ("barras_apiladas", "general", ["score_privacion"], 8),
            ("donut", "individual", ["cobertura_acueducto", "cobertura_alcantarillado"], 1),
            ("gauge", "individual", ["densidad_poblacional"], 1),
            ("histograma", "individual", ["score_privacion"], 1),  # solo modo general
            ("bullet", "comparar", ["score_privacion"], 3),
            ("radar", "comparar", ["score_privacion", "cobertura_acueducto"], 2),  # mínimo 3 variables
            ("correlaciones", "general", ["score_privacion", "cobertura_acueducto", "cobertura_alcantarillado"], 2),  # pocas zonas
            ("lineas", "comparar", ["score_privacion"], 1),  # comparar necesita 2+
            ("lineas", "comparar", ["score_privacion"], 11),  # y como máximo 10
            ("lineas", "individual", ["score_privacion"], 2),
            ("no_existe", "general", ["score_privacion"], 3),
        ]
        for tipo, modo, variables, zonas in incompatibles:
            self.assertIsNotNone(validar_combinacion(tipo, modo, variables, zonas), f"{tipo}/{modo}/{variables}/{zonas} debería rechazarse")

    def test_la_solicitud_valida_graficas_zonas_e_ids(self):
        base = {"formato": "pdf", "periodo": {"anio_inicio": 2020, "anio_fin": 2025}, "variables": ["score_privacion"], "zonas": {"departamentos": ["68"], "municipios": ["68001", "05001"]}}
        ok = {**base, "graficas": [{"id": "a", "tipo": "coropletico", "modo": "general", "variables": ["score_privacion"]}, {"id": "b", "tipo": "lineas", "modo": "individual", "variables": ["score_privacion"], "zonas": ["68001"]}]}
        self.assertEqual([g.id for g in ReporteGenerarRequest.model_validate(ok).graficas], ["a", "b"])
        malos = [
            {"id": "a", "tipo": "lineas", "modo": "individual", "variables": ["score_privacion"], "zonas": ["99999"]},  # zona no seleccionada
            {"id": "a", "tipo": "barras_agrupadas", "modo": "general", "variables": ["score_privacion", "densidad_poblacional"]},  # mezcla unidades
            {"id": "a b", "tipo": "coropletico", "modo": "general", "variables": ["score_privacion"]},  # id inválido
        ]
        for grafica in malos:
            with self.assertRaises(ValidationError):
                ReporteGenerarRequest.model_validate({**base, "graficas": [grafica]})
        with self.assertRaises(ValidationError):  # ids repetidos
            ReporteGenerarRequest.model_validate({**base, "graficas": [{"id": "a", "tipo": "coropletico", "modo": "general", "variables": ["score_privacion"]}] * 2})
        # el orden elegido se conserva
        ordenado = {**base, "graficas": [{"id": i, "tipo": "coropletico", "modo": "general", "variables": ["score_privacion"]} for i in ("z", "m", "a")]}
        self.assertEqual([g.id for g in ReporteGenerarRequest.model_validate(ordenado).graficas], ["z", "m", "a"])


class EscenasTest(unittest.TestCase):
    def test_cada_tipo_construye_una_escena_valida_y_serializable(self):
        datos = _datos()
        for tipo, modo, variables, zonas in CASOS:
            for ancho in (560, 360):
                with self.subTest(tipo=tipo, modo=modo, ancho=ancho):
                    resultado = esc.construir_escena(_spec(tipo, modo, variables, zonas), datos, ancho)
                    escena = resultado.escena
                    self.assertGreater(len(escena["elementos"]), 4, "escena vacía o de aviso")
                    self.assertGreater(escena["alto"], 40)
                    self.assertEqual(escena["ancho"], ancho)
                    self.assertTrue(resultado.titulo)
                    json.dumps(resultado.escena)  # serializable (viaja a la vista previa)
                    self.assertTrue(resultado.tabla["encabezados"])
                    self.assertTrue({e["t"] for e in escena["elementos"]} <= {"rect", "line", "poly", "circle", "text"})
                    for e in escena["elementos"]:  # todo dentro del lienzo (con margen para rótulos)
                        for clave in ("x", "cx"):
                            if clave in e:
                                self.assertGreaterEqual(e[clave], -2, f"{e}")
                                self.assertLessEqual(e[clave], ancho + 2, f"{e}")

    def test_sin_datos_devuelve_una_escena_de_aviso_y_no_falla(self):
        datos = _datos()
        vacios = esc.DatosGraficas(anios=ANIOS, zonas=datos.zonas, medias={}, anual={}, nacional={}, geometrias={})
        for tipo, modo, variables, zonas in CASOS:
            with self.subTest(tipo=tipo):
                resultado = esc.construir_escena(_spec(tipo, modo, variables, zonas), vacios, 560)
                textos = [e["s"] for e in resultado.escena["elementos"] if e["t"] == "text"]
                self.assertTrue(resultado.escena["elementos"])
                self.assertTrue(any("Sin" in t or "Faltan" in t or "No se pudo" in t for t in textos) or resultado.escena["elementos"], textos)

    def test_el_ranking_ordena_de_mayor_a_menor_y_marca_el_promedio_nacional(self):
        datos = _datos()
        resultado = esc.construir_escena(_spec("barras_ranking", "general", ["score_privacion"]), datos, 560)
        valores = [fila[1] for fila in resultado.tabla["filas"]]
        self.assertEqual(valores, sorted(valores, reverse=True))
        textos = " ".join(e["s"] for e in resultado.escena["elementos"] if e["t"] == "text")
        self.assertIn("Prom. nacional", textos)

    def test_el_ranking_limita_a_20_filas_y_lo_avisa(self):
        zonas = [{"nivel": "municipio", "codigo": f"{20000 + i}", "nombre": f"M{i}", "etiqueta": f"M{i}, D"} for i in range(45)]
        medias = {(z["codigo"], "score_privacion"): i / 50 for i, z in enumerate(zonas)}
        datos = esc.DatosGraficas(anios=ANIOS, zonas=zonas, medias=medias, anual={}, nacional={})
        resultado = esc.construir_escena(_spec("barras_ranking", "general", ["score_privacion"]), datos, 560)
        self.assertIn("20 de 45", resultado.nota)
        self.assertEqual(len(resultado.tabla["filas"]), 45)  # la tabla (Excel) conserva todas

    def test_formato_numerico_en_espanol(self):
        self.assertEqual(esc.num_es(1234.5, 1), "1.234,5")
        self.assertEqual(esc.fmt(88.04, "cobertura_acueducto", True), "88,0 %")  # % con espacio
        self.assertEqual(esc.fmt(0.4567, "score_privacion"), "0,46")
        self.assertEqual(esc.fmt(None, "score_privacion"), "n.d.")

    def test_no_se_mezclan_unidades_en_un_eje_las_variables_distintas_van_a_paneles(self):
        resultado = esc.construir_escena(_spec("paneles", "general", ["score_privacion", "densidad_poblacional", "cobertura_acueducto"]), _datos(), 560)
        titulos = [e["s"] for e in resultado.escena["elementos"] if e["t"] == "text" and e.get("bold")]
        self.assertEqual(len(titulos), 3)

    def test_dispersion_usa_escala_logaritmica_con_densidades_muy_dispares(self):
        zonas = _zonas()[1:6]
        medias = {}
        for i, z in enumerate(zonas):
            medias[(z["codigo"], "densidad_poblacional")] = 10 ** (i + 1)
            medias[(z["codigo"], "score_privacion")] = 0.2 + 0.1 * i
        datos = esc.DatosGraficas(anios=ANIOS, zonas=zonas, medias=medias, anual={}, nacional={})
        resultado = esc.construir_escena(_spec("dispersion", "general", ["densidad_poblacional", "score_privacion"]), datos, 560)
        textos = " ".join(e["s"] for e in resultado.escena["elementos"] if e["t"] == "text")
        self.assertIn("escala log", textos)

    def test_pearson(self):
        self.assertAlmostEqual(esc.pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(esc.pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0)
        self.assertIsNone(esc.pearson([1, 1, 1], [1, 2, 3]))
        self.assertIsNone(esc.pearson([1, 2], [1, 2]))

    def test_el_coropletico_dibuja_un_poligono_por_zona_con_geometria(self):
        datos = _datos()
        resultado = esc.construir_escena(_spec("coropletico", "general", ["score_privacion"]), datos, 560)
        poligonos = [e for e in resultado.escena["elementos"] if e["t"] == "poly" and e.get("cerrado")]
        self.assertEqual(len(poligonos), len(datos.zonas))
        sin_geometria = esc.DatosGraficas(anios=ANIOS, zonas=datos.zonas, medias=datos.medias, anual=datos.anual, nacional={}, geometrias={})
        aviso = esc.construir_escena(_spec("coropletico", "general", ["score_privacion"]), sin_geometria, 560)
        self.assertTrue(any("geometr" in e.get("s", "") for e in aviso.escena["elementos"]))


def _solicitud(formato, plantilla, graficas):
    return ReporteGenerarRequest.model_validate(
        {
            "formato": formato,
            "periodo": {"anio_inicio": 2020, "anio_fin": 2025},
            "opciones": {"plantilla": plantilla},
            "variables": ["score_privacion", "cobertura_acueducto"],
            "zonas": {"departamentos": ["68"], "municipios": [z["codigo"] for z in _zonas()[1:]]},
            "graficas": graficas,
        }
    )


def _series_de(datos):
    return [
        {**zona, "anio": anio, "variable": variable, "valor": datos.anual[(zona["codigo"], variable)][i], "origen": "TerriData", "comentario": ""}
        for zona in datos.zonas
        for variable in ("score_privacion", "cobertura_acueducto")
        for i, anio in enumerate(datos.anios)
    ]


class DocumentosConGraficasTest(unittest.TestCase):
    def _graficas(self):
        return [
            {"id": f"g{i}", "tipo": tipo, "modo": modo, "variables": variables, "zonas": [z for z in zonas if z in {zz["codigo"] for zz in _zonas()}]}
            for i, (tipo, modo, variables, zonas) in enumerate(CASOS)
        ]

    def test_pdf_apa_e_ieee_incluyen_todas_las_figuras_en_el_orden_elegido(self):
        import pymupdf

        datos = _datos()
        for plantilla, ancho in (("apa", 560), ("ieee", 360)):
            solicitud = _solicitud("pdf", plantilla, self._graficas())
            escenas = [(spec, esc.construir_escena(spec, datos, ancho)) for spec in solicitud.graficas]
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "r.pdf"
                rs._crear_pdf(destino, solicitud, datos.zonas, _series_de(datos), "Ana Pérez", escenas)
                documento = pymupdf.open(destino)
                texto = " ".join(" ".join(pagina.get_text().split()) for pagina in documento)
                documento.close()
            etiqueta = "Figura" if plantilla == "apa" else "Fig."
            self.assertIn(f"{etiqueta} 1", texto)
            self.assertIn(f"{etiqueta} {len(CASOS)}", texto)
            self.assertNotIn(f"{etiqueta} {len(CASOS) + 1}", texto)
            # cada rótulo «Figura n» va seguido del título de la n-ésima gráfica de la lista, en orden creciente
            ultimo = -1
            for numero, (_, resultado) in enumerate(escenas, start=1):
                titulo = " ".join(resultado.titulo.split())
                if plantilla == "apa":
                    rotulo = texto.find(f"Figura {numero} ", ultimo + 1)
                else:
                    rotulo = texto.find(f"Fig. {numero}. ", ultimo + 1)
                self.assertGreater(rotulo, ultimo, f"{plantilla}: falta el rótulo de la figura {numero}")
                self.assertIn(titulo, texto[rotulo : rotulo + 400], f"{plantilla}: figura {numero}")
                ultimo = rotulo

    def test_el_orden_de_la_lista_es_el_orden_del_documento(self):
        import pymupdf

        datos = _datos()
        solicitud = _solicitud("pdf", "apa", [
            {"id": "a", "tipo": "barras_ranking", "modo": "general", "variables": ["score_privacion"]},
            {"id": "b", "tipo": "histograma", "modo": "general", "variables": ["cobertura_acueducto"]},
        ])
        for orden in (solicitud.graficas, list(reversed(solicitud.graficas))):
            escenas = [(spec, esc.construir_escena(spec, datos, 560)) for spec in orden]
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "o.pdf"
                rs._crear_pdf(destino, solicitud, datos.zonas, _series_de(datos), None, escenas)
                documento = pymupdf.open(destino)
                texto = "\n".join(p.get_text() for p in documento)
                documento.close()
            primero, segundo = (escenas[0][1].titulo, escenas[1][1].titulo)
            self.assertLess(texto.find(primero), texto.find(segundo))

    def test_excel_agrega_una_hoja_por_grafica_con_su_tabla_y_grafica_nativa_cuando_existe(self):
        import openpyxl

        datos = _datos()
        for plantilla in ("completo", "comparativo"):
            solicitud = _solicitud("excel", plantilla, self._graficas())
            escenas = [(spec, esc.construir_escena(spec, datos, 560)) for spec in solicitud.graficas]
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "g.xlsx"
                rs._crear_excel(destino, solicitud, datos.zonas, _series_de(datos), escenas)
                libro = openpyxl.load_workbook(destino)
                hojas = [n for n in libro.sheetnames if n.startswith("Gráfica ")]
                self.assertEqual(len(hojas), len(CASOS), plantilla)
                nativas = {spec.tipo: len(libro[f"Gráfica {i}"]._charts) for i, (spec, _) in enumerate(escenas, start=1)}
                for tipo in ("barras_ranking", "barras_agrupadas", "barras_apiladas", "lineas", "dispersion", "histograma", "donut"):
                    self.assertEqual(nativas[tipo], 1, f"{plantilla}: {tipo} debería traer su gráfica nativa")
                for tipo in ("coropletico", "radar", "boxplot", "treemap", "correlaciones"):
                    self.assertEqual(nativas[tipo], 0, tipo)
                hoja = libro["Gráfica 2"]
                self.assertEqual(hoja["A3"].value, "Zona")

    def test_sin_lista_de_graficas_y_con_incluir_graficas_se_conserva_el_comportamiento_anterior(self):
        import pymupdf

        datos = _datos()
        solicitud = _solicitud("pdf", "apa", [])
        solicitud.opciones.incluir_graficas = True
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "legado.pdf"
            rs._crear_pdf(destino, solicitud, datos.zonas, _series_de(datos), None, [])
            documento = pymupdf.open(destino)
            texto = "\n".join(p.get_text() for p in documento)
            documento.close()
        self.assertIn("Evolución de los Indicadores", texto)


class FakeRedis:
    """Doble mínimo de redis-py (solo lo que usa CacheReportes)."""

    def __init__(self, usado=1_000_000, falla=False):
        self.datos, self.indices, self.usado, self.falla = {}, {}, usado, falla

    def _revisar(self):
        if self.falla:
            import redis

            raise redis.ConnectionError("caído")

    def get(self, clave):
        self._revisar()
        return self.datos.get(clave)

    def exists(self, clave):
        self._revisar()
        return clave in self.datos

    def info(self, seccion):
        self._revisar()
        return {"used_memory": self.usado}

    def delete(self, *claves):
        for clave in claves:
            self.datos.pop(clave, None)

    def zcard(self, clave):
        return len(self.indices.get(clave, {}))

    def zrange(self, clave, inicio, fin):
        ordenados = sorted(self.indices.get(clave, {}), key=lambda h: self.indices[clave][h])
        return [h.encode() for h in ordenados[inicio : fin + 1]]

    def zrem(self, clave, *miembros):
        for miembro in miembros:
            self.indices.get(clave, {}).pop(miembro.decode() if isinstance(miembro, bytes) else miembro, None)

    def pipeline(self):
        cliente = self

        class Tuberia:
            def __init__(self):
                self.pendientes = []

            def set(self, clave, valor, ex=None):
                self.pendientes.append(lambda: cliente.datos.__setitem__(clave, valor))

            def zadd(self, clave, mapa):
                self.pendientes.append(lambda: cliente.indices.setdefault(clave, {}).update(mapa))

            def expire(self, clave, ttl):
                pass

            def execute(self):
                cliente._revisar()
                for pendiente in self.pendientes:
                    pendiente()

        return Tuberia()


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.original = (settings.reportes_cache_activa, settings.reportes_cache_max_archivo, settings.reportes_cache_por_usuario, settings.reportes_cache_redis_max_usado)
        settings.reportes_cache_activa = True

    def tearDown(self):
        (settings.reportes_cache_activa, settings.reportes_cache_max_archivo, settings.reportes_cache_por_usuario, settings.reportes_cache_redis_max_usado) = self.original

    def test_la_huella_depende_de_la_configuracion_el_usuario_y_el_dia_pero_no_de_reemplaza(self):
        base = {"formato": "pdf", "variables": ["score_privacion"], "reemplaza": 5}
        h = reportes_cache.huella_de(base, 1, date(2026, 10, 11))
        self.assertEqual(h, reportes_cache.huella_de({**base, "reemplaza": None}, 1, date(2026, 10, 11)))
        self.assertEqual(h, reportes_cache.huella_de({"variables": ["score_privacion"], "formato": "pdf"}, 1, date(2026, 10, 11)))  # orden de claves
        self.assertNotEqual(h, reportes_cache.huella_de(base, 2, date(2026, 10, 11)))  # otro usuario
        self.assertNotEqual(h, reportes_cache.huella_de(base, 1, date(2026, 10, 12)))  # otro día
        self.assertNotEqual(h, reportes_cache.huella_de({**base, "formato": "excel"}, 1, date(2026, 10, 11)))
        self.assertEqual(len(h), 64)

    def test_guardar_y_obtener(self):
        cache = reportes_cache.CacheReportes(lambda: FakeRedis())
        self.assertFalse(cache.existe("abc"))
        self.assertTrue(cache.guardar("abc", 1, b"%PDF-contenido"))
        self.assertTrue(cache.existe("abc"))
        self.assertEqual(cache.obtener("abc"), b"%PDF-contenido")
        cache.borrar("abc")
        self.assertIsNone(cache.obtener("abc"))

    def test_no_cachea_archivos_grandes_ni_cuando_redis_esta_lleno_ni_desactivada(self):
        settings.reportes_cache_max_archivo = 10
        cache = reportes_cache.CacheReportes(lambda: FakeRedis())
        self.assertFalse(cache.guardar("grande", 1, b"x" * 11))
        settings.reportes_cache_max_archivo = 1000
        lleno = reportes_cache.CacheReportes(lambda: FakeRedis(usado=settings.reportes_cache_redis_max_usado))
        self.assertFalse(lleno.guardar("lleno", 1, b"x"))
        settings.reportes_cache_activa = False
        self.assertFalse(cache.guardar("off", 1, b"x"))
        self.assertIsNone(cache.obtener("off"))
        self.assertFalse(cache.existe("off"))

    def test_conserva_como_maximo_n_reportes_por_usuario_descartando_el_mas_antiguo(self):
        settings.reportes_cache_por_usuario = 3
        falso = FakeRedis()
        cache = reportes_cache.CacheReportes(lambda: falso)
        for numero in range(5):
            cache.guardar(f"h{numero}", 7, b"x")
        self.assertEqual([cache.existe(f"h{n}") for n in range(5)], [False, False, True, True, True])
        cache.guardar("otro", 8, b"x")  # otro usuario no afecta
        self.assertTrue(cache.existe("h4"))

    def test_si_redis_falla_no_lanza_y_el_flujo_sigue_con_disco(self):
        cache = reportes_cache.CacheReportes(lambda: FakeRedis(falla=True))
        self.assertFalse(cache.guardar("a", 1, b"x"))
        self.assertIsNone(cache.obtener("a"))
        self.assertFalse(cache.existe("a"))
        cache.borrar("a")


if __name__ == "__main__":
    unittest.main()
