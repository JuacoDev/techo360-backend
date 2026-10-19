"""6.7b Tope de tokens del prompt (num_ctx 3072, num_predict 400, 2,3 caracteres por token). Sin base, sin modelo.

Ollama, si el prompt no cabe en num_ctx, desplaza el contexto y descarta el system a mitad de la generacion SIN error.
Ramas: cabe (nada cambia) / se quitan fragmentos del RAG / se recortan lineas de contexto / la pregunta sola no cabe.

Uso:
    python -m unittest services.tests_orquestador_tope
"""
import os
import unittest
from unittest.mock import patch

from core.config import Settings, settings
from services.orquestador import contexto as cx
from services.orquestador import ensamblador as ens
from services.orquestador.orquestador import preparar_consulta
from services.tests_orquestador import CAT, HOY, META, Datos, candidatos, correr
from services.tests_orquestador_rag import F_FORMULA, F_MUN

SYSTEM = "s" * 460  # 201 tokens a 2,3 car/token


def tokens_de(r) -> int:
    return r["notas"]["tokens_estimados"]


def ensamblar(pregunta, contexto, presupuesto=None):
    if presupuesto is None:
        return ens.ensamblar(pregunta, contexto, dict(META), system=SYSTEM)
    with patch.object(ens, "PRESUPUESTO_ENTRADA", presupuesto):
        return ens.ensamblar(pregunta, contexto, dict(META), system=SYSTEM)


class Constantes(unittest.TestCase):
    def test_valores_por_defecto_y_presupuesto(self):
        self.assertEqual((settings.ollama_num_ctx, settings.ollama_num_predict), (3072, 400))
        self.assertEqual(ens.PRESUPUESTO_ENTRADA, 3072 - 400 - 64)

    def test_se_configuran_por_entorno(self):
        with patch.dict(os.environ, {"OLLAMA_NUM_CTX": "4096", "OLLAMA_NUM_PREDICT": "256"}):
            s = Settings()
        self.assertEqual((s.ollama_num_ctx, s.ollama_num_predict), (4096, 256))

    def test_estimacion_a_2_3_caracteres_por_token(self):
        self.assertEqual(ens.estimar_tokens_prompt("x" * 229), 101)
        self.assertGreater(ens.estimar_tokens_prompt("x" * 300), cx.estimar_tokens("x" * 300))  # mas estricta que 3,0

    def test_el_system_tambien_se_estima_a_2_3(self):
        r = ensamblar("hola", cx.ContextoConstruido())
        esperado = ens.TOKENS_PLANTILLA + ens.estimar_tokens_prompt(SYSTEM) + ens.estimar_tokens_prompt(
            ens.mensaje_usuario([cx.LINEA_SIN_CONTEXTO], "hola"))
        self.assertEqual(tokens_de(r), esperado)


class Cabe(unittest.TestCase):
    def test_prompt_normal_no_se_toca(self):
        ctx = cx.ContextoConstruido(fragmentos_rag=[F_FORMULA, F_MUN])
        r = ensamblar("¿Cómo se calcula el Score?", ctx)
        self.assertEqual(r["notas"]["rag_fragmentos"], ["01-formula", "05-codigos-oficiales"])
        self.assertTrue(r["notas"]["dentro_del_presupuesto"])
        self.assertFalse(r["notas"]["pregunta_no_cabe"])
        self.assertLessEqual(tokens_de(r), ens.PRESUPUESTO_ENTRADA)

    def test_con_el_system_real_y_la_pregunta_maxima_cabe(self):
        r = ens.ensamblar("p" * 1500, candidatos(20, largo=170), dict(META))
        self.assertTrue(r["notas"]["dentro_del_presupuesto"])
        self.assertLessEqual(tokens_de(r), ens.PRESUPUESTO_ENTRADA)


