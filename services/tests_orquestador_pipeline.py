"""Pruebas de la parte 2 de la Fase 4 (claude/fase4-especificacion-2.md): 4.1b ajustes del resolutor, 4.4 pipeline y
bandera, 4.5 validador de cifras, 4.6 bloque de grafica, 4.7 sanitizador, 4.8 persistencia y aislamiento entre
usuarios. Sin Redis, RabbitMQ, base ni modelo (dobles). El extremo a extremo con contenedores desechables esta en
scripts/e2e_orquestador_local.py.

Uso:
    python -m unittest services.tests_orquestador_pipeline
"""
import asyncio
import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from schemas.chatbot_contrato_v2 import validar_peticion
from services import chatbot_estados, chatbot_service, proyeccion_cache
from services.orquestador import grafica_bloque as gb
from services.orquestador import persistencia
from services.orquestador import pipeline as pp
from services.orquestador import resolutor as rs
from services.orquestador.sanitizador import MAX_SALIDA, sanitizar_texto
from services.orquestador.validador_cifras import extraer_numeros, validar_cifras
from services.tests_orquestador import CAT, A, Datos, fila, punto

HOY = date(2026, 10, 17)
META = {"usuario_id": 7, "consentimiento_entrenamiento": False, "conversacion_id": 12, "mensaje_id": 345, "intento": 1}


def correr(coro):
    return asyncio.run(coro)


def res(texto):
    return rs.resolver(texto, CAT, HOY)


# ------------------------------------------------------------------------------------------ 4.1b
class Ajustes41b(unittest.TestCase):
    def test_palabra_comun_en_pregunta_conceptual_no_resuelve_pesca(self):
        r = res("¿Qué es la pesca en Colombia?")
        self.assertEqual((r.municipios, r.aclaracion), ([], None))

    def test_con_departamento_si_resuelve(self):
        r = res("déficit en Pesca, Boyacá")
        self.assertIsNone(r.aclaracion)
        self.assertEqual([m["divipola"] for m in r.municipios], ["15541"])

    def test_sensible_capitalizado_o_precedido_de_preposicion(self):
        self.assertEqual([m["divipola"] for m in res("cobertura en Pesca").municipios], ["15541"])
        self.assertEqual([m["divipola"] for m in res("cobertura en pesca").municipios], ["15541"])  # precedido de "en"
        self.assertEqual([m["divipola"] for m in res("cobertura del municipio de pesca").municipios], ["15541"])
        self.assertEqual(res("cobertura de la pesca artesanal").municipios, [])  # minuscula y sin preposicion directa

    def test_departamento_de_x_se_lee_como_departamento(self):
        r = res("cobertura en el departamento de Caldas")
        self.assertEqual((r.municipios, r.aclaracion), ([], None))
        r = res("cobertura en el departamento de Caldas y en Rionegro, Antioquia")
        self.assertEqual([m["divipola"] for m in r.municipios], ["05615"])
        self.assertIsNone(r.aclaracion)

    def test_homonimo_solo_pide_aclaracion_con_intencion_de_datos(self):
        self.assertEqual(res("Granada").aclaracion, None)  # sin variable ni año
        self.assertEqual(res("¿Qué es Rionegro?").aclaracion, None)
        con_variable = res("cobertura de acueducto en Rionegro")
        self.assertEqual(con_variable.aclaracion["tipo"], "homonimo")
        con_anio = res("Rionegro en 2024")
        self.assertEqual(con_anio.aclaracion["tipo"], "homonimo")

    def test_granada_con_variable_pide_aclaracion(self):
        from services.orquestador.resolutor import Catalogo, Municipio

        cat = Catalogo([Municipio("05313", "Granada", "05", "Antioquia"), Municipio("25312", "Granada", "25", "Cundinamarca"),
                        Municipio("50313", "Granada", "50", "Meta")])
        r = rs.resolver("¿Cuál es la cobertura de acueducto en Granada?", cat, HOY)
        self.assertEqual(r.aclaracion["tipo"], "homonimo")
        for dep in ("Antioquia", "Cundinamarca", "Meta"):
            self.assertIn(dep, r.aclaracion["texto"])


