"""Pruebas de services/chatbot_estados.py contra un PostgreSQL DESECHABLE (fase C).

Necesitan Postgres de verdad (indice unico parcial, UPDATE ... RETURNING, carreras
entre conexiones): se saltan solas si no hay PG_BIN / MIGRACION_0002_SQL.

Uso:
    PG_BIN=C:/ruta/pgsql/bin MIGRACION_0002_SQL=C:/ruta/0002_mensajes_estado.sql \
        python -m services.tests_chatbot_estados_pg
"""
import asyncio
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services import chatbot_estados as est
from services import tests_pg_desechable as pg

SALTAR = not pg.disponible()
_servidor = None


def setUpModule():
    global _servidor
    if not SALTAR:
        _servidor = pg.ServidorDesechable(__import__("os").environ["PG_BIN"])


def tearDownModule():
    if _servidor is not None:
        _servidor.parar()


@unittest.skipIf(SALTAR, "sin PG_BIN / MIGRACION_0002_SQL: pruebas contra Postgres desechable omitidas")
class Base(unittest.TestCase):
    def setUp(self):
        nombre = "t_" + uuid.uuid4().hex[:10]
        self.url = pg.crear_base_con_esquema(_servidor, nombre)
        self.engine = create_engine(self.url)
        self.Sesion = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Sesion()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        with self.engine.begin() as con:
            con.execute(text("INSERT INTO conversaciones (id, usuario_id, consentimiento_entrenamiento) VALUES (1, 7, false), (2, 7, false), (3, 8, false)"))

    def estado(self, mensaje_id):
        return self.db.execute(text("SELECT estado FROM mensajes WHERE id = :i"), {"i": mensaje_id}).scalar()

    def contar(self, conversacion_id=None, rol=None):
        sql = "SELECT count(*) FROM mensajes WHERE true"
        params = {}
        if conversacion_id:
            sql += " AND conversacion_id = :c"; params["c"] = conversacion_id
        if rol:
            sql += " AND rol = :r"; params["r"] = rol
        return self.db.execute(text(sql), params).scalar()


