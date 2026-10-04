"""Pruebas de services/chatbot_estados.py contra un PostgreSQL DESECHABLE (fase C).

Necesitan Postgres de verdad (indice unico parcial, UPDATE ... RETURNING, carreras
entre conexiones): se saltan solas si no hay PG_BIN / MIGRACION_0002_SQL.

Uso:
    PG_BIN=C:/ruta/pgsql/bin MIGRACION_0002_SQL=C:/ruta/0002_mensajes_estado.sql \
        python -m services.tests_chatbot_estados_pg
"""
import threading
import time
import unittest
import uuid

from sqlalchemy import create_engine, text
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