# --------------------------------------------------------------------------------------------- 4.5
CTX = [
    "[c1] Cobertura de acueducto, Bucaramanga (Santander), 2024: 94,2 %. Fuente: DNP - TerriData. Dato real.",
    "[c2] Cobertura de acueducto, Bucaramanga (Santander), 2020: 90,0 %. Fuente: DNP - TerriData. Dato real.",
    "[c3] Densidad poblacional, Bucaramanga (Santander), 2025: 4.012 hab/km². Fuente: DANE - Proyecciones de población. Proyección, fiabilidad 30 %.",
]
ITEMS = [
    {"valor": 94.2, "variable": "cobertura_acueducto", "codigodivipola": "68001", "anio": 2024},
    {"valor": 90.0, "variable": "cobertura_acueducto", "codigodivipola": "68001", "anio": 2020},
    {"valor": 4012.0, "variable": "densidad_poblacional", "codigodivipola": "68001", "anio": 2025},
]


def vc(texto, pregunta="cobertura de acueducto en Bucaramanga"):
    return validar_cifras(texto, CTX, ITEMS, pregunta)


class Validador(unittest.TestCase):
    def test_cifras_del_contexto_y_formatos(self):
        for texto in ("Es 94,2 %.", "Es 94,2%.", "Es 94.2.", "La densidad es 4.012 hab/km².", "La densidad es 4012.", "Fiabilidad 30 %."):
            with self.subTest(texto):
                self.assertEqual(vc(texto)["estado"], "ok")

    def test_redondeo_a_menos_decimales_si_pero_no_a_otro_valor(self):
        self.assertEqual(vc("Casi 94 %")["estado"], "ok")
        self.assertEqual(vc("Casi 94,2")["estado"], "ok")
        self.assertEqual(vc("Es 94,3 %")["no_validadas"], ["94,3 %"])
        self.assertEqual(vc("Es 94,25 %")["estado"], "no_validada")  # mas decimales que el contexto
        self.assertEqual(vc("Son 4.000 hab/km²")["estado"], "no_validada")

    def test_derivados_permitidos(self):
        self.assertEqual(vc("Subió 4,2 puntos porcentuales")["estado"], "ok")  # 94,2 - 90,0
        self.assertEqual(vc("Subió un 4,7 %")["estado"], "ok")  # variacion porcentual 90,0 -> 94,2
        self.assertEqual(vc("Entre ambos suman 184,2")["estado"], "ok")
        self.assertEqual(vc("Subió 5,1 puntos")["estado"], "no_validada")

    def test_promedios_y_estimaciones_propias_no_se_validan(self):
        self.assertEqual(vc("El promedio nacional es 88,9 %")["estado"], "no_validada")

    def test_anios(self):
        self.assertEqual(vc("En 2024 fue 94,2 %")["estado"], "ok")  # año del contexto
        self.assertEqual(vc("En 2019 fue distinto", pregunta="y en 2019?")["estado"], "abstencion")  # año de la pregunta
        self.assertEqual(vc("Hacia 2031 sera mejor")["no_validadas"], ["2031"])  # año que no esta en el contexto ni en la pregunta
        self.assertEqual(vc("La cifra 2500 es inventada")["no_validadas"], ["2500"])  # no es un año valido para el filtro (fuera de 2000-2042 no aplica)

    def test_listas_y_codigos(self):
        self.assertEqual(vc("1. Primero\n2. Segundo")["estado"], "abstencion")
        self.assertEqual(vc("Usa sp-0.4.0-1 y el Decreto L3")["estado"], "abstencion")
        self.assertEqual(vc("Datos de CNPV2018")["estado"], "abstencion")

    def test_sin_cifras_pasa_como_abstencion(self):
        self.assertEqual(vc("No tengo ese dato; no puedo estimarlo.")["estado"], "abstencion")

    def test_escalas_y_signos(self):
        n = extraer_numeros("1,5 millones y -3,2 puntos y 2.5")
        self.assertEqual([(x.valor, x.decimales) for x in n], [(1500000.0, 1), (-3.2, 1), (2.5, 1)])
        self.assertEqual(validar_cifras("Son 1,5 millones", ["Poblacion: 1.500.000 habitantes."], None, "")["estado"], "ok")


