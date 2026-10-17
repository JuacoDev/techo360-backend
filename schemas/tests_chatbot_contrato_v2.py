"""Pruebas de esquema del contrato RPC v2 (paso 2.4-A), sin RabbitMQ, sin Ollama y sin base.

Valida los modelos de schemas/chatbot_contrato_v2.py contra contrato_v2_ejemplos.json (identico byte a byte en
techo360-backend y techo360-chatbot). Las pruebas del consumer, de la traduccion HTTP y del
consumer viejo (6 a 8 de la seccion 8 del documento) son del paso 4.4.

Uso:
    python -m unittest schemas.tests_chatbot_contrato_v2
"""
import copy
import json
import os
import unittest

from schemas.chatbot_contrato_v2 import (
    ErrorContrato,
    PeticionV1,
    PeticionV2,
    RespuestaErrorV2,
    RespuestaOkV2,
    es_proyeccion,
    validar_peticion,
    validar_respuesta,
)

RUTA_EJEMPLOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contrato_v2_ejemplos.json")
with open(RUTA_EJEMPLOS, encoding="utf-8") as f:
    EJEMPLOS = json.load(f)


def _por_nombre(lista, nombre):
    return next(c for c in lista if c["nombre"] == nombre)["payload"]


class PeticionesValidas(unittest.TestCase):
    def test_v1_validos(self):
        for caso in EJEMPLOS["peticiones_v1_validas"]:
            with self.subTest(caso["nombre"]):
                self.assertIsInstance(validar_peticion(caso["payload"]), PeticionV1)

    def test_v2_validos(self):
        for caso in EJEMPLOS["peticiones_v2_validas"]:
            with self.subTest(caso["nombre"]):
                self.assertIsInstance(validar_peticion(caso["payload"]), PeticionV2)

    def test_version_ausente_o_1_es_v1(self):
        base = {"mensaje": "x", "usuario_id": 1, "consentimiento_entrenamiento": True}
        self.assertIsInstance(validar_peticion(base), PeticionV1)
        self.assertIsInstance(validar_peticion(dict(base, version_contrato=1)), PeticionV1)

    def test_v2_ejemplo_conserva_campos(self):
        p = validar_peticion(_por_nombre(EJEMPLOS["peticiones_v2_validas"], "v2_ejemplo_spec"))
        self.assertEqual(p.mensajes[0].rol, "system")
        self.assertEqual(p.contexto[0].id, "c1")
        self.assertIsNone(p.contexto[0].fiabilidad)
        self.assertEqual(p.metadatos.version_prompt, "sp-0.4.0-1")

    def test_v2_sin_contexto_da_lista_vacia(self):
        p = validar_peticion(_por_nombre(EJEMPLOS["peticiones_v2_validas"], "v2_sin_clave_contexto"))
        self.assertEqual(p.contexto, [])


class PeticionesInvalidas(unittest.TestCase):
    def test_casos_invalidos_con_su_tipo_de_error(self):
        for caso in EJEMPLOS["peticiones_invalidas"]:
            with self.subTest(caso["nombre"]):
                with self.assertRaises(ErrorContrato) as cm:
                    validar_peticion(caso["payload"])
                self.assertEqual(cm.exception.tipo, caso["error_esperado"])

    def test_version_3_es_contrato_no_soportado(self):
        with self.assertRaises(ErrorContrato) as cm:
            validar_peticion(_por_nombre(EJEMPLOS["peticiones_invalidas"], "version_contrato_3"))
        self.assertEqual(cm.exception.tipo, "contrato_no_soportado")

    def test_contenido_demasiado_largo(self):
        p = copy.deepcopy(_por_nombre(EJEMPLOS["peticiones_v2_validas"], "v2_ejemplo_spec"))
        p["mensajes"][1]["contenido"] = "x" * 8000
        validar_peticion(p)  # el limite es inclusivo
        p["mensajes"][1]["contenido"] = "x" * 8001
        with self.assertRaises(ErrorContrato) as cm:
            validar_peticion(p)
        self.assertEqual(cm.exception.tipo, "payload_invalido")

    def test_veinte_contextos_pasan_y_veintiuno_fallan(self):
        # Enmienda del 17/10/2026 a fase2-2: contexto de 0 a 20 elementos (antes 40).
        p = copy.deepcopy(_por_nombre(EJEMPLOS["peticiones_v2_validas"], "v2_ejemplo_spec"))
        base = p["contexto"][0]
        p["contexto"] = [dict(base, id="c%d" % i) for i in range(20)]
        validar_peticion(p)
        p["contexto"].append(dict(base, id="c20"))
        with self.assertRaises(ErrorContrato) as cm:
            validar_peticion(p)
        self.assertEqual(cm.exception.tipo, "payload_invalido")

    def test_v2_sin_mensaje_no_valida_como_v1(self):
        # Un consumer v1 que reciba un v2 debe fallar (seccion 6, regla 2), no responder sin contexto.
        for caso in EJEMPLOS["peticiones_v2_validas"]:
            with self.subTest(caso["nombre"]):
                self.assertNotIn("mensaje", caso["payload"])
                with self.assertRaises(Exception):
                    PeticionV1.model_validate(caso["payload"])

    def test_el_detalle_del_error_no_incluye_el_contenido(self):
        p = copy.deepcopy(_por_nombre(EJEMPLOS["peticiones_v2_validas"], "v2_ejemplo_spec"))
        p["mensajes"][1]["contenido"] = "SECRETO-DE-USUARIO " + "x" * 8100
        with self.assertRaises(ErrorContrato) as cm:
            validar_peticion(p)
        self.assertNotIn("SECRETO-DE-USUARIO", cm.exception.detail)