class RecorteEnOrden(unittest.TestCase):
    def test_primero_se_quitan_los_fragmentos_del_rag_el_menos_relevante_primero(self):
        base = ensamblar("¿Qué es el Score?", cx.ContextoConstruido(fragmentos_rag=[F_FORMULA, F_MUN]))
        con_uno = ensamblar("¿Qué es el Score?", cx.ContextoConstruido(fragmentos_rag=[F_FORMULA]))
        sin = ensamblar("¿Qué es el Score?", cx.ContextoConstruido())
        r = ensamblar("¿Qué es el Score?", cx.ContextoConstruido(fragmentos_rag=[F_FORMULA, F_MUN]), tokens_de(con_uno))
        self.assertEqual(r["notas"]["rag_fragmentos"], ["01-formula"])
        r = ensamblar("¿Qué es el Score?", cx.ContextoConstruido(fragmentos_rag=[F_FORMULA, F_MUN]), tokens_de(con_uno) - 1)
        self.assertEqual(r["notas"]["rag_fragmentos"], [])
        self.assertLessEqual(tokens_de(r), tokens_de(con_uno) - 1)
        self.assertGreater(tokens_de(base), tokens_de(con_uno))
        self.assertGreater(tokens_de(con_uno), tokens_de(sin))

    def test_los_datos_no_se_tocan_mientras_quitar_rag_alcance(self):
        ctx = candidatos(3, largo=60)
        ctx.fragmentos_rag = [F_FORMULA, F_MUN]
        sin_rag = ensamblar("¿Qué es el Score?", candidatos(3, largo=60))
        r = ensamblar("¿Qué es el Score?", ctx, tokens_de(sin_rag))
        self.assertEqual((r["notas"]["rag_fragmentos"], r["contexto_ids"]), ([], ["c1", "c2", "c3"]))

    def test_despues_se_recortan_las_lineas_de_contexto_las_ultimas_primero(self):
        completo = ensamblar("¿Cómo está?", candidatos(10, largo=80))
        self.assertEqual(len(completo["contexto_ids"]), 10)
        r = ensamblar("¿Cómo está?", candidatos(10, largo=80), tokens_de(completo) - 60)
        ids = r["contexto_ids"]
        self.assertTrue(0 < len(ids) < 10)
        self.assertEqual(ids, [f"c{n}" for n in range(1, len(ids) + 1)])  # prefijo: se pierden las ultimas
        self.assertGreater(r["notas"]["omitidos"], 0)
        self.assertTrue(r["notas"]["dentro_del_presupuesto"])
        self.assertLessEqual(tokens_de(r), tokens_de(completo) - 60)

    def test_rag_y_lineas_juntos_rag_primero(self):
        ctx = candidatos(10, largo=80)
        ctx.fragmentos_rag = [F_FORMULA]
        completo = ensamblar("¿Cómo está?", ctx)
        solo_datos = ensamblar("¿Cómo está?", candidatos(10, largo=80))
        r = ensamblar("¿Cómo está?", ctx, tokens_de(solo_datos) - 40)
        self.assertEqual(r["notas"]["rag_fragmentos"], [])
        self.assertLess(len(r["contexto_ids"]), 10)
        self.assertGreater(tokens_de(completo), tokens_de(solo_datos))

    def test_las_lineas_sin_dato_obligatorias_no_se_recortan(self):
        ctx = candidatos(10, sin_dato=2, largo=80)
        completo = ensamblar("¿Cómo está?", ctx)
        r = ensamblar("¿Cómo está?", ctx, tokens_de(completo) - 200)
        contenido = r["peticion"]["mensajes"][1]["contenido"]
        self.assertIn("Sin dato para algo 0.", contenido)
        self.assertIn("Sin dato para algo 1.", contenido)
        self.assertTrue(r["notas"]["dentro_del_presupuesto"])

    def test_nunca_se_recorta_el_system_ni_la_pregunta(self):
        r = ensamblar("¿Cómo está?", candidatos(10, largo=80), 400)
        self.assertEqual(r["peticion"]["mensajes"][0]["contenido"], SYSTEM)
        self.assertTrue(r["peticion"]["mensajes"][1]["contenido"].endswith("¿Cómo está?"))


class PreguntaNoCabe(unittest.TestCase):
    def test_marca_pregunta_no_cabe_cuando_la_pregunta_sola_excede(self):
        r = ensamblar("p" * 1500, candidatos(5, largo=60), 300)
        self.assertTrue(r["notas"]["pregunta_no_cabe"])
        self.assertFalse(r["notas"]["dentro_del_presupuesto"])
        self.assertEqual(r["contexto_ids"], [])  # se agoto todo lo recortable antes de rendirse

    def test_el_orquestador_responde_el_mensaje_fijo_sin_armar_consulta(self):
        with patch.object(ens, "PRESUPUESTO_ENTRADA", 100):
            r = correr(preparar_consulta("¿Qué es el Score de privación?", None, dict(META), hoy=HOY, catalogo=CAT,
                                         leer_serie_fn=Datos().leer, completar=Datos().completar))
        self.assertEqual(r, {"accion": "aclarar", "tipo": "pregunta_larga", "texto": ens.MENSAJE_PREGUNTA_LARGA})
        self.assertIn("acortarla", r["texto"])

    def test_el_pipeline_no_llama_al_modelo(self):
        from services.tests_orquestador_pipeline import Solicitar, correr_pipeline, respuesta_ok
        sol = Solicitar(respuesta_ok("no deberia llamarse"))
        with patch.object(ens, "PRESUPUESTO_ENTRADA", 100):
            r = correr_pipeline("¿Qué es el Score de privación?", sol)
        self.assertEqual((r["accion"], r["tipo"], r["texto"]), ("aclarar", "pregunta_larga", ens.MENSAJE_PREGUNTA_LARGA))
        self.assertEqual(sol.peticiones, [])


if __name__ == "__main__":
    unittest.main()
