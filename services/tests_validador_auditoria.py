"""Pruebas de las correcciones de la auditoria 4.10: derivados del validador de cifras mas estrechos y
regeneracion con error o timeout. Sin Redis, RabbitMQ, base ni modelo.

Uso:
    python -m unittest services.tests_validador_auditoria
"""
import unittest
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations
from unittest.mock import AsyncMock

from services import chatbot_rpc_client
from services.orquestador import pipeline as pp
from services.orquestador.validador_cifras import valores_derivados, validar_cifras
from services.tests_orquestador import A, AL, CUANT, DENS, Datos, fila
from services.tests_orquestador_pipeline import Pipeline, Solicitar, correr, correr_pipeline, respuesta_ok


def item(variable, codigo, anio, valor):
    return {"variable": variable, "codigodivipola": codigo, "anio": anio, "valor": valor}


# 8 elementos sinteticos de 4 variables (dos por variable)
ITEMS = [
    item(A, "68001", 2020, 90.0), item(A, "68001", 2024, 94.2),
    item(AL, "68001", 2020, 85.0), item(AL, "68001", 2024, 88.4),
    item(CUANT, "68001", 2018, 12.5), item(CUANT, "05001", 2018, 9.1),
    item(DENS, "68001", 2025, 4012.0), item(DENS, "05001", 2025, 7640.0),
]
NOMBRE = {A: "Cobertura de acueducto", AL: "Cobertura de alcantarillado", CUANT: "Déficit habitacional cuantitativo", DENS: "Densidad poblacional"}
MUN = {"68001": "Bucaramanga (Santander)", "05001": "Medellín (Antioquia)"}


def _linea(i, n):
    v = f"{i['valor']:.1f}".replace(".", ",")
    cierre = "Dato real." if i["anio"] <= 2024 and i["variable"] != DENS else "Proyección, fiabilidad 30 %."
    return f"[c{n}] {NOMBRE[i['variable']]}, {MUN[i['codigodivipola']]}, {i['anio']}: {v}. Fuente: X. {cierre}"


LINEAS = [_linea(i, n + 1) for n, i in enumerate(ITEMS)]