class DecisionesDeEsquema(unittest.TestCase):
    """Decisiones de Juan/Claude del 17/10/2026 (14:04-14:30) sobre las ambiguedades de la especificacion."""

    def _v2(self):
        return copy.deepcopy(_por_nombre(EJEMPLOS["peticiones_v2_validas"], "v2_ejemplo_spec"))

    def _error(self, payload):
        with self.assertRaises(ErrorContrato) as cm:
            validar_peticion(payload)
        self.assertEqual(cm.exception.tipo, "payload_invalido")

    def test_1_campos_obligatorios_del_contexto_y_de_los_metadatos(self):
        for campo in ("id", "tipo", "valor", "origen", "es_real"):
            with self.subTest("contexto." + campo):
                p = self._v2()
                del p["contexto"][0][campo]
                self._error(p)
        for campo in ("usuario_id", "consentimiento_entrenamiento"):
            with self.subTest("metadatos." + campo):
                p = self._v2()
                del p["metadatos"][campo]
                self._error(p)
        p = self._v2()
        for campo in ("variable", "codigodivipola", "anio", "unidad", "fuente"):
            p["contexto"][0].pop(campo)
        validar_peticion(p)  # el resto del contexto es opcional

    def test_2_contenido_solo_espacios(self):
        for contenido in (" ", "   ", "\n", "\t \n"):
            with self.subTest(repr(contenido)):
                p = self._v2()
                p["mensajes"][1]["contenido"] = contenido
                self._error(p)
        p = self._v2()
        p["mensajes"][1]["contenido"] = " hola "
        validar_peticion(p)

    def test_3_extras_solo_prohibidos_en_el_nivel_superior(self):
        p = self._v2()
        p["mensaje"] = "hola"  # la forma v1 mezclada con v2
        self._error(p)
        p = self._v2()
        p["otra_clave"] = 1
        self._error(p)
        p = self._v2()
        p["contexto"][0]["extra"] = "x"
        p["metadatos"]["extra"] = "x"
        validar_peticion(p)  # en contexto y metadatos se ignoran

    def test_4_fiabilidad_null_si_es_real(self):
        p = self._v2()
        p["contexto"][0]["fiabilidad"] = 80  # origen TerriData, es_real=true
        self._error(p)
        p = self._v2()
        p["contexto"][0].update(tipo="proyeccion", origen="proyeccion-ultimo-valor", es_real=False, fiabilidad=62)
        validar_peticion(p)  # a las proyecciones no se les fuerza nada
        p["contexto"][0]["fiabilidad"] = None
        validar_peticion(p)

    def test_5_respuesta_modelo_obligatorio_y_resto_opcional(self):
        base = {"version_contrato": 2, "status": "ok", "response": "x"}
        with self.assertRaises(ErrorContrato):
            validar_respuesta(dict(base, metadatos={}))
        r = validar_respuesta(dict(base, metadatos={"modelo": "techo360-chatbot:v3.2"}))
        self.assertIsNone(r.metadatos.digest)
        self.assertIsNone(r.metadatos.done_reason)
        self.assertEqual(r.metadatos.contexto_ids, [])


class OrigenDeProyeccion(unittest.TestCase):
    def test_tabla_de_casos(self):
        for caso in EJEMPLOS["casos_origen"]:
            with self.subTest(str(caso["origen"])):
                self.assertIs(es_proyeccion(caso["origen"]), caso["es_proyeccion"])


class Respuestas(unittest.TestCase):
    def test_respuestas_validas(self):
        for caso in EJEMPLOS["respuestas_validas"]:
            with self.subTest(caso["nombre"]):
                r = validar_respuesta(caso["payload"])
                esperado = RespuestaOkV2 if caso["payload"]["status"] == "ok" else RespuestaErrorV2
                self.assertIsInstance(r, esperado)

    def test_digest_es_opcional(self):
        r = validar_respuesta(_por_nombre(EJEMPLOS["respuestas_validas"], "respuesta_ok_sin_digest"))
        self.assertIsNone(r.metadatos.digest)
        r = validar_respuesta(_por_nombre(EJEMPLOS["respuestas_validas"], "respuesta_ok_spec"))
        self.assertEqual(r.metadatos.digest, "sha256:abc123")

    def test_respuestas_invalidas(self):
        for caso in EJEMPLOS["respuestas_invalidas"]:
            with self.subTest(caso["nombre"]):
                with self.assertRaises(ErrorContrato):
                    validar_respuesta(caso["payload"])


if __name__ == "__main__":
    unittest.main()