# --------------------------------------------------------------------------------------------- 4.7
class Sanitizador(unittest.TestCase):
    def test_html_comentarios_imagenes_enlaces_y_esquemas(self):
        s = sanitizar_texto('Hola <script>alert(1)</script><b>mundo</b> <!-- oculto --> ![x](http://a/b.png) [aquí](https://e.com/x) '
                            'javascript:alert(1) data:text/html;base64,AAA')
        self.assertNotIn("<", s)
        self.assertNotIn("oculto", s)
        self.assertNotIn("http", s)
        self.assertNotIn("javascript:", s.lower())
        self.assertNotIn("data:", s.lower())
        self.assertIn("aquí", s)
        self.assertIn("mundo", s)

    def test_control_y_bidireccional(self):
        s = sanitizar_texto("a\x00b\x07c‮d‬e‏f⁦g⁩h")
        self.assertEqual(s, "abcdefgh")

    def test_tope_de_longitud(self):
        self.assertEqual(len(sanitizar_texto("x" * (MAX_SALIDA + 500))), MAX_SALIDA)

    def test_texto_normal_no_cambia(self):
        t = "La cobertura de acueducto en Bucaramanga (Santander) fue 94,2 % en 2024.\nSegunda línea."
        self.assertEqual(sanitizar_texto(t), t)

    def test_idempotente(self):
        s = sanitizar_texto("<i>x</i> [a](b)")
        self.assertEqual(sanitizar_texto(s), s)


# --------------------------------------------------------------------------------------------- 4.6
SPEC = {"v": 1, "tipo": "lineas", "modo": "individual", "variables": [A], "zonas": ["68001"], "anios": [2019, 2024]}


def con_bloque(spec, antes="Aqui va.", despues="Fin."):
    return f"{antes}\n```grafica\n{json.dumps(spec)}\n```\n{despues}"


class Grafica(unittest.TestCase):
    def procesar(self, texto, datos=None, validos=("68001", "05001")):
        datos = datos or Datos(series={("68001", A): [fila(a, 80.0 + a - 2019) for a in range(2019, 2025)]})

        async def score(codigo, anio):
            return {"estado": "sin_dato", "motivo": "sin_hueco"}

        return correr(gb.procesar_grafica(texto, set(validos), leer_serie_fn=datos.leer, completar=datos.completar, score_fn=score))

    def test_bloque_valido_se_valida_y_se_quita_del_texto(self):
        texto, g = self.procesar(con_bloque(SPEC))
        self.assertEqual(g["valida"], True)
        self.assertEqual(g["spec"]["tipo"], "lineas")
        self.assertNotIn("```", texto)
        self.assertEqual(texto, "Aqui va.\n\nFin.")

    def test_sin_bloque(self):
        self.assertEqual(self.procesar("Solo texto."), ("Solo texto.", None))

    def test_dos_bloques_usa_el_primero_y_quita_ambos(self):
        otro = dict(SPEC, tipo="gauge", anios=[2024])
        texto, g = self.procesar(con_bloque(SPEC) + "\n" + con_bloque(otro, antes="", despues=""))
        self.assertEqual(g["spec"]["tipo"], "lineas")
        self.assertNotIn("```", texto)

    def test_bloque_sin_cierre_o_json_roto_se_descartan(self):
        texto, g = self.procesar("Texto\n```grafica\n" + json.dumps(SPEC))
        self.assertEqual((g["valida"], texto), (False, "Texto"))
        self.assertIn("bloque_sin_cierre", g["motivo"])
        texto, g = self.procesar('Texto\n```grafica\n{"v":1,"tipo":\n```\nfin')
        self.assertFalse(g["valida"])
        self.assertIn("json_invalido", g["motivo"])
        self.assertNotIn("```", texto)

    def test_estructura_y_combinacion_invalidas(self):
        _, g = self.procesar(con_bloque(dict(SPEC, valores=[1, 2])))
        self.assertIn("estructura", g["motivo"])
        _, g = self.procesar(con_bloque(dict(SPEC, tipo="gauge", variables=["densidad_poblacional"], anios=[2024])))
        self.assertIn("combinacion", g["motivo"])

    def test_zona_inexistente(self):
        _, g = self.procesar(con_bloque(dict(SPEC, zonas=["99999"])))
        self.assertEqual(g["valida"], False)
        self.assertIn("zona_inexistente", g["motivo"])

    def test_lineas_con_un_solo_punto_no_es_valida(self):
        datos = Datos(series={("68001", A): [fila(2019, 80.0)]})
        _, g = self.procesar(con_bloque(SPEC), datos=datos)
        self.assertEqual(g["valida"], False)
        self.assertIn("datos_insuficientes", g["motivo"])

    def test_los_puntos_que_faltan_se_completan(self):
        datos = Datos(series={("68001", A): [fila(2019, 80.0)]}, completados={(A, "68001", 2024): punto(A, "68001", 2024, 90.0)})
        _, g = self.procesar(con_bloque(SPEC), datos=datos)
        self.assertEqual(g["valida"], True)

    def test_quitar_bloques(self):
        self.assertEqual(gb.quitar_bloques("a\n```grafica\n{}\n```\nb"), "a\n\nb")
        self.assertEqual(gb.quitar_bloques("a\n```json\n{}\n```\nb"), "a\n```json\n{}\n```\nb")