def redondeo_entero(x):
    return int(Decimal(repr(abs(x))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pasa(texto, pregunta="cobertura"):
    return validar_cifras(texto, LINEAS, ITEMS, pregunta)["estado"] in ("ok", "abstencion")


class Derivados(unittest.TestCase):
    def test_a_enteros_inventados_del_1_al_100(self):
        valores_contexto = {redondeo_entero(i["valor"]) for i in ITEMS} | {30}  # 30 = la fiabilidad que aparece en las lineas
        derivados = {redondeo_entero(d) for d in valores_derivados(ITEMS)}
        sin_palabra = {n for n in range(1, 101) if pasa(f"Son {n}.")}
        con_palabra = {n for n in range(1, 101) if pasa(f"Subió {n} puntos.")}
        esperado_sin = {n for n in valores_contexto if 1 <= n <= 100}
        esperado_con = esperado_sin | {n for n in derivados if 1 <= n <= 100}
        print(f"\n[medicion a] 8 elementos / 4 variables: sin palabra de comparacion pasan {len(sin_palabra)} de 100 "
              f"(valores del contexto: {sorted(sin_palabra)}); con palabra pasan {len(con_palabra)} de 100 "
              f"(+ derivados legitimos: {sorted(con_palabra - sin_palabra)})")
        self.assertEqual(sin_palabra, esperado_sin)
        self.assertEqual(con_palabra, esperado_con)
        # ningun entero inventado (ni del contexto ni derivado legitimo) pasa
        self.assertFalse((con_palabra - esperado_con) | (sin_palabra - esperado_sin))
        # antes de la correccion pasaban 26; ahora estrictamente menos que con todos los pares sumados y restados
        self.assertLess(len(con_palabra), 26)

    def test_b_derivado_con_y_sin_palabra_de_comparacion(self):
        self.assertTrue(pasa("El acueducto subió 4,2 puntos entre 2020 y 2024."))  # 94,2 - 90,0, misma variable
        self.assertFalse(pasa("El acueducto son 4,2 puntos."))  # sin palabra de comparacion
        self.assertTrue(pasa("La brecha entre Bucaramanga y Medellín en déficit es 3,4."))  # 12,5 - 9,1, misma variable
        self.assertFalse(pasa("Entre ambos suman 184,2 puntos más."))  # las sumas ya no existen
        self.assertFalse(pasa("El acueducto subió 9,2 puntos respecto al alcantarillado."))  # 94,2 - 85,0: variables distintas
        self.assertTrue(pasa("El acueducto subió un 4,7 % desde 2020."))  # variacion porcentual 90,0 -> 94,2
        # limite conocido: no se comprueba a QUE variable atribuye la oracion el derivado (solo que exista uno legitimo)

    def test_el_derivado_se_evalua_por_oracion(self):
        self.assertFalse(pasa("Subió mucho. Fueron 4,2 puntos."))  # la palabra esta en otra oracion
        self.assertTrue(pasa("Fueron 4,2 puntos más que antes."))

    def test_c_n_anios_entre_2019_y_2024_pasa_y_sin_anios_no(self):
        items = [item(A, "68001", 2019, 80.0), item(A, "68001", 2024, 85.0)]
        lineas = ["[c1] Cobertura de acueducto, Bucaramanga (Santander), 2019: 80,0 %. Fuente: X. Dato real.",
                  "[c2] Cobertura de acueducto, Bucaramanga (Santander), 2024: 85,0 %. Fuente: X. Dato real."]
        self.assertEqual(validar_cifras("En 5 años la cobertura creció.", lineas, items, "")["estado"], "ok")
        self.assertEqual(validar_cifras("En 2 años hay datos.", lineas, items, "")["estado"], "ok")  # conteo real: dos años distintos
        self.assertEqual(validar_cifras("En 7 años la cobertura creció.", lineas, items, "")["estado"], "no_validada")
        sin_anios = ["[c1] Dato sin año: 80,0 %. Fuente: X. Dato real."]
        self.assertEqual(validar_cifras("En 5 años la cobertura creció.", sin_anios, [item(A, "68001", None, 80.0)], "")["estado"], "no_validada")
        un_anio = [item(A, "68001", 2024, 85.0)]
        self.assertEqual(validar_cifras("En 5 años la cobertura creció.", [lineas[1]], un_anio, "")["estado"], "no_validada")

    def test_d_conteo_de_municipios_y_variables(self):
        self.assertTrue(pasa("Hay datos de 2 municipios."))  # Bucaramanga y Medellin
        self.assertFalse(pasa("Hay datos de 3 municipios."))
        self.assertTrue(pasa("Consulté 4 variables."))
        self.assertFalse(pasa("Consulté 5 variables."))
        self.assertFalse(pasa("Hay 4 departamentos."))  # solo municipios, años y variables se cuentan


# --------------------------------------------------------------------------------------------- (e)
class RegeneracionConFallo(unittest.TestCase):
    P = Pipeline.P

    def test_regeneracion_con_error_del_consumer_cae_al_determinista(self):
        sol = Solicitar(respuesta_ok("Fue 99,9 %."),
                        {"version_contrato": 2, "status": "error", "tipo": "ollama_no_disponible", "detail": "sin ollama"})
        r = correr_pipeline(self.P, sol)
        self.assertEqual(r["accion"], "responder")
        self.assertTrue(r["texto"].startswith(pp.NOTA_FALLBACK))
        self.assertNotIn("99,9", r["texto"])
        self.assertEqual(r["meta"]["validacion_cifras"]["estado"], "abstencion")
        self.assertEqual(r["meta"]["validacion_cifras"]["no_validadas"], ["99,9 %"])
        self.assertEqual(r["meta"]["regeneraciones"], 1)
        self.assertEqual(len(sol.peticiones), 2)

    def test_regeneracion_con_timeout_cae_al_determinista(self):
        class ConTimeout(Solicitar):
            async def __call__(self, peticion, timeout=None):
                if self.peticiones:
                    self.peticiones.append(peticion)
                    raise chatbot_rpc_client.ChatbotTimeoutError("El asistente no respondio a tiempo.")
                return await super().__call__(peticion, timeout)

        sol = ConTimeout(respuesta_ok("Fue 99,9 %."))
        r = correr_pipeline(self.P, sol)
        self.assertEqual(r["accion"], "responder")
        self.assertTrue(r["texto"].startswith(pp.NOTA_FALLBACK))
        self.assertEqual(r["meta"]["validacion_cifras"]["estado"], "abstencion")
        self.assertEqual(len(sol.peticiones), 2)

    def test_regeneracion_fuera_de_contrato_cae_al_determinista(self):
        sol = Solicitar(respuesta_ok("Fue 99,9 %."), {"status": "ok", "response": "x"})
        r = correr_pipeline(self.P, sol)
        self.assertTrue(r["texto"].startswith(pp.NOTA_FALLBACK))

    def test_el_error_de_la_primera_generacion_sigue_siendo_error(self):
        sol = Solicitar({"version_contrato": 2, "status": "error", "tipo": "ollama_no_disponible", "detail": "sin ollama"})
        r = correr_pipeline(self.P, sol)
        self.assertEqual((r["accion"], r["tipo"]), ("error", "ollama_no_disponible"))

    def test_el_timeout_de_la_primera_generacion_se_propaga(self):
        async def timeout(peticion, timeout=None):
            raise chatbot_rpc_client.ChatbotTimeoutError("El asistente no respondio a tiempo.")

        with self.assertRaises(chatbot_rpc_client.ChatbotTimeoutError):
            correr_pipeline(self.P, timeout)


if __name__ == "__main__":
    unittest.main()
