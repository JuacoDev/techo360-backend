"""Pruebas de services/chatbot_estados.verificar_capacidad contra un PostgreSQL DESECHABLE:
el conteo de mensajes en proceso por usuario y en todo el sistema.

Se saltan solas si no hay PG_BIN / MIGRACION_0002_SQL (ver services/tests_pg_desechable.py).

Uso:
    PG_BIN=C:/ruta/pgsql/bin MIGRACION_0002_SQL=C:/ruta/0002_mensajes_estado.sql \
        python -m services.tests_chatbot_capacidad_pg
"""
import unittest

from sqlalchemy import text

from services import chatbot_estados as est
# Base crea una base desechable por prueba (conversaciones 1 y 2 del usuario 7, 3 del usuario 8);
# setUpModule/tearDownModule levantan y paran el servidor.
from services.tests_chatbot_estados_pg import Base, setUpModule, tearDownModule  # noqa: F401


class TestVerificarCapacidad(Base):
    def _mensaje(self, conversacion_id, estado, rol="usuario"):
        with self.engine.begin() as con:
            con.execute(
                text("INSERT INTO mensajes (conversacion_id, rol, contenido, estado) VALUES (:c, :r, 'hola', :e)"),
                {"c": conversacion_id, "r": rol, "e": estado},
            )

    def _conversaciones_ajenas(self, n):
        with self.engine.begin() as con:
            for i in range(n):
                con.execute(
                    text("INSERT INTO conversaciones (id, usuario_id, consentimiento_entrenamiento) VALUES (:i, :u, false)"),
                    {"i": 100 + i, "u": 1000 + i},
                )

    def test_sin_mensajes_hay_lugar(self):
        est.verificar_capacidad(self.db, 7, 2, 10)

    def test_dos_en_proceso_del_usuario_lo_bloquean_aunque_esten_en_conversaciones_distintas(self):
        self._mensaje(1, "procesando")
        self._mensaje(2, "pendiente")
        with self.assertRaises(est.CapacidadAgotada) as c:
            est.verificar_capacidad(self.db, 7, 2, 10)
        self.assertEqual((c.exception.motivo, c.exception.limite), ("usuario", 2))

    def test_uno_en_proceso_aun_deja_enviar(self):
        self._mensaje(1, "procesando")
        est.verificar_capacidad(self.db, 7, 2, 10)

    def test_error_cancelado_respondido_y_los_del_asistente_no_cuentan(self):
        self._mensaje(1, "cancelado")  # uno sin resolver por conversacion como maximo (indice unico parcial)
        self._mensaje(1, "respondido")
        self._mensaje(2, "error")
        self._mensaje(2, "respondido", rol="asistente")
        est.verificar_capacidad(self.db, 7, 2, 10)

    def test_los_mensajes_de_otro_usuario_no_cuentan_para_su_tope(self):
        self._mensaje(1, "procesando")
        self._mensaje(2, "procesando")
        est.verificar_capacidad(self.db, 8, 2, 10)  # el usuario 8 no tiene ninguno

    def test_cola_global_llena(self):
        self._conversaciones_ajenas(10)
        for i in range(10):
            self._mensaje(100 + i, "procesando")
        with self.assertRaises(est.CapacidadAgotada) as c:
            est.verificar_capacidad(self.db, 8, 2, 10)
        self.assertEqual((c.exception.motivo, c.exception.limite), ("cola", 10))

    def test_nueve_en_cola_todavia_hay_lugar(self):
        self._conversaciones_ajenas(9)
        for i in range(9):
            self._mensaje(100 + i, "pendiente")
        est.verificar_capacidad(self.db, 8, 2, 10)

    def test_cero_desactiva_ambos_topes(self):
        self._conversaciones_ajenas(12)
        for i in range(12):
            self._mensaje(100 + i, "procesando")
        self._mensaje(1, "procesando")
        self._mensaje(2, "procesando")
        est.verificar_capacidad(self.db, 7, 0, 0)

    def test_un_procesando_colgado_ya_no_cuenta_despues_de_barrer(self):
        self._mensaje(1, "procesando")
        self._mensaje(2, "procesando")
        with self.engine.begin() as con:
            con.execute(text("UPDATE mensajes SET actualizado_en = now() - interval '10 minutes' WHERE conversacion_id = 1"))
        est.barrer_colgados(self.db)
        est.verificar_capacidad(self.db, 7, 2, 10)  # solo queda 1 en proceso

    def test_crear_pendiente_y_marcar_procesando_cuentan(self):
        m1 = est.marcar_procesando(self.db, est.crear_pendiente(self.db, 1, "a").id)
        est.crear_pendiente(self.db, 2, "b")
        self.assertEqual(m1.estado, "procesando")
        with self.assertRaises(est.CapacidadAgotada):
            est.verificar_capacidad(self.db, 7, 2, 10)
        est.cerrar_respondido(self.db, m1.id, "respuesta")  # al responder se libera un lugar
        est.verificar_capacidad(self.db, 7, 2, 10)


if __name__ == "__main__":
    unittest.main()
