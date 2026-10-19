"""Pruebas de 4.13: aclaracion de homonimos con memoria (Redis, por usuario y conversacion). Sin Redis real, base,
RabbitMQ ni modelo (dobles). Reutiliza el catalogo sintetico y los dobles de las pruebas del orquestador.

Uso:
    python -m unittest services.tests_orquestador_aclaracion
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from services.orquestador import aclaracion as acl
from services.orquestador import pipeline as pp
from services.orquestador import resolutor as rs
from services.orquestador.resolutor import Catalogo, Municipio
from services.tests_orquestador import CAT, A, Datos, correr, fila
from services.tests_orquestador_pipeline import HOY, FakeRedis, Solicitar, respuesta_ok

P = "cobertura de acueducto en Rionegro"
SERIES = {("05615", A): [fila(a, 70.0 + a - 2019) for a in range(2019, 2025)],   # 2024 -> 75,0 %
          ("68615", A): [fila(a, 60.0 + a - 2019) for a in range(2019, 2025)],   # 2024 -> 65,0 %
          ("68001", A): [fila(a, 80.0 + a - 2019) for a in range(2019, 2025)]}


def catalogo_con_dos_en_santander() -> Catalogo:
    """CAT + un segundo Rionegro sintetico en Santander (para el caso «el de Santander» ambiguo)."""
    return Catalogo(CAT.municipios + [Municipio("68999", "Rionegro", "68", "Santander")])


class Sesion:
    """Una conversacion contra el pipeline, con almacen compartido (como Redis) y el modelo doblado."""

    def __init__(self, redis=None, catalogo=CAT, usuario=7, conversacion=12):
        self.redis = redis or FakeRedis()
        self.almacen = acl.AlmacenAclaraciones(fabrica_cliente=lambda: self.redis)
        self.catalogo, self.usuario, self.conversacion = catalogo, usuario, conversacion
        self.solicitar = Solicitar(*[respuesta_ok("Respuesta del modelo.")] * 20)
        self.datos = Datos(series=SERIES)

    def decir(self, texto, usuario=None, conversacion="-"):
        meta = {"usuario_id": self.usuario if usuario is None else usuario,
                "conversacion_id": self.conversacion if conversacion == "-" else conversacion}
        return correr(pp.responder(
            texto, None, meta, hoy=HOY, catalogo=self.catalogo, almacen=self.almacen, solicitar=self.solicitar,
            leer_serie_fn=self.datos.leer, completar=self.datos.completar,
            score_fn=AsyncMock(return_value={"estado": "sin_dato", "motivo": "sin_hueco"})))

    def estado(self, usuario=None, conversacion="-"):
        return self.almacen.leer(self.usuario if usuario is None else usuario,
                                 self.conversacion if conversacion == "-" else conversacion)

    def pregunta_al_modelo(self, i=-1):
        return self.solicitar.peticiones[i]["mensajes"][1]["contenido"].split("\n\nPREGUNTA DEL USUARIO\n")[1]

    def codigos_al_modelo(self, i=-1):
        return {c for it in self.solicitar.peticiones[i]["contexto"] for c in [it.get("c") or it.get("codigo")] if c}


class FlujoPrincipal(unittest.TestCase):
    def test_rionegro_antioquia(self):
        s = Sesion()
        r1 = s.decir(P)
        self.assertEqual((r1["accion"], r1["tipo"]), ("aclarar", "homonimo"))
        self.assertEqual(r1["texto"], "Hay varios municipios llamados Rionegro: Rionegro (Antioquia), Rionegro (Santander). ¿A cuál te refieres?")
        self.assertNotIn("opciones", r1)  # el router y la base nunca ven las opciones internas
        self.assertEqual(s.solicitar.peticiones, [])
        e = s.estado()
        self.assertEqual((e["pregunta_original"], e["tipo"]), (P, "homonimo"))
        self.assertEqual([o["divipola"] for o in e["opciones"]], ["05615", "68615"])
        r2 = s.decir("el de Antioquia")
        self.assertEqual(r2["accion"], "responder")
        self.assertEqual(r2["texto"], "Respuesta del modelo.")
        self.assertEqual(s.pregunta_al_modelo(), P)  # la pregunta original, no «el de Antioquia»
        peticion = json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False)
        self.assertIn("Rionegro (Antioquia)", peticion)
        self.assertNotIn("Rionegro (Santander)", peticion)
        self.assertNotIn("el de Antioquia", peticion)
        self.assertIsNone(s.estado())  # consumido

    def test_formas_de_responder_por_departamento(self):
        for respuesta in ("el de Antioquia", "la de Antioquia", "de Antioquia", "Antioquia", "antioquia.", "el de antioquia por favor",
                          "Rionegro, Antioquia", "el municipio de Antioquia", "el que esta en Antioquia"):
            s = Sesion()
            s.decir(P)
            r = s.decir(respuesta)
            self.assertEqual(r["accion"], "responder", respuesta)
            self.assertIn("Rionegro (Antioquia)", json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False), respuesta)

    def test_el_segundo_y_otros_ordinales(self):
        for respuesta, esperado in (("el segundo", "Santander"), ("la segunda", "Santander"), ("2", "Santander"),
                                    ("el primero", "Antioquia"), ("el 1", "Antioquia"), ("primera", "Antioquia")):
            s = Sesion()
            s.decir(P)
            r = s.decir(respuesta)
            self.assertEqual(r["accion"], "responder", respuesta)
            peticion = json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False)
            self.assertIn(f"Rionegro ({esperado})", peticion, respuesta)
            otro = "Antioquia" if esperado == "Santander" else "Santander"
            self.assertNotIn(f"Rionegro ({otro})", peticion, respuesta)

    def test_santander_cuando_solo_uno_es_de_santander(self):
        s = Sesion()
        s.decir(P)
        r = s.decir("Santander")
        self.assertEqual(r["accion"], "responder")
        self.assertEqual(s.pregunta_al_modelo(), P)
        self.assertIn("Rionegro (Santander)", json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False))

    def test_inconsistente_tambien_se_resuelve(self):
        s = Sesion()
        r1 = s.decir("cobertura de acueducto en Rionegro, Boyacá")
        self.assertEqual((r1["accion"], r1["tipo"]), ("aclarar", "inconsistente"))
        self.assertIn("pertenece a Antioquia o Santander, no a Boyacá", r1["texto"])
        r2 = s.decir("el de Santander")
        self.assertEqual(r2["accion"], "responder")
        self.assertIn("Rionegro (Santander)", json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False))

    def test_dos_homonimos_en_la_misma_pregunta_se_aclaran_en_dos_turnos(self):
        s = Sesion()
        r1 = s.decir("cobertura de acueducto en Rionegro y en Santa Rosa")
        self.assertIn("llamados Rionegro", r1["texto"])
        r2 = s.decir("el de Antioquia")
        self.assertEqual((r2["accion"], r2["tipo"]), ("aclarar", "homonimo"))
        self.assertIn("llamados Santa Rosa", r2["texto"])
        self.assertEqual(s.estado()["fijados"], {"rionegro": "05615"})
        r3 = s.decir("la de Cauca")
        self.assertEqual(r3["accion"], "responder")
        peticion = json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False)
        self.assertIn("Rionegro (Antioquia)", peticion)
        self.assertNotIn("Rionegro (Santander)", peticion)
        self.assertEqual(s.pregunta_al_modelo(), "cobertura de acueducto en Rionegro y en Santa Rosa")


class SinCoincidencia(unittest.TestCase):
    def test_pregunta_nueva_descarta_el_estado(self):
        s = Sesion()
        s.decir(P)
        r = s.decir("cobertura de acueducto en Bucaramanga 2024")
        self.assertEqual(r["accion"], "responder")
        self.assertIsNone(s.estado())
        # y la respuesta suelta de despues ya no resuelve nada
        s.decir("el de Antioquia")
        self.assertEqual(s.pregunta_al_modelo(), "el de Antioquia")

    def test_texto_no_relacionado_corto_o_largo_descarta(self):
        for texto in ("hola", "¿Qué es el score de privación?", "gracias",
                      "el de Antioquia pero dime tambien cuanta gente vive en todo el pais por favor ahora"):
            s = Sesion()
            s.decir(P)
            s.decir(texto)
            self.assertIsNone(s.estado(), texto)
            self.assertEqual(s.pregunta_al_modelo(), texto)

    def test_respuesta_con_variable_o_anio_es_pregunta_nueva(self):
        for texto in ("el de Antioquia 2023", "acueducto de Antioquia"):
            s = Sesion()
            s.decir(P)
            s.decir(texto)
            self.assertIsNone(s.estado(), texto)
            self.assertEqual(s.pregunta_al_modelo(), texto)

    def test_departamento_que_no_es_ninguna_opcion_repite_sin_renovar_el_plazo(self):
        s = Sesion()
        s.decir(P)
        r = s.decir("el de Boyacá")
        self.assertEqual((r["accion"], r["tipo"]), ("aclarar", "homonimo"))
        self.assertEqual(r["texto"], "Hay varios municipios llamados Rionegro: Rionegro (Antioquia), Rionegro (Santander). ¿A cuál te refieres?")
        self.assertEqual(s.solicitar.peticiones, [])
        self.assertIsNotNone(s.estado())
        s.redis.ahora += 400
        s.decir("el cuarto")  # fuera de rango: repite, tampoco renueva
        s.redis.ahora += 400   # 800 s desde el guardado original
        self.assertIsNone(s.estado())

    def test_ambiguo_repite_acotado_a_esas_opciones(self):
        s = Sesion(catalogo=catalogo_con_dos_en_santander())
        r1 = s.decir(P)
        self.assertEqual(r1["texto"], "Hay varios municipios llamados Rionegro: Rionegro (Antioquia), Rionegro (Santander), Rionegro (Santander). ¿A cuál te refieres?")
        r2 = s.decir("el de Santander")
        self.assertEqual((r2["accion"], r2["tipo"]), ("aclarar", "homonimo"))
        self.assertEqual(r2["texto"], "Hay varios municipios llamados Rionegro: Rionegro (Santander), Rionegro (Santander). ¿A cuál te refieres?")
        self.assertEqual([o["divipola"] for o in s.estado()["opciones"]], ["68615", "68999"])
        self.assertEqual(s.solicitar.peticiones, [])
        fijados = []
        original = pp.preparar_consulta

        async def espia(*a, **kw):
            fijados.append(kw.get("fijados"))
            return await original(*a, **kw)
        with patch.object(pp, "preparar_consulta", espia):
            r3 = s.decir("el segundo")  # ahora «segundo» es el de la lista acotada
        self.assertEqual(r3["accion"], "responder")
        self.assertIsNone(s.estado())
        self.assertEqual(fijados, [{"rionegro": "68999"}])

    def test_antioquia_sigue_siendo_inequivoca_con_dos_en_santander(self):
        s = Sesion(catalogo=catalogo_con_dos_en_santander())
        s.decir(P)
        self.assertEqual(s.decir("el de Antioquia")["accion"], "responder")


class TtlYAislamiento(unittest.TestCase):
    def test_ttl_de_diez_minutos_en_redis(self):
        s = Sesion()
        s.decir(P)
        clave = acl.clave_de(7, 12)
        self.assertEqual(clave, "aclaracion:v1:7:12")
        self.assertEqual(s.redis.datos[clave][1], acl.TTL_SEGUNDOS)
        self.assertEqual(acl.TTL_SEGUNDOS, 600)

    def test_ttl_vencido_la_respuesta_es_una_pregunta_nueva(self):
        s = Sesion()
        s.decir(P)
        s.redis.ahora += 601
        r = s.decir("el de Antioquia")
        self.assertEqual(r["accion"], "responder")
        self.assertEqual(s.pregunta_al_modelo(), "el de Antioquia")
        self.assertNotIn("Rionegro", json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False))

    def test_dentro_del_ttl_si_resuelve(self):
        s = Sesion()
        s.decir(P)
        s.redis.ahora += 599
        self.assertEqual(s.decir("el de Antioquia")["accion"], "responder")
        self.assertEqual(s.pregunta_al_modelo(), P)

    def test_dos_usuarios_con_la_misma_pregunta_en_paralelo(self):
        s = Sesion()  # mismo almacen, misma conversacion 12, usuarios 7 y 8
        a1, b1 = s.decir(P, usuario=7), s.decir(P, usuario=8)
        self.assertEqual((a1["accion"], b1["accion"]), ("aclarar", "aclarar"))
        self.assertEqual(sorted(k for k in s.redis.datos), ["aclaracion:v1:7:12", "aclaracion:v1:8:12"])

        async def en_paralelo():
            meta = lambda u: {"usuario_id": u, "conversacion_id": 12}
            comun = dict(hoy=HOY, catalogo=CAT, almacen=s.almacen, solicitar=s.solicitar, leer_serie_fn=s.datos.leer,
                         completar=s.datos.completar, score_fn=AsyncMock(return_value={"estado": "sin_dato", "motivo": "sin_hueco"}))
            return await asyncio.gather(pp.responder("el de Antioquia", None, meta(7), **comun),
                                        pp.responder("el segundo", None, meta(8), **comun))

        ra, rb = correr(en_paralelo())
        self.assertEqual((ra["accion"], rb["accion"]), ("responder", "responder"))
        contextos = {p["mensajes"][1]["contenido"] for p in s.solicitar.peticiones}
        self.assertEqual(len(s.solicitar.peticiones), 2)
        self.assertTrue(any("Antioquia" in c for c in contextos) and any("Santander" in c for c in contextos))
        self.assertEqual(s.redis.datos, {})

    def test_la_respuesta_de_otro_usuario_no_consume_el_estado(self):
        s = Sesion()
        s.decir(P, usuario=7)
        r = s.decir("el de Antioquia", usuario=8)       # el usuario 8 no tiene estado: pregunta nueva
        self.assertEqual(s.pregunta_al_modelo(), "el de Antioquia")
        self.assertIsNotNone(s.estado(usuario=7))
        self.assertEqual(s.decir("el de Antioquia", usuario=7)["accion"], "responder")
        self.assertEqual(s.pregunta_al_modelo(), P)

    def test_otra_conversacion_del_mismo_usuario_no_lo_ve(self):
        s = Sesion()
        s.decir(P, conversacion=12)
        s.decir("el de Antioquia", conversacion=13)
        self.assertEqual(s.pregunta_al_modelo(), "el de Antioquia")
        self.assertIsNotNone(s.estado(conversacion=12))

    def test_conversacion_sin_id_no_guarda_nada(self):
        s = Sesion()
        r = s.decir(P, conversacion=None)
        self.assertEqual(r["accion"], "aclarar")
        self.assertEqual(s.redis.datos, {})
        s.decir("el de Antioquia", conversacion=None)
        self.assertEqual(s.pregunta_al_modelo(), "el de Antioquia")

    def test_usuario_sin_id_tampoco(self):
        s = Sesion()
        s.almacen.guardar(None, 12, {"x": 1})
        self.assertEqual(s.redis.datos, {})


class EstadoYModelo(unittest.TestCase):
    def test_el_estado_solo_guarda_pregunta_opciones_y_codigos(self):
        s = Sesion()
        s.decir("  cobertura \x00de acueducto\x07 en   Rionegro  ")
        e = s.estado()
        self.assertEqual(set(e), {"pregunta_original", "tipo", "opciones", "fijados", "creado_en"})
        self.assertEqual(e["pregunta_original"], "cobertura de acueducto en Rionegro")
        self.assertEqual(set(e["opciones"][0]), {"divipola", "nombre", "departamento"})
        s.decir("el de Antioquia por favor, gracias")  # texto de la respuesta: ni se guarda ni llega al modelo
        crudo = json.dumps(s.solicitar.peticiones[-1], ensure_ascii=False)
        self.assertNotIn("gracias", crudo)
        self.assertNotIn("aclaracion", crudo.lower())

    def test_la_pregunta_guardada_se_recorta(self):
        s = Sesion()
        s.decir("acueducto en Rionegro " + "x" * 5000)
        self.assertLessEqual(len(s.estado()["pregunta_original"]), acl.MAX_PREGUNTA)

    def test_el_modelo_nunca_recibe_el_estado_ni_historial(self):
        s = Sesion()
        s.decir(P)
        s.decir("el segundo")
        p = s.solicitar.peticiones[-1]
        self.assertEqual([m["rol"] for m in p["mensajes"]], ["system", "user"])  # sin turnos previos
        crudo = json.dumps(p, ensure_ascii=False)
        for ajeno in ("pregunta_original", "opciones", "fijados", "creado_en", "Hay varios municipios", "A cuál te refieres"):
            self.assertNotIn(ajeno, crudo)

    def test_estado_corrupto_o_ajeno_se_ignora(self):
        s = Sesion()
        s.redis.set(acl.clave_de(7, 12), "no es json")
        self.assertEqual(s.decir("el de Antioquia")["accion"], "responder")
        s.redis.set(acl.clave_de(7, 12), json.dumps({"pregunta_original": 3, "tipo": "x"}))
        self.assertIsNone(s.estado())

    def test_texto_de_la_plantilla_no_cambia(self):
        r = rs.resolver(P, CAT, HOY)
        self.assertEqual(r.aclaracion["texto"], "Hay varios municipios llamados Rionegro: Rionegro (Antioquia), Rionegro (Santander). ¿A cuál te refieres?")
        r = rs.resolver("acueducto en Rionegro, Boyacá", CAT, HOY)
        self.assertEqual(r.aclaracion["texto"], "Rionegro pertenece a Antioquia o Santander, no a Boyacá. ¿Quieres consultar Rionegro (Antioquia) o Rionegro (Santander)?")

    def test_demasiados_no_guarda_estado(self):
        s = Sesion()
        r = s.decir("acueducto en Rionegro, Pereira, Cali, Manizales, Cartagena y Medellín")
        self.assertEqual(r["tipo"], "demasiados")
        self.assertEqual(s.redis.datos, {})

    def test_resolver_con_fijados(self):
        r = rs.resolver(P, CAT, HOY, fijados={"rionegro": "68615"})
        self.assertEqual((r.aclaracion, [m["divipola"] for m in r.municipios]), (None, ["68615"]))
        r = rs.resolver("acueducto en Rionegro, Boyacá", CAT, HOY, fijados={"rionegro": "05615"})
        self.assertEqual((r.aclaracion, [m["divipola"] for m in r.municipios]), (None, ["05615"]))
        r = rs.resolver(P, CAT, HOY, fijados={"rionegro": "99999"})  # codigo ajeno: se ignora y se aclara
        self.assertEqual(r.aclaracion["tipo"], "homonimo")


class Fallos(unittest.TestCase):
    def test_redis_caido_no_rompe_el_flujo(self):
        def fabrica():
            raise ConnectionError("caido")
        almacen = acl.AlmacenAclaraciones(fabrica_cliente=fabrica)
        s = Sesion()
        s.almacen = almacen
        r1 = s.decir(P)
        self.assertEqual((r1["accion"], r1["tipo"]), ("aclarar", "homonimo"))
        r2 = s.decir("el de Antioquia")  # sin estado: pregunta nueva, sin excepcion
        self.assertEqual(r2["accion"], "responder")

    def test_el_circuito_evita_reintentar_redis_a_cada_mensaje(self):
        llamadas = []

        def fabrica():
            llamadas.append(1)
            raise ConnectionError("caido")
        t = [0.0]
        almacen = acl.AlmacenAclaraciones(fabrica_cliente=fabrica, reloj=lambda: t[0])
        for _ in range(5):
            almacen.leer(7, 12)
        self.assertEqual(len(llamadas), 1)
        t[0] += acl.CIRCUITO_SEGUNDOS + 1
        almacen.leer(7, 12)
        self.assertEqual(len(llamadas), 2)

    def test_error_del_modelo_tras_resolver_restaura_el_estado_para_el_reintento(self):
        s = Sesion()
        s.decir(P)
        s.solicitar = Solicitar({"version_contrato": 2, "status": "error", "tipo": "ollama_no_disponible", "detail": "sin ollama"})
        r = s.decir("el de Antioquia")
        self.assertEqual(r["accion"], "error")
        self.assertIsNotNone(s.estado())
        s.solicitar = Solicitar(respuesta_ok("Respuesta del modelo."))
        self.assertEqual(s.decir("el de Antioquia")["accion"], "responder")
        self.assertEqual(s.pregunta_al_modelo(), P)

    def test_excepcion_del_rpc_tambien_restaura(self):
        s = Sesion()
        s.decir(P)

        async def falla(peticion, timeout=None):
            raise TimeoutError("rpc")
        s.solicitar = falla
        with self.assertRaises(TimeoutError):
            s.decir("el de Antioquia")
        self.assertIsNotNone(s.estado())


class Bandera(unittest.TestCase):
    def test_apagada_no_toca_el_almacen(self):
        from routers import chatbot as r
        payload = {"mensaje": P, "usuario_id": 7, "consentimiento_entrenamiento": False, "conversacion_id": 12}
        with patch.object(r.settings, "chatbot_orquestador_activo", False), \
                patch.object(r, "solicitar_chatbot", AsyncMock(return_value={"status": "ok", "response": "r"})), \
                patch.object(acl, "almacen_por_defecto", side_effect=AssertionError("no debe usarse")), \
                patch("redis.Redis.from_url", side_effect=AssertionError("sin Redis")):
            salida = correr(r._pedir_respuesta(payload, None, P))
        self.assertEqual(salida, {"status": "ok", "response": "r"})


if __name__ == "__main__":
    unittest.main()