class TestFlujo(Base):
    def test_flujo_feliz_pendiente_procesando_respondido(self):
        m = est.crear_pendiente(self.db, 1, "¿Qué es el score?")
        self.assertEqual(m.estado, "pendiente")
        m = est.marcar_procesando(self.db, m.id)
        self.assertEqual(m.estado, "procesando")
        usuario, asistente = est.cerrar_respondido(self.db, m.id, "Es un indicador.")
        self.assertEqual((usuario.estado, asistente.rol, asistente.estado, asistente.contenido), ("respondido", "asistente", "respondido", "Es un indicador."))
        self.assertEqual(self.contar(1), 2)

    def test_un_error_deja_el_mensaje_con_su_detalle_y_bloquea(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "hola").id)
        e = est.marcar_error(self.db, m.id, est.DETALLE_TIMEOUT)
        self.assertEqual((e.estado, e.error_detalle), ("error", est.DETALLE_TIMEOUT))
        with self.assertRaises(est.ConversacionBloqueada) as ctx:
            est.crear_pendiente(self.db, 1, "otra")
        self.assertEqual(ctx.exception.mensaje_pendiente.id, m.id)
        self.assertEqual(self.contar(1, "usuario"), 1)  # la segunda NO se guardo

    def test_otra_conversacion_del_mismo_usuario_no_esta_bloqueada(self):
        est.crear_pendiente(self.db, 1, "a")
        est.crear_pendiente(self.db, 2, "b")
        self.assertEqual(self.contar(rol="usuario"), 2)

    def test_cancelado_sigue_bloqueando(self):
        m = est.crear_pendiente(self.db, 1, "a")
        est.cancelar(self.db, m.id, 7)
        with self.assertRaises(est.ConversacionBloqueada):
            est.crear_pendiente(self.db, 1, "b")

    def test_respondido_o_eliminar_desbloquean(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        est.cerrar_respondido(self.db, m.id, "r")
        est.crear_pendiente(self.db, 1, "b")  # ya no bloquea
        m2 = est.mensaje_sin_resolver(self.db, 1)
        self.assertEqual(est.eliminar(self.db, m2.id, 7), "eliminado")
        est.crear_pendiente(self.db, 1, "c")

    def test_cerrar_respondido_descarta_la_respuesta_si_se_cancelo_mientras_tanto(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        est.cancelar(self.db, m.id, 7)
        self.assertIsNone(est.cerrar_respondido(self.db, m.id, "llego tarde"))
        self.assertEqual(self.estado(m.id), "cancelado")
        self.assertEqual(self.contar(1, "asistente"), 0)  # NO se guardo la respuesta

    def test_marcar_error_no_pisa_un_cancelado(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        est.cancelar(self.db, m.id, 7)
        self.assertIsNone(est.marcar_error(self.db, m.id, "x"))
        self.assertEqual(self.estado(m.id), "cancelado")

    def test_reintentar_desde_error_y_desde_cancelado_reusa_el_mismo_mensaje(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "a").id, "boom")
        r = est.reintentar(self.db, m.id, 7)
        self.assertEqual((r.id, r.estado, r.error_detalle), (m.id, "procesando", None))
        est.cancelar(self.db, m.id, 7)
        self.assertEqual(est.reintentar(self.db, m.id, 7).estado, "procesando")
        self.assertEqual(self.contar(1, "usuario"), 1)  # nunca se creo otro

    def test_reintentar_un_respondido_o_uno_en_proceso_no_hace_nada(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        self.assertIsNone(est.reintentar(self.db, m.id, 7))  # procesando
        est.cerrar_respondido(self.db, m.id, "r")
        self.assertIsNone(est.reintentar(self.db, m.id, 7))  # respondido

    def test_los_mensajes_de_otro_usuario_no_se_tocan(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "a").id, "x")  # conversacion 1 es del usuario 7
        self.assertIsNone(est.reintentar(self.db, m.id, 8))
        self.assertIsNone(est.cancelar(self.db, m.id, 8))
        self.assertEqual(est.eliminar(self.db, m.id, 8), "no_encontrado")
        self.assertIsNone(est.obtener_de_usuario(self.db, m.id, 8))
        self.assertEqual(self.estado(m.id), "error")

    def test_eliminar_segun_estado(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        self.assertEqual(est.eliminar(self.db, m.id, 7), "en_proceso")  # hay que cancelarlo antes
        est.cancelar(self.db, m.id, 7)
        self.assertEqual(est.eliminar(self.db, m.id, 7), "eliminado")
        self.assertEqual(est.eliminar(self.db, m.id, 7), "no_encontrado")
        r = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "b").id)
        est.cerrar_respondido(self.db, r.id, "x")
        self.assertEqual(est.eliminar(self.db, r.id, 7), "no_encontrado")  # con respuesta: no se elimina

    def test_el_asistente_nunca_se_cancela_ni_elimina_ni_reintenta(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        _, asistente = est.cerrar_respondido(self.db, m.id, "r")
        self.assertIsNone(est.cancelar(self.db, asistente.id, 7))
        self.assertIsNone(est.reintentar(self.db, asistente.id, 7))
        self.assertEqual(est.eliminar(self.db, asistente.id, 7), "no_encontrado")


class TestBarrido(Base):
    def test_barrer_colgados_pasa_a_error_solo_los_viejos(self):
        viejo = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "viejo").id)
        reciente = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 2, "reciente").id)
        self.db.execute(text("UPDATE mensajes SET actualizado_en = now() - interval '10 minutes' WHERE id = :i"), {"i": viejo.id})
        self.db.commit()
        self.assertEqual(est.barrer_colgados(self.db), 1)
        self.assertEqual(self.estado(viejo.id), "error")
        self.assertEqual(self.estado(reciente.id), "procesando")
        detalle = self.db.execute(text("SELECT error_detalle FROM mensajes WHERE id = :i"), {"i": viejo.id}).scalar()
        self.assertEqual(detalle, est.DETALLE_COLGADO)

    def test_barrer_colgados_no_toca_error_cancelado_ni_respondido(self):
        for estado in ("error", "cancelado"):
            m = est.crear_pendiente(self.db, 1, estado)
            self.db.execute(text("UPDATE mensajes SET estado = :e, actualizado_en = now() - interval '1 day' WHERE id = :i"), {"e": estado, "i": m.id})
            self.db.commit()
            self.assertEqual(est.barrer_colgados(self.db), 0)
            self.assertEqual(self.estado(m.id), estado)
            est.eliminar(self.db, m.id, 7)

    def test_barrer_al_arrancar_pasa_todo_pendiente_y_procesando_a_error(self):
        a = est.crear_pendiente(self.db, 1, "a")  # pendiente
        b = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 2, "b").id)
        self.assertEqual(est.barrer_al_arrancar(self.db), 2)
        self.assertEqual((self.estado(a.id), self.estado(b.id)), ("error", "error"))
        detalle = self.db.execute(text("SELECT error_detalle FROM mensajes WHERE id = :i"), {"i": a.id}).scalar()
        self.assertEqual(detalle, est.DETALLE_REINICIO)