# ---------------------------------------------------------------------------------- 4.4 pipeline
SERIE = [fila(a, 80.0 + a - 2019) for a in range(2019, 2025)]  # 2024 -> 85,0 %


def respuesta_ok(texto, **extra):
    return {"version_contrato": 2, "status": "ok", "response": texto,
            "metadatos": {"modelo": "techo360-chatbot:v3.2", "digest": "sha256:abc", "done_reason": "stop",
                          "prompt_eval_count": 600, "eval_count": 50, "duracion_ms": 30000, "contexto_ids": ["c1"], **extra}}


class Solicitar:
    def __init__(self, *respuestas):
        self.respuestas, self.peticiones, self.timeouts = list(respuestas), [], []

    async def __call__(self, peticion, timeout=None):
        self.peticiones.append(peticion)
        self.timeouts.append(timeout)
        return self.respuestas.pop(0)


class Reloj:
    def __init__(self, pasos):
        self.pasos = list(pasos)
        self.ultimo = 0.0

    def __call__(self):
        if self.pasos:
            self.ultimo = self.pasos.pop(0)
        return self.ultimo


def correr_pipeline(pregunta, solicitar, datos=None, **kw):
    datos = datos or Datos(series={("68001", A): SERIE})
    return correr(pp.responder(pregunta, None, dict(META), hoy=HOY, catalogo=CAT, solicitar=solicitar,
                               leer_serie_fn=datos.leer, completar=datos.completar,
                               score_fn=AsyncMock(return_value={"estado": "sin_dato", "motivo": "sin_hueco"}), **kw))


