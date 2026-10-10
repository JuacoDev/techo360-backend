"""Pruebas del interruptor DESACTIVAR_TAREAS_DE_FONDO (modo local) sobre el evento startup de
main.py, con DOBLES: sin base de datos, Redis ni RabbitMQ.

Uso:
    python -m routers.tests_tareas_de_fondo
"""
import asyncio
import contextlib
import io
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "prueba")
os.environ.setdefault("BREVO_API_KEY", "prueba")

import main  # noqa: E402
from core.config import Settings  # noqa: E402

FRASE = "modo local: tareas de fondo desactivadas"


class Dobles:
    """Reemplaza todo lo que arranca el evento startup y recuerda si se llamo."""

    def __init__(self):
        self.parches = {
            "aioredis": patch.object(main.aioredis, "from_url", MagicMock(return_value=MagicMock())),
            "scheduler": patch.object(main, "iniciar_scheduler", MagicMock()),
            "esquema": patch.object(main, "preparar_esquema_reportes", MagicMock()),
            "persistencia": patch.object(main, "iniciar_consumidor_persistencia", AsyncMock(return_value=MagicMock(close=AsyncMock()))),
            "reportes": patch.object(
                main, "iniciar_consumidor_reportes",
                AsyncMock(return_value=(MagicMock(close=AsyncMock()), MagicMock())),
            ),
            "barrido": patch.object(main.chatbot_estados, "barrer_al_arrancar", MagicMock(return_value=0)),
            "sesion_vector": patch.object(main, "SessionLocalVector", MagicMock()),
            "precalentamiento": patch.object(main, "precalentar_tras_arranque", AsyncMock()),
        }
        self.mocks = {}

    def __enter__(self):
        for nombre, parche in self.parches.items():
            self.mocks[nombre] = parche.start()
        return self

    def __exit__(self, *args):
        for parche in self.parches.values():
            parche.stop()
        return False

    def llamados(self):
        return sorted(n for n, m in self.mocks.items() if n != "aioredis" and getattr(m, "called", False))


def arrancar(desactivar: bool):
    """Corre el startup (y despues el shutdown) con el interruptor en `desactivar`."""
    salida = io.StringIO()
    with Dobles() as dobles, patch.object(main.settings, "desactivar_tareas_de_fondo", desactivar):
        async def ciclo():
            await main.iniciar_pipeline_datos()
            await asyncio.sleep(0)  # deja correr la tarea de precalentamiento, si se creo
            await main.cerrar_conexiones()

        with contextlib.redirect_stdout(salida):
            asyncio.run(ciclo())
        return dobles.llamados(), salida.getvalue(), main.app.state
    return None


class TestInterruptor(unittest.TestCase):
    def setUp(self):
        for nombre in ("conexion_persistencia_chatbot", "conexion_reportes", "canal_reportes", "tarea_precalentamiento"):
            if hasattr(main.app.state, nombre):
                delattr(main.app.state, nombre)

    def test_por_defecto_esta_apagado(self):
        self.assertIs(Settings.model_fields["desactivar_tareas_de_fondo"].default, False)

    def test_la_variable_de_entorno_se_lee(self):
        for valor, esperado in (("1", True), ("true", True), ("0", False)):
            with self.subTest(valor=valor):
                with patch.dict(os.environ, {"DESACTIVAR_TAREAS_DE_FONDO": valor}):
                    self.assertIs(Settings().desactivar_tareas_de_fondo, esperado)
        with patch.dict(os.environ):
            os.environ.pop("DESACTIVAR_TAREAS_DE_FONDO", None)
            self.assertIs(Settings(_env_file=None).desactivar_tareas_de_fondo, False)

    def test_con_la_variable_no_arranca_nada_y_avisa(self):
        llamados, salida, estado = arrancar(True)
        self.assertEqual(llamados, [])  # ni scheduler, ni esquema, ni consumers, ni barrido, ni precalentamiento
        self.assertIn(FRASE, salida)
        for nombre in ("conexion_persistencia_chatbot", "conexion_reportes", "canal_reportes", "tarea_precalentamiento"):
            self.assertFalse(hasattr(estado, nombre), nombre)

    def test_con_la_variable_el_shutdown_no_falla(self):
        arrancar(True)  # arrancar() ya ejecuta cerrar_conexiones(); si lanzara, esta prueba fallaria

    def test_sin_la_variable_todo_arranca_como_siempre(self):
        llamados, salida, estado = arrancar(False)
        self.assertEqual(
            llamados,
            sorted(["scheduler", "esquema", "persistencia", "reportes", "barrido", "sesion_vector", "precalentamiento"]),
        )
        self.assertNotIn(FRASE, salida)
        self.assertTrue(hasattr(estado, "conexion_persistencia_chatbot"))
        self.assertTrue(hasattr(estado, "canal_reportes"))
        self.assertTrue(hasattr(estado, "tarea_precalentamiento"))


if __name__ == "__main__":
    unittest.main()
