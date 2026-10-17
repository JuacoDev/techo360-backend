"""Pruebas de esquema del bloque de grafica del chatbot (paso 2.4-B), sin base ni red.

Cubren los casos 1, 2, 3, 5, parte del 4 y 8 de la seccion 7 de claude/fase2-3-contrato-grafica.md.
Quedan para el paso 4.6: codigo inexistente en zonas_municipio, abstencion por datos, sanitizado del
titulo (4.7) y la coherencia con el validador de cifras.

Uso:
    python -m unittest schemas.tests_chatbot_grafica
"""
import json
import re
import unittest
from pathlib import Path

from schemas.chatbot_grafica import ErrorGrafica, extraer_bloque, validar_bloque_json, validar_especificacion
from services.reportes_graficas_catalogo import GRUPO_UNIDAD, MAX_ZONAS_COMPARAR, TIPOS, VARIABLES, validar_combinacion

ZONAS = ["68001", "05001", "11001", "76001", "08001", "13001", "17001", "20001", "23001", "25001"]
RUTA_CATALOGO_FRONTEND = (
    Path(__file__).resolve().parents[2] / "techo360-frontend" / "src" / "pages" / "Reportes" / "graficasCatalogo.js"
)


def _spec(**cambios):
    base = {"v": 1, "tipo": "lineas", "modo": "individual", "variables": ["cobertura_acueducto"],
            "zonas": ["68001"], "anios": [2019, 2024]}
    base.update(cambios)
    return base


def _variables_validas(tipo):
    """Variables que el catalogo admite para el tipo (en la cantidad minima)."""
    definicion = TIPOS[tipo]
    candidatas = [v for v in VARIABLES if definicion.permitidas is None or v in definicion.permitidas]
    if definicion.misma_unidad:
        candidatas = [v for v in candidatas if GRUPO_UNIDAD[v] == "porcentaje"]
    if tipo == "donut":
        candidatas = ["deficit_habitacional_cuantitativo", "deficit_habitacional_cualitativo"]
    if tipo == "barras_apiladas":
        candidatas = ["deficit_habitacional_cuantitativo", "deficit_habitacional_cualitativo"]
    return candidatas[: max(definicion.min_variables, 1)]


def _zonas_validas(tipo, modo):
    if modo == "individual":
        return ZONAS[:1]
    if modo == "comparar":
        return ZONAS[:2]
    return ZONAS[: max(TIPOS[tipo].min_zonas_general, 2)]


def _anios_validos(tipo):
    return [2019, 2024]


class BloquesValidos(unittest.TestCase):
    def test_subconjunto_de_0_4_1(self):
        casos = [
            _spec(),
            _spec(tipo="barras_ranking", modo="comparar", zonas=["68001", "05001"], anios=[2024]),
            _spec(tipo="barras_agrupadas", variables=["cobertura_acueducto", "cobertura_alcantarillado"], anios=[2024]),
            _spec(tipo="gauge", anios=[2024]),
            _spec(tipo="bullet", anios=[2024]),
        ]
        for c in casos:
            with self.subTest(c["tipo"]):
                self.assertEqual(validar_especificacion(c).tipo, c["tipo"])

    def test_los_16_tipos_en_todos_sus_modos(self):
        self.assertEqual(len(TIPOS), 16)
        for tipo, definicion in TIPOS.items():
            for modo in sorted(definicion.modos):
                with self.subTest(f"{tipo}/{modo}"):
                    spec = _spec(tipo=tipo, modo=modo, variables=_variables_validas(tipo),
                                 zonas=_zonas_validas(tipo, modo), anios=_anios_validos(tipo))
                    self.assertEqual(validar_especificacion(spec).modo, modo)

    def test_titulo_opcional(self):
        self.assertIsNone(validar_especificacion(_spec()).titulo)
        self.assertEqual(validar_especificacion(_spec(titulo="Cobertura en Bucaramanga")).titulo, "Cobertura en Bucaramanga")

    def test_bloque_en_texto(self):
        texto = "Aqui va la grafica.\n```grafica\n" + json.dumps(_spec()) + "\n```\nFin."
        self.assertEqual(validar_bloque_json(extraer_bloque(texto)).tipo, "lineas")