class Pipeline(unittest.TestCase):
    P = "cobertura de acueducto en Bucaramanga 2024"

    def test_respuesta_valida_pasa_y_lleva_metadatos(self):
        sol = Solicitar(respuesta_ok("En 2024 la cobertura fue 85,0 %."))
        r = correr_pipeline(self.P, sol)
        self.assertEqual(r["accion"], "responder")
        self.assertEqual(r["texto"], "En 2024 la cobertura fue 85,0 %.")
        m = r["meta"]
        self.assertEqual((m["version_contrato"], m["regeneraciones"], m["validacion_cifras"]["estado"]), (2, 0, "ok"))
        self.assertEqual((m["modelo"], m["modelo_digest"], m["version_prompt"], m["done_reason"], m["tokens_prompt"], m["tokens_respuesta"]),
                         ("techo360-chatbot:v3.2", "sha256:abc", "sp-0.4.0-1", "stop", 600, 50))
        self.assertEqual(m["contexto_ids"], ["c1"])
        self.assertEqual(json.loads(m["contexto"])["e"][0]["x"], 85.0)
        self.assertEqual(len(sol.peticiones), 1)
        validar_peticion(sol.peticiones[0])

    def test_cifra_inventada_se_corrige_en_el_reintento(self):
        sol = Solicitar(respuesta_ok("Fue 99,9 %."), respuesta_ok("Fue 85,0 %."))
        r = correr_pipeline(self.P, sol)
        self.assertEqual((r["accion"], r["texto"]), ("responder", "Fue 85,0 %."))
        self.assertEqual((r["meta"]["regeneraciones"], r["meta"]["validacion_cifras"]), (1, {"estado": "regenerada", "no_validadas": []}))
        self.assertEqual([p["metadatos"]["intento"] for p in sol.peticiones], [1, 2])
        self.assertEqual(sol.peticiones[0]["mensajes"], sol.peticiones[1]["mensajes"])  # mismo contexto
        self.assertEqual(sol.peticiones[0]["contexto"], sol.peticiones[1]["contexto"])

    def test_cifra_inventada_dos_veces_da_respuesta_determinista(self):
        sol = Solicitar(respuesta_ok("Fue 99,9 %."), respuesta_ok("Fue 97,1 %."))
        r = correr_pipeline(self.P, sol)
        self.assertEqual(r["accion"], "responder")
        self.assertTrue(r["texto"].startswith(pp.NOTA_FALLBACK))
        self.assertIn("Cobertura de acueducto, Bucaramanga (Santander), 2024: 85,0 %. Fuente: DNP - TerriData. Dato real.", r["texto"])
        self.assertNotIn("99,9", r["texto"])
        self.assertNotIn("97,1", r["texto"])
        self.assertEqual((r["meta"]["regeneraciones"], r["meta"]["validacion_cifras"]), (1, {"estado": "abstencion", "no_validadas": ["97,1 %"]}))
        self.assertEqual(len(sol.peticiones), 2)

    def test_margen_de_tiempo_insuficiente_va_directo_al_fallback(self):
        # t0=0; primera generacion de 100 s (t1=0 -> 100); quedan 200-110=90 s < 1,3 x 100
        reloj = Reloj([0.0, 0.0, 100.0, 110.0, 110.0])
        sol = Solicitar(respuesta_ok("Fue 99,9 %."))
        r = correr_pipeline(self.P, sol, reloj=reloj)
        self.assertEqual(len(sol.peticiones), 1)
        self.assertEqual((r["meta"]["regeneraciones"], r["meta"]["validacion_cifras"]["estado"]), (0, "abstencion"))
        self.assertTrue(r["texto"].startswith(pp.NOTA_FALLBACK))

    def test_margen_suficiente_si_reintenta(self):
        reloj = Reloj([0.0, 0.0, 30.0, 40.0, 40.0, 70.0, 70.0])  # primera 30 s; quedan 160 >= 39
        sol = Solicitar(respuesta_ok("Fue 99,9 %."), respuesta_ok("Fue 85,0 %."))
        correr_pipeline(self.P, sol, reloj=reloj)
        self.assertEqual(len(sol.peticiones), 2)
        self.assertLessEqual(sol.timeouts[1], 200 - 40 + 0.001)

    def test_homonimo_se_aclara_sin_llegar_a_rabbitmq(self):
        sol = Solicitar()
        r = correr_pipeline("cobertura de acueducto en Rionegro", sol)
        self.assertEqual((r["accion"], r["tipo"]), ("aclarar", "homonimo"))
        self.assertIn("¿A cuál te refieres?", r["texto"])
        self.assertEqual(sol.peticiones, [])

    def test_error_del_consumer_se_propaga(self):
        sol = Solicitar({"version_contrato": 2, "status": "error", "tipo": "ollama_no_disponible", "detail": "sin ollama"})
        r = correr_pipeline(self.P, sol)
        self.assertEqual((r["accion"], r["tipo"], r["detail"]), ("error", "ollama_no_disponible", "sin ollama"))

    def test_respuesta_fuera_de_contrato_es_error(self):
        sol = Solicitar({"status": "ok", "response": "x"})  # una respuesta v1: no es RespuestaV2
        r = correr_pipeline(self.P, sol)
        self.assertEqual((r["accion"], r["tipo"]), ("error", "error_interno"))

    def test_el_texto_entregado_va_sanitizado_y_sin_bloque_de_grafica(self):
        texto = "Fue 85,0 % <b>según</b> [DNP](http://x.y).\n" + con_bloque(SPEC, antes="", despues="")
        r = correr_pipeline(self.P, Solicitar(respuesta_ok(texto)),
                            datos=Datos(series={("68001", A): SERIE}))
        self.assertEqual(r["texto"], "Fue 85,0 % según DNP.")
        self.assertEqual(r["meta"]["grafica"]["valida"], True)

    def test_pregunta_sin_contexto_se_abstiene_sin_cifras(self):
        sol = Solicitar(respuesta_ok("No tengo datos para esa consulta."))
        r = correr_pipeline("¿Qué es el score de privación?", sol)
        self.assertEqual(r["meta"]["validacion_cifras"]["estado"], "abstencion")
        self.assertEqual(sol.peticiones[0]["contexto"], [])

    def test_sin_texto_tras_sanitizar_cae_al_determinista(self):
        r = correr_pipeline(self.P, Solicitar(respuesta_ok("<!-- nada -->")))
        self.assertTrue(r["texto"].startswith(pp.NOTA_FALLBACK) or r["texto"] == pp.TEXTO_SIN_DATOS)