class TestCarreras(Base):
    def _en_paralelo(self, n, tarea):
        resultados, barrera = [], threading.Barrier(n)

        def correr(i):
            sesion = self.Sesion()
            try:
                barrera.wait()
                resultados.append(tarea(sesion, i))
            except Exception as error:  # el resultado de la tarea incluye la excepcion
                resultados.append(error)
            finally:
                sesion.close()

        hilos = [threading.Thread(target=correr, args=(i,)) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(30)
        return resultados

    def test_ocho_envios_simultaneos_a_la_misma_conversacion_solo_uno_gana(self):
        resultados = self._en_paralelo(8, lambda s, i: est.crear_pendiente(s, 1, f"m{i}"))
        ganadores = [r for r in resultados if not isinstance(r, Exception)]
        bloqueados = [r for r in resultados if isinstance(r, est.ConversacionBloqueada)]
        self.assertEqual((len(ganadores), len(bloqueados)), (1, 7), str(resultados))
        self.assertEqual(self.contar(1, "usuario"), 1)

    def test_el_indice_unico_cubre_la_carrera_aunque_la_pregunta_previa_no_vea_nada(self):
        # Simula que dos peticiones pasaron la comprobacion previa a la vez: la BD rechaza la segunda.
        est.crear_pendiente(self.db, 1, "primero")
        original = est.mensaje_sin_resolver
        llamadas = []

        def ciega_la_primera_vez(db, conversacion_id):
            llamadas.append(1)
            return None if len(llamadas) == 1 else original(db, conversacion_id)

        est.mensaje_sin_resolver = ciega_la_primera_vez
        try:
            with self.assertRaises(est.ConversacionBloqueada) as ctx:
                est.crear_pendiente(self.db, 1, "segundo")
        finally:
            est.mensaje_sin_resolver = original
        self.assertIsNotNone(ctx.exception.mensaje_pendiente)
        self.assertEqual(self.contar(1, "usuario"), 1)

    def test_cinco_reintentos_simultaneos_solo_uno_gana(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "a").id, "x")
        resultados = self._en_paralelo(5, lambda s, i: est.reintentar(s, m.id, 7))
        self.assertEqual(sum(1 for r in resultados if r is not None and not isinstance(r, Exception)), 1, str(resultados))
        self.assertEqual(self.estado(m.id), "procesando")

    def test_cancelar_y_cerrar_a_la_vez_nunca_dejan_respuesta_con_mensaje_cancelado(self):
        for _ in range(15):
            m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
            res = self._en_paralelo(2, lambda s, i: est.cancelar(s, m.id, 7) if i == 0 else est.cerrar_respondido(s, m.id, "r"))
            self.assertFalse(any(isinstance(r, Exception) for r in res), str(res))
            estado = self.estado(m.id)
            asistentes = self.contar(1, "asistente")
            if estado == "respondido":
                self.assertEqual(asistentes, 1)  # gano cerrar: hay respuesta
                est.eliminar(self.db, m.id, 7)  # no se puede: tiene respuesta
                self.db.execute(text("DELETE FROM mensajes WHERE conversacion_id = 1")); self.db.commit()
            else:
                self.assertEqual((estado, asistentes), ("cancelado", 0))  # gano cancelar: NO hay respuesta
                est.eliminar(self.db, m.id, 7)

    def test_dos_eliminar_simultaneos_uno_borra_y_el_otro_no(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "a").id, "x")
        res = self._en_paralelo(2, lambda s, i: est.eliminar(s, m.id, 7))
        self.assertEqual(sorted(res), ["eliminado", "no_encontrado"])