class CombinacionesRechazadas(unittest.TestCase):
    def _causa_y_mensaje(self, spec):
        with self.assertRaises(ErrorGrafica) as cm:
            validar_especificacion(spec)
        return cm.exception.causa, cm.exception.detail

    def test_modo_no_admitido_por_el_tipo(self):
        for tipo, definicion in TIPOS.items():
            for modo in ("individual", "comparar", "general"):
                if modo in definicion.modos:
                    continue
                with self.subTest(f"{tipo}/{modo}"):
                    zonas = _zonas_validas(tipo, "general") if modo == "general" else ZONAS[: (1 if modo == "individual" else 2)]
                    spec = _spec(tipo=tipo, modo=modo, variables=_variables_validas(tipo), zonas=zonas)
                    causa, detalle = self._causa_y_mensaje(spec)
                    self.assertEqual(causa, "combinacion")
                    self.assertEqual(detalle, validar_combinacion(tipo, modo, spec["variables"], len(zonas)))

    def test_el_mensaje_es_el_del_catalogo(self):
        casos = [
            _spec(tipo="gauge", variables=["densidad_poblacional"]),
            _spec(tipo="donut", variables=["cobertura_acueducto", "cobertura_alcantarillado"]),
            _spec(tipo="barras_agrupadas", variables=["cobertura_acueducto", "densidad_poblacional"]),
            _spec(tipo="lineas", variables=["cobertura_acueducto", "cobertura_alcantarillado"]),
            _spec(tipo="radar", variables=["score_privacion", "cobertura_acueducto"], modo="comparar", zonas=["68001", "05001"]),
            _spec(modo="comparar", zonas=["68001"]),
            _spec(modo="individual", zonas=["68001", "05001"]),
            _spec(tipo="barras_apiladas", variables=["cobertura_acueducto", "cobertura_alcantarillado"]),
        ]
        for c in casos:
            with self.subTest(f"{c['tipo']}/{c['modo']}"):
                causa, detalle = self._causa_y_mensaje(c)
                self.assertEqual(causa, "combinacion")
                self.assertEqual(detalle, validar_combinacion(c["tipo"], c["modo"], c["variables"], len(c["zonas"])))
                self.assertIsNotNone(detalle)

    def test_tipo_desconocido(self):
        causa, detalle = self._causa_y_mensaje(_spec(tipo="pastel"))
        self.assertEqual(causa, "combinacion")
        self.assertIn("desconocido", detalle)

    def test_minimo_de_zonas_en_modo_general(self):
        causa, _ = self._causa_y_mensaje(_spec(tipo="correlaciones", modo="general", variables=VARIABLES[:3], zonas=ZONAS[:3]))
        self.assertEqual(causa, "combinacion")


class EstructuraRechazada(unittest.TestCase):
    def _estructura(self, spec):
        with self.assertRaises(ErrorGrafica) as cm:
            validar_especificacion(spec)
        self.assertEqual(cm.exception.causa, "estructura")
        return cm.exception.detail

    def test_clave_desconocida(self):
        self._estructura(_spec(valores=[1, 2, 3]))
        self._estructura(_spec(color="rojo"))

    def test_el_bloque_no_trae_valores_de_datos(self):
        self._estructura(_spec(datos={"68001": 94.2}))

    def test_version_distinta_de_1(self):
        for v in (0, 2, True, "1", 1.0, None):
            with self.subTest(repr(v)):
                self._estructura(_spec(v=v))

    def test_falta_un_campo(self):
        for clave in ("v", "tipo", "modo", "variables", "zonas", "anios"):
            with self.subTest(clave):
                spec = _spec()
                del spec[clave]
                self._estructura(spec)

    def test_variable_fuera_del_catalogo(self):
        self._estructura(_spec(variables=["pobreza_total"]))

    def test_no_es_objeto(self):
        for payload in ([], "x", 5, None):
            with self.subTest(repr(payload)):
                self._estructura(payload)

    def test_titulo_demasiado_largo_o_con_marcado(self):
        self._estructura(_spec(titulo="x" * 81))
        validar_especificacion(_spec(titulo="x" * 80))
        for titulo in ("<b>hola</b>", "**negrita**", "`codigo`", "# titulo", "[a](b)"):
            with self.subTest(titulo):
                self._estructura(_spec(titulo=titulo))