# --------------------------------------------------------------------------------------------- 4.8
class FakeDb:
    def __init__(self, falla=False):
        self.sentencias, self.falla, self.commits, self.rollbacks, self.flushes, self.agregados = [], falla, 0, 0, 0, []

    def execute(self, sentencia, parametros=None):
        if self.falla:
            raise RuntimeError("base caida")
        self.sentencias.append((str(sentencia), parametros))

    def add(self, objeto):
        objeto.id = 99
        self.agregados.append(objeto)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, objeto):
        pass


META_V2 = {"version_contrato": 2, "modelo": "m", "modelo_digest": "d", "version_prompt": "sp-0.4.0-1", "done_reason": "stop",
           "tokens_prompt": 10, "tokens_respuesta": 5, "duracion_ms": 1000, "contexto_ids": ["c1"], "contexto": '{"s":[],"e":[]}',
           "validacion_cifras": {"estado": "ok", "no_validadas": []}, "regeneraciones": 0, "grafica": {"valida": False, "motivo": "x"}}


class Persistencia(unittest.TestCase):
    def test_un_update_parametrizado_con_las_13_columnas(self):
        db = FakeDb()
        persistencia.aplicar_metadatos(db, 99, META_V2)
        sql, params = db.sentencias[0]
        self.assertTrue(sql.lstrip().upper().startswith("UPDATE MENSAJES"))
        for columna in persistencia.CAMPOS_META:
            self.assertIn(columna, sql)
        self.assertEqual(params["id"], 99)
        self.assertEqual(params["contexto_ids"], ["c1"])
        self.assertEqual(json.loads(params["validacion_cifras"])["estado"], "ok")
        self.assertEqual(params["regeneraciones"], 0)
        self.assertNotIn("DROP", sql)

    def test_agregar_mensaje_guarda_en_la_misma_transaccion(self):
        db = FakeDb()
        chatbot_service.agregar_mensaje(db, 5, "asistente", "texto", metadatos=META_V2)
        self.assertEqual((len(db.sentencias), db.commits, db.rollbacks), (1, 1, 0))

    def test_sin_metadatos_no_hace_nada_distinto(self):
        db = FakeDb()
        chatbot_service.agregar_mensaje(db, 5, "usuario", "hola")
        self.assertEqual((db.sentencias, db.flushes, db.commits), ([], 0, 1))

    def test_si_falla_el_guardado_no_queda_nada_y_se_propaga_el_error(self):
        db = FakeDb(falla=True)
        with self.assertRaises(RuntimeError):
            chatbot_service.agregar_mensaje(db, 5, "asistente", "texto", metadatos=META_V2)
        self.assertEqual((db.commits, db.rollbacks), (0, 1))


# -------------------------------------------------------------------- bandera apagada / aislamiento
class Bandera(unittest.TestCase):
    def test_por_defecto_esta_apagada(self):
        from core.config import Settings
        self.assertIs(Settings.model_fields["chatbot_orquestador_activo"].default, False)

    def test_apagada_es_exactamente_solicitar_chatbot(self):
        from routers import chatbot as r
        payload = {"mensaje": "hola", "usuario_id": 7, "consentimiento_entrenamiento": False, "conversacion_id": 1}
        with patch.object(r.settings, "chatbot_orquestador_activo", False), \
                patch.object(r, "solicitar_chatbot", AsyncMock(return_value={"status": "ok", "response": "r"})) as sol, \
                patch("services.orquestador.pipeline.responder", AsyncMock()) as pipeline:
            salida = correr(r._pedir_respuesta(payload, None, "hola"))
        sol.assert_awaited_once_with(payload)
        pipeline.assert_not_awaited()
        self.assertEqual(salida, {"status": "ok", "response": "r"})  # sin "_v2"

    def test_encendida_usa_el_pipeline_y_devuelve_la_misma_forma(self):
        from routers import chatbot as r
        payload = {"mensaje": "hola", "usuario_id": 7, "consentimiento_entrenamiento": True, "conversacion_id": 1, "mensaje_id": 5, "intento": 2}
        ok = {"accion": "responder", "texto": "T", "meta": {"regeneraciones": 0}}
        with patch.object(r.settings, "chatbot_orquestador_activo", True), \
                patch.object(r, "solicitar_chatbot", AsyncMock()) as sol, \
                patch("services.orquestador.pipeline.responder", AsyncMock(return_value=ok)) as pipeline:
            salida = correr(r._pedir_respuesta(payload, "db", "hola"))
        sol.assert_not_awaited()
        self.assertEqual(salida, {"status": "ok", "response": "T", "_v2": {"regeneraciones": 0}})
        self.assertEqual(pipeline.await_args.args[2], {k: payload[k] for k in ("usuario_id", "consentimiento_entrenamiento", "conversacion_id", "mensaje_id", "intento")})
        error = {"accion": "error", "tipo": "ollama_no_disponible", "detail": "d"}
        with patch.object(r.settings, "chatbot_orquestador_activo", True), patch("services.orquestador.pipeline.responder", AsyncMock(return_value=error)):
            self.assertEqual(correr(r._pedir_respuesta(payload, "db", "hola")),
                             {"status": "error", "tipo": "ollama_no_disponible", "detail": "d"})