class TestIntentosYErrores(Base):
    def test_intentos_se_incrementa_en_cada_paso_a_procesando(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        self.assertEqual(m.intentos, 1)
        est.cancelar(self.db, m.id, 7)
        self.assertEqual(est.reintentar(self.db, m.id, 7).intentos, 2)

    def test_marcar_error_y_cerrar_solo_actuan_sobre_el_intento_vigente(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)  # intento 1
        est.cancelar(self.db, m.id, 7)
        est.reintentar(self.db, m.id, 7)  # intento 2 vigente
        self.assertIsNone(est.marcar_error(self.db, m.id, "tarde", intento=1))
        self.assertIsNone(est.cerrar_respondido(self.db, m.id, "vieja", intento=1))
        self.assertEqual(self.estado(m.id), "procesando")
        self.assertEqual(self.contar(1, "asistente"), 0)
        self.assertIsNotNone(est.cerrar_respondido(self.db, m.id, "nueva", intento=2))
        self.assertEqual(self.estado(m.id), "respondido")

    def test_un_error_de_integridad_que_no_es_el_indice_unico_no_se_confunde_con_un_409(self):
        with self.assertRaises(IntegrityError):  # conversacion inexistente: viola la FK
            est.crear_pendiente(self.db, 424242, "a")

    def test_error_detalle_se_acota_a_500_caracteres(self):
        m = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        e = est.marcar_error(self.db, m.id, "x" * 900)
        self.assertEqual(len(e.error_detalle), 500)


class TestRouterConPostgresReal(Base):
    """El router completo sobre Postgres de verdad: dos usuarios, pool y RPC cancelado vivo."""

    def setUp(self):
        super().setUp()
        from db.session import get_db
        from db.session_vector import get_db_vector
        from routers import chatbot as router_chatbot
        from routers.auth import get_current_user

        self.router = router_chatbot
        # Motor propio del router con UNA sola conexion: si algo la retiene durante el RPC, se nota.
        self.engine_router = create_engine(self.url, pool_size=1, max_overflow=0, pool_timeout=3)
        self.addCleanup(self.engine_router.dispose)
        SesionRouter = sessionmaker(bind=self.engine_router, autoflush=False)
        self.usuario = SimpleNamespace(id=7)

        def db_vector():
            sesion = SesionRouter()
            try:
                yield sesion
            finally:
                sesion.close()

        app = FastAPI()
        app.include_router(router_chatbot.router)
        app.dependency_overrides[get_db_vector] = db_vector
        app.dependency_overrides[get_db] = lambda: SimpleNamespace(rollback=lambda: None)
        app.dependency_overrides[get_current_user] = lambda: self.usuario
        self.cliente = TestClient(app)
        self.rpc_llamadas = []
        self.guion = []  # un comportamiento por llamada al RPC (en orden)

        async def rpc(payload):
            self.rpc_llamadas.append(payload)
            comportamiento = self.guion.pop(0) if self.guion else {"respuesta": {"status": "ok", "response": "ok"}}
            if "alcanzado" in comportamiento:
                comportamiento["alcanzado"].set()
            if "espera" in comportamiento:
                await asyncio.get_running_loop().run_in_executor(None, comportamiento["espera"].wait, 20)
            if "durante" in comportamiento:
                comportamiento["durante"]()
            if "error" in comportamiento:
                raise comportamiento["error"]
            return comportamiento["respuesta"]

        for p in (
            patch.object(router_chatbot, "solicitar_chatbot", rpc),
            patch.object(router_chatbot.settings, "chatbot_bloqueo_pendientes", True),
        ):
            p.start()
            self.addCleanup(p.stop)

    def enviar(self, texto="hola", conversacion=1):
        return self.cliente.post(f"/chatbot/conversaciones/{conversacion}/mensajes/enviar", json={"contenido": texto})

    def esperar_estado(self, mensaje_id, estado, limite=10):
        t0 = time.time()
        while time.time() - t0 < limite:
            if self.estado(mensaje_id) == estado:
                return
            time.sleep(0.05)
        self.fail(f"el mensaje {mensaje_id} no llego a '{estado}' (esta en '{self.estado(mensaje_id)}')")

    # ---------------------------------------------------------------- autorizacion con DOS usuarios
    def test_dos_usuarios_reintentar_cancelar_eliminar_ajenos_dan_404_identico_al_de_un_id_inexistente(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "pregunta privada del usuario 7").id, "x")  # conversacion 1 = usuario 7
        self.usuario = SimpleNamespace(id=8)  # ahora actua el usuario 8
        for metodo, ruta in (("post", "/chatbot/mensajes/{}/reintentar"), ("post", "/chatbot/mensajes/{}/cancelar"), ("delete", "/chatbot/mensajes/{}")):
            ajeno = getattr(self.cliente, metodo)(ruta.format(m.id))
            inexistente = getattr(self.cliente, metodo)(ruta.format(987654))
            self.assertEqual(ajeno.status_code, 404, ruta)
            self.assertEqual((ajeno.status_code, ajeno.content), (inexistente.status_code, inexistente.content), ruta)
            self.assertNotIn("privada", ajeno.text)
        self.assertEqual(self.estado(m.id), "error")  # intacto
        self.assertEqual(self.rpc_llamadas, [])  # y no se llamo al asistente
        self.assertEqual(self.enviar("hola", conversacion=1).status_code, 404)  # tampoco puede escribir en la conversacion ajena

    def test_dos_usuarios_un_mensaje_ajeno_en_cualquier_estado_nunca_da_409(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "a").id, "x")
        self.usuario = SimpleNamespace(id=8)
        for estado in ("error", "cancelado", "procesando", "pendiente"):
            self.db.execute(text("UPDATE mensajes SET estado = :e WHERE id = :i"), {"e": estado, "i": m.id})
            self.db.commit()
            for metodo, ruta in (("post", f"/chatbot/mensajes/{m.id}/reintentar"), ("post", f"/chatbot/mensajes/{m.id}/cancelar"), ("delete", f"/chatbot/mensajes/{m.id}")):
                self.assertEqual(getattr(self.cliente, metodo)(ruta).status_code, 404, f"{estado} {ruta}")

    def test_dos_usuarios_cada_uno_ve_y_resuelve_solo_lo_suyo(self):
        a = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "del 7").id, "x")
        b = est.marcar_error(self.db, est.crear_pendiente(self.db, 3, "del 8").id, "x")
        self.usuario = SimpleNamespace(id=8)
        self.assertEqual(self.cliente.post(f"/chatbot/mensajes/{b.id}/cancelar").status_code, 200)
        self.assertEqual(self.cliente.post(f"/chatbot/mensajes/{a.id}/cancelar").status_code, 404)
        self.assertEqual((self.estado(a.id), self.estado(b.id)), ("error", "cancelado"))

    # ---------------------------------------------------------------- sesiones / pool
    def test_ninguna_conexion_del_pool_queda_retenida_durante_el_rpc(self):
        en_vuelo = []
        self.guion.append({"durante": lambda: en_vuelo.append(self.engine_router.pool.checkedout()), "respuesta": {"status": "ok", "response": "r"}})
        r = self.enviar("hola")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(en_vuelo, [0], "durante el RPC el pool (1 conexion) debe estar libre")

    def test_reintentar_tampoco_retiene_la_conexion_durante_el_rpc(self):
        m = est.marcar_error(self.db, est.crear_pendiente(self.db, 1, "a").id, "x")
        en_vuelo = []
        self.guion.append({"durante": lambda: en_vuelo.append(self.engine_router.pool.checkedout()), "respuesta": {"status": "ok", "response": "r"}})
        self.assertEqual(self.cliente.post(f"/chatbot/mensajes/{m.id}/reintentar").status_code, 200)
        self.assertEqual(en_vuelo, [0])

    # ---------------------------------------------------------------- RPC cancelado que sigue vivo
    def _enviar_en_hilo(self, texto, resultado):
        hilo = threading.Thread(target=lambda: resultado.append(self.enviar(texto)))
        hilo.start()
        return hilo

    def _id_del_mensaje_de_usuario(self):
        return self.db.execute(text("SELECT id FROM mensajes WHERE conversacion_id = 1 AND rol = 'usuario'")).scalar()

    def test_cancelar_y_reintentar_con_el_rpc_viejo_vivo_que_falla_mientras_el_nuevo_sigue_en_vuelo(self):
        # El escenario del hallazgo 1: sin identificar el intento, el error tardio del RPC viejo
        # pasaba el mensaje a 'error' y el reintento (aun en vuelo) ya no podia cerrarlo.
        from services.chatbot_rpc_client import ChatbotTimeoutError

        espera_a, alcanzado_a = threading.Event(), threading.Event()
        espera_b, alcanzado_b = threading.Event(), threading.Event()
        self.guion += [
            {"espera": espera_a, "alcanzado": alcanzado_a, "error": ChatbotTimeoutError("tarde")},  # RPC-A (viejo)
            {"espera": espera_b, "alcanzado": alcanzado_b, "respuesta": {"status": "ok", "response": "respuesta B"}},  # RPC-B (reintento)
        ]
        res_a, res_b = [], []
        hilo_a = self._enviar_en_hilo("lento", res_a)
        self.assertTrue(alcanzado_a.wait(10))
        m_id = self._id_del_mensaje_de_usuario()
        self.esperar_estado(m_id, "procesando")
        self.assertEqual(self.cliente.post(f"/chatbot/mensajes/{m_id}/cancelar").status_code, 200)
        hilo_b = threading.Thread(target=lambda: res_b.append(self.cliente.post(f"/chatbot/mensajes/{m_id}/reintentar")))
        hilo_b.start()
        self.assertTrue(alcanzado_b.wait(10))
        espera_a.set()  # el RPC viejo falla AHORA, con el reintento aun esperando
        hilo_a.join(15)
        self.assertEqual(res_a[0].status_code, 409)  # su resultado se descarta
        self.assertEqual(self.estado(m_id), "procesando")  # el reintento NO fue pisado
        espera_b.set()
        hilo_b.join(15)
        self.assertEqual((res_b[0].status_code, res_b[0].json()["mensaje_asistente"]["contenido"]), (200, "respuesta B"))
        self.assertEqual(self.estado(m_id), "respondido")
        asistentes = self.db.execute(text("SELECT contenido FROM mensajes WHERE conversacion_id = 1 AND rol = 'asistente'")).scalars().all()
        self.assertEqual(asistentes, ["respuesta B"])

    def test_el_rpc_viejo_que_falla_despues_de_que_el_reintento_ya_respondio_no_cambia_nada(self):
        from services.chatbot_rpc_client import ChatbotTimeoutError

        espera_a, alcanzado_a = threading.Event(), threading.Event()
        self.guion += [
            {"espera": espera_a, "alcanzado": alcanzado_a, "error": ChatbotTimeoutError("tarde")},
            {"respuesta": {"status": "ok", "response": "respuesta B"}},
        ]
        res_a = []
        hilo = self._enviar_en_hilo("lento", res_a)
        self.assertTrue(alcanzado_a.wait(10))
        m_id = self._id_del_mensaje_de_usuario()
        self.esperar_estado(m_id, "procesando")
        self.cliente.post(f"/chatbot/mensajes/{m_id}/cancelar")
        r_b = self.cliente.post(f"/chatbot/mensajes/{m_id}/reintentar")
        self.assertEqual(r_b.status_code, 200)
        espera_a.set()
        hilo.join(15)
        self.assertEqual(res_a[0].status_code, 409)
        self.assertEqual(self.estado(m_id), "respondido")
        self.assertEqual(self.contar(1, "asistente"), 1)

    def test_cancelar_y_reintentar_con_el_rpc_viejo_vivo_que_responde_antes_que_el_nuevo(self):
        espera_a, alcanzado_a = threading.Event(), threading.Event()
        espera_b, alcanzado_b = threading.Event(), threading.Event()
        self.guion += [
            {"espera": espera_a, "alcanzado": alcanzado_a, "respuesta": {"status": "ok", "response": "respuesta A (vieja)"}},
            {"espera": espera_b, "alcanzado": alcanzado_b, "respuesta": {"status": "ok", "response": "respuesta B (nueva)"}},
        ]
        res_a, res_b = [], []
        hilo_a = self._enviar_en_hilo("lento", res_a)
        self.assertTrue(alcanzado_a.wait(10))
        m_id = self._id_del_mensaje_de_usuario()
        self.esperar_estado(m_id, "procesando")
        self.cliente.post(f"/chatbot/mensajes/{m_id}/cancelar")
        hilo_b = threading.Thread(target=lambda: res_b.append(self.cliente.post(f"/chatbot/mensajes/{m_id}/reintentar")))
        hilo_b.start()
        self.assertTrue(alcanzado_b.wait(10))
        espera_a.set()  # el viejo responde PRIMERO
        hilo_a.join(15)
        self.assertEqual(res_a[0].status_code, 409)
        self.assertEqual(self.estado(m_id), "procesando")  # el intento nuevo sigue esperando
        self.assertEqual(self.contar(1, "asistente"), 0)  # la respuesta vieja NO se guardo
        espera_b.set()
        hilo_b.join(15)
        self.assertEqual((res_b[0].status_code, res_b[0].json()["mensaje_asistente"]["contenido"]), (200, "respuesta B (nueva)"))
        self.assertEqual(self.estado(m_id), "respondido")
        self.assertEqual(self.contar(1, "asistente"), 1)

    def test_el_rpc_cancelado_que_responde_despues_no_guarda_nada(self):
        espera, alcanzado = threading.Event(), threading.Event()
        self.guion.append({"espera": espera, "alcanzado": alcanzado, "respuesta": {"status": "ok", "response": "llego tarde"}})
        res = []
        hilo = self._enviar_en_hilo("lento", res)
        self.assertTrue(alcanzado.wait(10))
        m_id = self._id_del_mensaje_de_usuario()
        self.esperar_estado(m_id, "procesando")
        self.cliente.post(f"/chatbot/mensajes/{m_id}/cancelar")
        espera.set()
        hilo.join(15)
        self.assertEqual((res[0].status_code, res[0].json()["mensaje_usuario"]["estado"]), (409, "cancelado"))
        self.assertEqual((self.estado(m_id), self.contar(1, "asistente")), ("cancelado", 0))

    # ---------------------------------------------------------------- respuestas invalidas
    def test_respuesta_invalida_del_asistente_deja_el_mensaje_en_error_no_procesando(self):
        for rara in ({"status": "ok"}, {"status": "ok", "response": None}, "texto"):
            self.guion.append({"respuesta": rara})
            r = self.enviar("hola")
            self.assertEqual(r.status_code, 502, str(rara))
            self.assertEqual(r.json()["mensaje_usuario"]["estado"], "error")
            self.db.execute(text("DELETE FROM mensajes WHERE conversacion_id = 1"))
            self.db.commit()

    def test_camino_feliz_y_bloqueo_409_con_el_router_real(self):
        self.guion.append({"respuesta": {"status": "ok", "response": "r1"}})
        self.assertEqual(self.enviar("uno").status_code, 201)
        self.guion.append({"error": ConnectionError("rabbit")})
        self.assertEqual(self.enviar("dos").status_code, 503)
        r = self.enviar("tres")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["mensaje_pendiente"]["estado"], "error")
        self.assertEqual(self.contar(1, "usuario"), 2)  # "tres" no se guardo


class TestCompatibilidad(Base):
    def test_un_mensaje_del_camino_viejo_queda_respondido_y_no_bloquea(self):
        with self.engine.begin() as con:  # exactamente el INSERT del backend actual (sin estado)
            con.execute(text("INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES (1, 'usuario', 'camino viejo')"))
        self.assertIsNone(est.mensaje_sin_resolver(self.db, 1))
        est.crear_pendiente(self.db, 1, "nuevo")  # no hay bloqueo


if __name__ == "__main__":
    if SALTAR:
        print("[omitidas: define PG_BIN y MIGRACION_0002_SQL]")
    unittest.main()
