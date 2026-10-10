"""Pruebas del consumer de 'chatbot_eventos_persistencia' con DOBLES: sin base de
datos ni RabbitMQ. Cubren la verificacion de propiedad y de esquema (SEGURIDAD):
conversacion ajena, conversacion inexistente, esquema invalido, y el caso legitimo.

Uso:
    python -m services.tests_chatbot_persistencia_consumer
"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import chatbot_persistencia_consumer as consumer

DUENIO = 7
OTRO = 8


class _Mensaje:
    """Doble de AbstractIncomingMessage: registra si process() salio con o sin error.
    aio-pika hace ack al salir sin error y reject/requeue si sale con excepcion."""

    def __init__(self, cuerpo):
        self.body = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        self.salida = None

    def process(self):
        mensaje = self

        class _Ctx:
            async def __aenter__(self):
                return mensaje

            async def __aexit__(self, tipo, valor, tb):
                mensaje.salida = "ack" if tipo is None else "error"
                return False

        return _Ctx()


class _Db:
    def __init__(self):
        self.cerrada = False

    def close(self):
        self.cerrada = True


def _evento(**cambios):
    base = {
        "tipo": "intercambio_completo",
        "usuario_id": DUENIO,
        "consentimiento_entrenamiento": True,
        "mensaje_usuario": "hola",
        "respuesta_asistente": "respuesta",
    }
    base.update(cambios)
    return {k: v for k, v in base.items() if v is not ...}


class ConsumerPersistencia(unittest.TestCase):
    def setUp(self):
        consumer.DESCARTADOS.clear()
        self.escritos = []
        self.creadas = []
        self.conversaciones = {1: DUENIO}  # id -> dueno
        self.db = _Db()

        def agregar(db, cid, rol, contenido):
            self.escritos.append((cid, rol, contenido))

        def crear(db, uid, consentimiento):
            self.creadas.append((uid, consentimiento))
            return SimpleNamespace(id=99)

        def de_usuario(db, cid, uid):
            return SimpleNamespace(id=cid) if self.conversaciones.get(cid) == uid else None

        self.parches = [
            patch.object(consumer, "agregar_mensaje", agregar),
            patch.object(consumer, "crear_conversacion", crear),
            patch.object(consumer, "conversacion_de_usuario", de_usuario),
            patch.object(consumer, "SessionLocalVector", lambda: self.db),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def procesar(self, cuerpo):
        mensaje = _Mensaje(cuerpo)
        with self.assertLogs(consumer.logger, level="DEBUG") as registro:
            consumer.logger.debug("inicio")  # assertLogs exige al menos un registro
            asyncio.run(consumer._procesar_evento(mensaje))
        return mensaje, "\n".join(registro.output)

    def test_legitimo_con_conversacion_propia(self):
        mensaje, _ = self.procesar(_evento(conversacion_id=1))
        self.assertEqual(self.escritos, [(1, "usuario", "hola"), (1, "asistente", "respuesta")])
        self.assertEqual(mensaje.salida, "ack")
        self.assertEqual(sum(consumer.DESCARTADOS.values()), 0)
        self.assertTrue(self.db.cerrada)

    def test_legitimo_sin_conversacion_crea_una_del_usuario(self):
        self.procesar(_evento())
        self.assertEqual(self.creadas, [(DUENIO, True)])
        self.assertEqual([e[0] for e in self.escritos], [99, 99])

    def test_conversacion_ajena_se_descarta_sin_escribir(self):
        mensaje, log = self.procesar(_evento(usuario_id=OTRO, conversacion_id=1, mensaje_usuario="SECRETO-123"))
        self.assertEqual(self.escritos, [])
        self.assertEqual(mensaje.salida, "ack", "ack: no se reintenta un evento malo")
        self.assertEqual(consumer.DESCARTADOS["conversacion_ajena_o_inexistente"], 1)
        self.assertNotIn("SECRETO-123", log, "el log no lleva el contenido del mensaje")
        self.assertIn("conversacion_id=1", log)

    def test_conversacion_inexistente_se_descarta(self):
        mensaje, _ = self.procesar(_evento(conversacion_id=12345))
        self.assertEqual(self.escritos, [])
        self.assertEqual(self.creadas, [], "no crea una conversacion nueva en su lugar")
        self.assertEqual(mensaje.salida, "ack")
        self.assertEqual(consumer.DESCARTADOS["conversacion_ajena_o_inexistente"], 1)

    def test_esquemas_invalidos_se_descartan_con_su_motivo(self):
        casos = {
            b"no es json": "json_invalido",
            json.dumps([1, 2]).encode(): "no_es_objeto",
            json.dumps(_evento(tipo="otro")).encode(): "tipo_desconocido",
            json.dumps(_evento(usuario_id=...)).encode(): "usuario_id_invalido",
            json.dumps(_evento(usuario_id="7")).encode(): "usuario_id_invalido",
            json.dumps(_evento(usuario_id=True)).encode(): "usuario_id_invalido",
            json.dumps(_evento(usuario_id=0)).encode(): "usuario_id_invalido",
            json.dumps(_evento(consentimiento_entrenamiento="si")).encode(): "consentimiento_invalido",
            json.dumps(_evento(mensaje_usuario="")).encode(): "mensaje_usuario_invalido",
            json.dumps(_evento(mensaje_usuario=5)).encode(): "mensaje_usuario_invalido",
            json.dumps(_evento(respuesta_asistente="x" * 20001)).encode(): "respuesta_asistente_invalido",
            json.dumps(_evento(conversacion_id="1")).encode(): "conversacion_id_invalido",
            json.dumps(_evento(conversacion_id=-3)).encode(): "conversacion_id_invalido",
        }
        for cuerpo, motivo in casos.items():
            with self.subTest(motivo=motivo, cuerpo=cuerpo[:60]):
                consumer.DESCARTADOS.clear()
                mensaje, _ = self.procesar(cuerpo)
                self.assertEqual(dict(consumer.DESCARTADOS), {motivo: 1})
                self.assertEqual(mensaje.salida, "ack")
        self.assertEqual(self.escritos, [])
        self.assertEqual(self.creadas, [])

    def test_validar_evento_devuelve_los_campos(self):
        e = consumer.validar_evento(_evento(conversacion_id=1))
        self.assertEqual((e.usuario_id, e.conversacion_id, e.consentimiento_entrenamiento), (DUENIO, 1, True))

    def test_fallo_de_bd_no_reintenta(self):
        def roto(*_):
            raise RuntimeError("bd caida")

        with patch.object(consumer, "agregar_mensaje", roto):
            mensaje, _ = self.procesar(_evento(conversacion_id=1))
        self.assertEqual(mensaje.salida, "ack")
        self.assertTrue(self.db.cerrada)


if __name__ == "__main__":
    unittest.main()