class Aislamiento(unittest.TestCase):
    def test_dos_usuarios_con_la_misma_pregunta_no_comparten_nada(self):
        datos = Datos(series={("68001", A): SERIE})
        peticiones = {}

        async def uno(usuario, conv):
            sol = Solicitar(respuesta_ok("Fue 85,0 %."))
            meta = {"usuario_id": usuario, "consentimiento_entrenamiento": usuario == 1, "conversacion_id": conv, "mensaje_id": conv * 10, "intento": 1}
            r = await pp.responder("cobertura de acueducto en Bucaramanga 2024", None, meta, hoy=HOY, catalogo=CAT, solicitar=sol,
                                   leer_serie_fn=datos.leer, completar=datos.completar,
                                   score_fn=AsyncMock(return_value={"estado": "sin_dato", "motivo": "sin_hueco"}))
            peticiones[usuario] = sol.peticiones[0]
            return r

        async def ambos():
            return await asyncio.gather(uno(1, 100), uno(2, 200))

        r1, r2 = correr(ambos())
        p1, p2 = peticiones[1], peticiones[2]
        self.assertEqual(p1["contexto"], p2["contexto"])  # mismo dato publico
        self.assertEqual(p1["mensajes"], p2["mensajes"])
        self.assertNotEqual(p1["metadatos"], p2["metadatos"])
        self.assertEqual((p1["metadatos"]["usuario_id"], p2["metadatos"]["usuario_id"]), (1, 2))
        # el contexto y los mensajes no llevan ningun dato del usuario ni de la conversacion
        for p, usuario, conv in ((p1, 1, 100), (p2, 2, 200)):
            texto = json.dumps({"mensajes": p["mensajes"], "contexto": p["contexto"]}, ensure_ascii=False)
            for prohibido in ("usuario_id", "conversacion_id", "consentimiento", f'"{usuario}"', f"conv{conv}"):
                self.assertNotIn(prohibido, texto)
        self.assertEqual(r1["meta"]["contexto"], r2["meta"]["contexto"])
        self.assertEqual(r1["texto"], r2["texto"])

    def test_la_clave_de_cache_de_los_puntos_no_depende_del_usuario(self):
        clave = proyeccion_cache.ProyeccionCache._clave_punto("cobertura_acueducto", "68001", 2012)
        self.assertEqual(clave, "proyeccion:v3:punto:cobertura_acueducto:68001:2012")
        import inspect
        self.assertNotIn("usuario", inspect.getsource(proyeccion_cache.ProyeccionCache._clave_punto))

    def test_conversacion_ajena_no_se_resuelve(self):
        capturado = {}

        class Consulta:
            def filter(self, *condiciones):
                capturado["condiciones"] = [str(c.compile(compile_kwargs={"literal_binds": True})) for c in condiciones]
                return self

            def first(self):
                return None

        class Sesion:
            def query(self, modelo):
                return Consulta()

        self.assertIsNone(chatbot_service.resolver_conversacion(Sesion(), "12", usuario_id=7))
        self.assertTrue(any("usuario_id = 7" in c for c in capturado["condiciones"]))
        self.assertIsNone(chatbot_service.conversacion_de_usuario(Sesion(), 12, 7))
        self.assertTrue(any("usuario_id = 7" in c for c in capturado["condiciones"]))


if __name__ == "__main__":
    unittest.main()