class Zonas(unittest.TestCase):
    def _estructura(self, spec):
        with self.assertRaises(ErrorGrafica) as cm:
            validar_especificacion(spec)
        self.assertEqual(cm.exception.causa, "estructura")

    def test_nombre_en_lugar_de_codigo(self):
        self._estructura(_spec(zonas=["Bucaramanga"]))

    def test_codigo_mal_formado(self):
        for zona in ("5001", "680011", "6800a", 68001, None, ""):
            with self.subTest(repr(zona)):
                self._estructura(_spec(zonas=[zona]))

    def test_mas_de_diez_zonas(self):
        spec = _spec(modo="comparar", zonas=[f"{50000 + i}" for i in range(MAX_ZONAS_COMPARAR + 1)])
        self._estructura(spec)
        validar_especificacion(_spec(modo="comparar", zonas=[f"{50000 + i}" for i in range(MAX_ZONAS_COMPARAR)]))

    def test_sin_zonas(self):
        self._estructura(_spec(zonas=[]))


class Anios(unittest.TestCase):
    def _estructura(self, spec):
        with self.assertRaises(ErrorGrafica) as cm:
            validar_especificacion(spec)
        self.assertEqual(cm.exception.causa, "estructura")

    def test_desordenados(self):
        self._estructura(_spec(anios=[2024, 2019]))

    def test_repetidos(self):
        self._estructura(_spec(anios=[2019, 2019, 2024]))

    def test_fuera_de_rango(self):
        self._estructura(_spec(anios=[2017, 2024]))
        self._estructura(_spec(anios=[2019, 2043]))
        validar_especificacion(_spec(anios=[2018, 2042]))

    def test_tipos_no_enteros(self):
        for anios in ([2019.5, 2024], ["2019", "2024"], [True, 2024]):
            with self.subTest(repr(anios)):
                self._estructura(_spec(anios=anios))

    def test_mas_de_25(self):
        self._estructura(_spec(anios=list(range(2018, 2044))))  # 26 valores, uno fuera de rango
        self._estructura(_spec(anios=list(range(2018, 2043)) + [2043]))
        validar_especificacion(_spec(anios=list(range(2018, 2043))))  # 25 valores

    def test_lineas_con_un_solo_anio(self):
        self._estructura(_spec(anios=[2024]))
        self._estructura(_spec(anios=[]))
        validar_especificacion(_spec(tipo="gauge", anios=[2024]))


class ExtraccionDelBloque(unittest.TestCase):
    def test_sin_bloque(self):
        self.assertIsNone(extraer_bloque("Respuesta sin grafica."))

    def test_json_roto(self):
        texto = "```grafica\n{\"v\":1,\"tipo\":\n```"
        with self.assertRaises(ErrorGrafica) as cm:
            validar_bloque_json(extraer_bloque(texto))
        self.assertEqual(cm.exception.causa, "json_invalido")

    def test_dos_bloques_usa_el_primero(self):
        primero = _spec()
        segundo = _spec(tipo="gauge", anios=[2024])
        texto = f"```grafica\n{json.dumps(primero)}\n```\ntexto\n```grafica\n{json.dumps(segundo)}\n```"
        self.assertEqual(validar_bloque_json(extraer_bloque(texto)).tipo, "lineas")

    def test_bloque_sin_cierre(self):
        with self.assertRaises(ErrorGrafica) as cm:
            extraer_bloque("```grafica\n" + json.dumps(_spec()))
        self.assertEqual(cm.exception.causa, "bloque_sin_cierre")

    def test_otra_etiqueta_no_es_bloque_de_grafica(self):
        self.assertIsNone(extraer_bloque("```json\n{}\n```"))


class ParidadConElFrontend(unittest.TestCase):
    def test_los_16_ids_del_backend_son_los_del_frontend(self):
        if not RUTA_CATALOGO_FRONTEND.exists():
            self.skipTest("techo360-frontend no esta junto a techo360-backend")
        texto = RUTA_CATALOGO_FRONTEND.read_text(encoding="utf-8")
        ids_frontend = re.findall(r'\{ id: "([a-z_]+)", nombre: "[^"]*", icono:', texto)
        self.assertEqual(len(ids_frontend), 16)
        self.assertEqual(len(set(ids_frontend)), 16)
        self.assertEqual(set(ids_frontend), set(TIPOS))


if __name__ == "__main__":
    unittest.main()
