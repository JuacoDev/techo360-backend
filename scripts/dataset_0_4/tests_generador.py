"""Pruebas del generador de pares 0.4.0 (5.2).

Dos grupos:
  * puras (siempre corren): redaccion, similitud, controles, derivados, tokenizador si `llama-tokenize` existe.
  * con base de datos (solo lectura): una por subfamilia con un ejemplo fijo, la prueba dorada, la deduplicacion y el
    determinismo. Corren solo si TECHO360_PRUEBAS_BD=1 (usan DATABASE_URL de techo360-backend/.env en solo lectura y el
    repo techo360-proyeccion junto al backend).

Uso (desde techo360-backend/):
    python -m unittest scripts.dataset_0_4.tests_generador
    TECHO360_PRUEBAS_BD=1 python -m unittest scripts.dataset_0_4.tests_generador
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from . import controles, lengua as lg, resp_a, resp_b, resp_c, similitud
from .familias import Candidato
from .resp_comun import Eleccion, texto_fiabilidad

import random

CON_BD = os.environ.get("TECHO360_PRUEBAS_BD") == "1"
DIR_PROYECCION = Path(__file__).resolve().parents[3] / "techo360-proyeccion"


def dato(variable="cobertura_acueducto", anio=2022, valor=94.2, real=True, fiab=None, mun="Bucaramanga", dep="Santander", impl=False):
    from services.orquestador import contexto as cx
    return lg.Dato("c1", variable, "68001", mun, dep, anio, impl, valor, cx.formato_valor(variable, valor), real, fiab, "DNP - TerriData")


# ============================================================================================ puras
class Redaccion(unittest.TestCase):
    def test_nombre_natural_baja_las_particulas(self):
        self.assertEqual(lg.nombre_natural("Santa Rosa De Cabal"), "Santa Rosa de Cabal")
        self.assertEqual(lg.nombre_natural("De La Cruz"), "De la Cruz")

    def test_informal_sin_tildes_ni_signos(self):
        self.assertEqual(lg.informal("¿Cuál es la cobertura de acueducto en Medellín?"), "cual es la cobertura de acueducto en medellin")

    def test_unir_y_cerrar(self):
        self.assertEqual(lg.unir(["a", "b", "c"]), "a, b y c")
        self.assertEqual(lg.cerrar("Bogotá, D.C. se ubica en Bogotá, D.C.."), "Bogotá, D.C. se ubica en Bogotá, D.C.")
        self.assertEqual(lg.cerrar("Y sigue..."), "Y sigue...")

    def test_contracciones_de_variable(self):
        self.assertEqual(lg.var_de("deficit_habitacional_cuantitativo"), "del déficit habitacional cuantitativo")
        self.assertEqual(lg.var_de("cobertura_acueducto"), "de la cobertura de acueducto")


class Derivados(unittest.TestCase):
    def test_diferencia_se_calcula_sobre_lo_mostrado_y_coincide_con_el_validador(self):
        a, b = dato(valor=94.24, anio=2022), dato(valor=91.0, anio=2023)
        self.assertEqual(resp_a.diferencia_consistente(a, b), "3,2 puntos porcentuales")
        # mostrado 94,2 - 94,1 = 0,1 pero real 94,24-94,06 = 0,18 -> redondea a 0,2: no coincide, se descarta
        c, d = dato(valor=94.24), dato(valor=94.06)
        self.assertIsNone(resp_a.diferencia_consistente(c, d))

    def test_variacion_porcentual(self):
        a, b = dato(valor=50.0, anio=2020), dato(valor=55.0, anio=2021)
        self.assertEqual(resp_a.variacion_consistente(a, b), "10,0 %")

    def test_fiabilidad_nunca_se_promedia(self):
        ds = [dato(real=False, fiab=30), dato(real=False, fiab=55)]
        self.assertEqual(texto_fiabilidad(ds), "30 % a 55 %")
        self.assertEqual(texto_fiabilidad([dato(real=False, fiab=30), dato(real=False, fiab=30)]), "30 %")


class Plantillas(unittest.TestCase):
    def _ctx(self, ds):
        return lg.Contexto(datos=ds)

    def test_a1_dato_real_sin_advertencia(self):
        d = dato()
        for i in range(len(resp_a.A1)):
            t = resp_a.A1[i].format(**resp_a._campos(d))
            self.assertIn("Bucaramanga (Santander)", t)
            self.assertIn("94,2 %", t)
            self.assertNotIn("estimaci", t.lower())

    def test_a2_proyeccion_con_aviso_y_fiabilidad(self):
        d = dato(anio=2030, real=False, fiab=30)
        texto, _ = resp_a.a2(self._ctx([d]), Eleccion(random.Random(1)))
        self.assertIn("30 %", texto)
        self.assertTrue("estimaci" in texto.lower() or "proyecc" in texto.lower())

    def test_ningun_marco_de_a1_deja_de_el_ni_a_el(self):
        for var in lg.FRASE_VARIABLE:
            d = dato(variable=var, valor=12.3)
            for f in resp_a.A1 + resp_a.A2_BASE:
                t = f.format(**resp_a._campos(d))
                self.assertNotIn(" de el ", t)
                self.assertNotIn(" a el ", t)

    def test_b_sin_cifras_ni_palabras_prohibidas(self):
        for motivo in resp_b.MOTIVO_LLANO:
            sd = lg.SinDato("cobertura_acueducto", "Bucaramanga", "Santander", "2007" if motivo != "no hay ningún dato real" else "sin año con dato real", motivo)
            ctx = lg.Contexto(sin_datos=[sd])
            for i in range(30):
                texto, _ = resp_b.b1(ctx, Eleccion(random.Random(i)), {})
                self.assertFalse(any(p in texto.lower() for p in ("aproximadamente", "estimo", "alrededor", "cerca de")), texto)

    def test_c1_c2_c3_sin_digitos(self):
        el = Eleccion(random.Random(7))
        for tema in resp_c.C1_CONCEPTO:
            self.assertFalse(any(ch.isdigit() for ch in resp_c.c1(tema, el)[0]))
        for palabra in resp_c.C2_DEFINICION:
            self.assertFalse(any(ch.isdigit() for ch in resp_c.c2(palabra, el)[0]))
        for tema, (_, rs) in resp_c.C3_TEMAS.items():
            for r in rs:
                self.assertFalse(any(ch.isdigit() for ch in r.replace("{Y}", "")), (tema, r))

    def test_c3_cada_tema_tiene_al_menos_tres_preguntas_y_dos_respuestas(self):
        for tema, (qs, rs) in resp_c.C3_TEMAS.items():
            self.assertGreaterEqual(len(qs), 3, tema)
            self.assertGreaterEqual(len(rs), 2, tema)


class Similitud(unittest.TestCase):
    def test_dedupe_detecta_copias_y_deja_pasar_distintas(self):
        corpus = ["¿Cuál es la cobertura de acueducto en Bucaramanga en 2022?", "¿Cuál es la cobertura de acueducto en Bucaramanga en 2022?",
                  "Dime el déficit habitacional de Pasto", "Háblame de Rionegro"]
        t = similitud.Tfidf(corpus)
        conj = similitud.Conjunto()
        conj.agregar(t.vec(corpus[0]))
        self.assertGreaterEqual(conj.maximo(t.vec(corpus[1]))[0], similitud.UMBRAL)
        self.assertLess(conj.maximo(t.vec(corpus[2]))[0], similitud.UMBRAL)

    def test_indice_invertido_igual_al_coseno_directo(self):
        corpus = ["acueducto en Bucaramanga 2022", "alcantarillado en Girón 2021", "déficit en Pasto"]
        t = similitud.Tfidf(corpus)
        conj = similitud.Conjunto()
        for c in corpus[:2]:
            conj.agregar(t.vec(c))
        directo = max(similitud.coseno(t.vec(corpus[2]), t.vec(c)) for c in corpus[:2])
        self.assertAlmostEqual(conj.maximo(t.vec(corpus[2]))[0], directo, places=9)


class ControlesPuros(unittest.TestCase):
    def _c(self, fam, sub, resp, user="CONTEXTO\nSin datos disponibles para esta consulta.\n\nPREGUNTA DEL USUARIO\n¿Qué es el IPM?", pregunta="¿Qué es el IPM?"):
        c = Candidato(fam, sub, {"pregunta": pregunta}, user, resp, "p", "r")
        c.render = {"items": []}
        c.ctx = lg.Contexto()
        return c

    def test_forma_detecta_emoji_markdown_lightgbm(self):
        self.assertEqual(controles.forma(self._c("C", "C3", "Hola 😀")), "emoji")
        self.assertEqual(controles.forma(self._c("C", "C3", "**negrita**")), "markdown")
        self.assertEqual(controles.forma(self._c("C", "C3", "- viñeta")), "markdown")
        self.assertEqual(controles.forma(self._c("C", "C3", "Usa LightGBM")), "lightgbm")
        self.assertIsNone(controles.forma(self._c("C", "C3", "Respuesta normal.")))

    def test_b_y_c_solo_aceptan_anios_de_la_pregunta_o_del_contexto(self):
        c = self._c("C", "C1", "En 2030 no hay datos.", pregunta="¿Qué pasa en 2030?")
        self.assertIsNone(controles.cifras(c))
        c = self._c("C", "C1", "Son 94 millones.", pregunta="¿Cuántos?")
        self.assertEqual(controles.cifras(c), "numero_no_permitido_en_bc")
        c = self._c("B", "B1", "En 2042 no hay.", pregunta="¿Qué pasa en 2030?")
        self.assertEqual(controles.cifras(c), "numero_no_permitido_en_bc")

    def test_b_prohibe_aproximadamente_y_estimo(self):
        for t in ("Aproximadamente así.", "Yo estimo que sí.", "Alrededor de eso.", "Cerca de aquí.", "Una cifra aproximada."):
            self.assertEqual(controles.palabras_b(self._c("B", "B5", t)), "palabra_prohibida_b", t)
        self.assertIsNone(controles.palabras_b(self._c("B", "B5", "No lo voy a estimar.")))


class TokenizadorPuro(unittest.TestCase):
    def test_system_da_241_tokens(self):
        from .tokens import EXE, VOCAB, Tokenizador
        if not (EXE.exists() and VOCAB.exists()):
            self.skipTest("llama-tokenize o el vocabulario Llama 3 no estan en este equipo")
        from services.orquestador.prompts import cargar_system
        self.assertEqual(Tokenizador().contar([cargar_system()]), [241])


# ============================================================================================ con BD
@unittest.skipUnless(CON_BD, "TECHO360_PRUEBAS_BD=1 para las pruebas con base de datos (solo lectura)")
class ConBase(unittest.TestCase):
    SEMILLA = 20261017

    @classmethod
    def setUpClass(cls):
        from .datos import Datos
        from .generar import elegir_reservados
        from .familias import Generador
        cls.datos = Datos(DIR_PROYECCION.resolve())
        cls.reservados = set(elegir_reservados(cls.datos, cls.SEMILLA))
        cls.Generador = Generador

    def gen(self, modo="train"):
        return self.Generador(self.datos, self.SEMILLA, self.reservados, modo)

    def test_sesion_en_solo_lectura(self):
        self.assertEqual(self.datos.solo_lectura, "on")

    # un ejemplo fijo por subfamilia: el primer candidato de una semilla fija (ver SNAPSHOTS)
    def test_ejemplo_fijo_por_subfamilia(self):
        from .generar import PLAN
        g = self.gen()
        for sub, (fam, _, _, buckets) in PLAN.items():
            bucket, _, metodo, kw = buckets[0]
            c = None
            for _ in range(20):
                c = getattr(g, metodo)(**kw)
                if c:
                    break
            with self.subTest(sub):
                self.assertIsNotNone(c, f"{sub}: ningun candidato en 20 intentos")
                esperado = SNAPSHOTS.get(sub)
                if esperado is None:
                    self.skipTest("sin snapshot")
                self.assertEqual(c.user, esperado["user"])
                self.assertEqual(c.respuesta, esperado["respuesta"])

    def test_dorada_el_user_se_reconstruye_byte_a_byte(self):
        g = self.gen()
        for _ in range(15):
            c = g.A1()
            if c:
                self.assertIsNone(controles.dorada(self.datos, c))
                entrada = json.loads(json.dumps(c.entrada, ensure_ascii=False))
                self.assertEqual(self.datos.renderizar(entrada)["user"], c.user)
        alterado = Candidato("A", "A1", dict(c.entrada, municipios=[]), c.user, c.respuesta, "p", "r")
        self.assertEqual(controles.dorada(self.datos, alterado), "dorada_user_difiere")

    def test_el_contexto_nunca_se_escribe_a_mano(self):
        """El user de cualquier candidato coincide con el que arma `ensamblar` del backend para el mismo contexto."""
        from services.orquestador import ensamblador as ens
        g = self.gen()
        c = g.A5("medio")
        contexto = self.datos.contexto(c.entrada)
        paquete = ens.ensamblar(c.entrada["pregunta"], contexto, {"usuario_id": 0, "consentimiento_entrenamiento": False})
        self.assertEqual(paquete["peticion"]["mensajes"][1]["contenido"], c.user)

    def test_resolutor_real_llega_a_la_misma_entrada(self):
        g = self.gen()
        for _ in range(10):
            c = g.A1()
            if c:
                self.assertIsNone(g.paridad(c.entrada))

    def test_dedupe_no_deja_pasar_la_misma_pregunta_dos_veces(self):
        g = self.gen()
        c = g.A1()
        t = similitud.Tfidf([c.entrada["pregunta"]])
        conj = similitud.Conjunto()
        conj.agregar(t.vec(c.entrada["pregunta"]))
        self.assertGreaterEqual(conj.maximo(t.vec(c.entrada["pregunta"]))[0], similitud.UMBRAL)

    def test_determinismo_misma_semilla_mismos_archivos(self):
        from .generar import main
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            cache = Path(d1) / "cache.json"
            for d in (d1, d2):
                main(["--salida", d, "--semilla", str(self.SEMILLA), "--escala", "0.04", "--sin-c0", "--sin-informe",
                      "--cache-tokens", str(cache)])
            for nombre in ("train_nuevos.jsonl", "holdout_ctx_v0_4.jsonl", "reservados.json"):
                h1 = hashlib.sha256((Path(d1) / nombre).read_bytes()).hexdigest()
                h2 = hashlib.sha256((Path(d2) / nombre).read_bytes()).hexdigest()
                self.assertEqual(h1, h2, nombre)

    def test_semilla_distinta_cambia_el_resultado(self):
        g1 = self.Generador(self.datos, 1, self.reservados, "train")
        g2 = self.Generador(self.datos, 2, self.reservados, "train")
        self.assertNotEqual(g1.A1().user, g2.A1().user)


SNAPSHOTS: dict = {}
SNAPSHOTS_ARCHIVO = Path(__file__).with_name("snapshots_tests.json")
if SNAPSHOTS_ARCHIVO.exists():
    SNAPSHOTS = json.loads(SNAPSHOTS_ARCHIVO.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
