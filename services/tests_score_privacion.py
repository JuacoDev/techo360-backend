"""Verificacion de services/score_privacion.py y de
calcular_zonas_score_privacion() (services/proyeccion_service.py) -- el
proyecto no tiene pytest en requirements.txt, asi que esto usa unittest
(stdlib), igual que ingestion/tests_terridata_adapter.py.

No usa base de datos ni Redis reales: calcular_limites_base/
obtener_deficit_base/calcular_zonas/proyeccion_cache se mockean.

Uso:
    python -m services.tests_score_privacion
"""
import unittest
from unittest.mock import AsyncMock, patch

from schemas.proyecciones import PuntoZona
from services.proyeccion_rpc_client import ProyeccionTimeoutError
from services.score_privacion import calcular_score_privacion, normalizar_minmax
from services import proyeccion_service as servicio


class TestNormalizarMinmax(unittest.TestCase):
    def test_caso_normal(self):
        self.assertEqual(normalizar_minmax(75, 50, 100), 0.5)

    def test_valor_none(self):
        self.assertIsNone(normalizar_minmax(None, 50, 100))

    def test_minimo_o_maximo_none(self):
        self.assertIsNone(normalizar_minmax(75, None, 100))
        self.assertIsNone(normalizar_minmax(75, 50, None))

    def test_rango_degenerado(self):
        # Todas las filas con el mismo valor -- no hay forma de
        # normalizar sin inventar un 0 o un 1 arbitrario.
        self.assertIsNone(normalizar_minmax(75, 75, 75))


class TestCalcularScorePrivacion(unittest.TestCase):
    """Estos casos replican EXACTAMENTE lo que el formula inline de
    routers/zonas.py (antes de extraerse) calculaba -- sirven como
    prueba de regresion de que mover el codigo a services/score_privacion.py
    no le cambio el comportamiento."""

    def _formula_original(self, acueducto, alcantarillado, deficit):
        # Copia literal de la expresion que tenia _score_privacion()
        # antes de la extraccion (ver commit de refactor), para comparar
        # contra calcular_score_privacion() y confirmar que son lo mismo.
        return round((1 - acueducto) / 3 + (1 - alcantarillado) / 3 + deficit / 3, 4)

    def test_los_tres_insumos_presentes(self):
        indicadores = {
            "cobertura_acueducto": {"score_normalizado": 0.9},
            "cobertura_alcantarillado": {"score_normalizado": 0.8},
            "deficit_habitacional_cuantitativo": {"score_normalizado": 0.2},
        }
        esperado = self._formula_original(0.9, 0.8, 0.2)
        self.assertEqual(calcular_score_privacion(indicadores), esperado)

    def test_falta_acueducto_da_null(self):
        indicadores = {
            "cobertura_acueducto": {"score_normalizado": None},
            "cobertura_alcantarillado": {"score_normalizado": 0.8},
            "deficit_habitacional_cuantitativo": {"score_normalizado": 0.2},
        }
        self.assertIsNone(calcular_score_privacion(indicadores))

    def test_falta_alcantarillado_da_null(self):
        indicadores = {
            "cobertura_acueducto": {"score_normalizado": 0.9},
            "cobertura_alcantarillado": {"score_normalizado": None},
            "deficit_habitacional_cuantitativo": {"score_normalizado": 0.2},
        }
        self.assertIsNone(calcular_score_privacion(indicadores))

    def test_falta_deficit_da_null(self):
        indicadores = {
            "cobertura_acueducto": {"score_normalizado": 0.9},
            "cobertura_alcantarillado": {"score_normalizado": 0.8},
            "deficit_habitacional_cuantitativo": {"score_normalizado": None},
        }
        self.assertIsNone(calcular_score_privacion(indicadores))

    def test_dict_vacio_da_null(self):
        # Caso MpCodigo=00000 (Area en Litigio): no tiene fila en
        # mapcalordb para ninguna variable.
        self.assertIsNone(calcular_score_privacion({}))

    def test_valores_en_los_limites(self):
        # score_normalizado = 0 y 1 son validos (bordes de la
        # normalizacion min-max), no deben tratarse como "falta el dato".
        indicadores = {
            "cobertura_acueducto": {"score_normalizado": 1.0},  # mejor caso posible
            "cobertura_alcantarillado": {"score_normalizado": 0.0},  # peor caso posible
            "deficit_habitacional_cuantitativo": {"score_normalizado": 1.0},  # peor caso posible
        }
        esperado = self._formula_original(1.0, 0.0, 1.0)
        resultado = calcular_score_privacion(indicadores)
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado, esperado)


class TestCalcularZonasScorePrivacion(unittest.IsolatedAsyncioTestCase):
    """calcular_zonas_score_privacion() con calcular_zonas (RPC),
    calcular_limites_base/obtener_deficit_base (BD) y proyeccion_cache
    (Redis) mockeados -- nada de esto toca un servicio real."""

    def setUp(self):
        self.limites_base = {
            "cobertura_acueducto": (80.0, 100.0),
            "cobertura_alcantarillado": (70.0, 90.0),
            "deficit_habitacional_cuantitativo": (2.0, 8.0),
        }
        self.deficit_base = {"05001": 5.0, "05002": 5.0}

    async def test_municipio_con_los_tres_insumos(self):
        with patch.object(servicio, "calcular_limites_base", return_value=self.limites_base), \
             patch.object(servicio, "obtener_deficit_base", return_value=self.deficit_base), \
             patch.object(
                 servicio.proyeccion_cache, "obtener_limites_base", new=AsyncMock(return_value=None)
             ), \
             patch.object(
                 servicio.proyeccion_cache, "guardar_limites_base", new=AsyncMock()
             ), \
             patch.object(
                 servicio,
                 "calcular_zonas",
                 new=AsyncMock(
                     side_effect=[
                         [PuntoZona(codigo_municipio="05001", valor=90.0, es_real=False)],
                         [PuntoZona(codigo_municipio="05001", valor=80.0, es_real=False)],
                     ]
                 ),
             ):
            zonas = await servicio.calcular_zonas_score_privacion(2030, db=None)

        self.assertEqual(len(zonas), 1)
        zona = zonas[0]
        self.assertEqual(zona.codigo_municipio, "05001")
        self.assertIsNotNone(zona.valor)
        # acueducto=90 -> normalizado 0.5; alcantarillado=80 -> 0.5;
        # deficit=5 -> 0.5 -- score = (1-0.5)/3*2 + 0.5/3 = 0.5
        self.assertAlmostEqual(zona.valor, 0.5, places=4)
        self.assertFalse(zona.es_real)

    async def test_municipio_falta_en_uno_de_los_dos_da_valor_null(self):
        with patch.object(servicio, "calcular_limites_base", return_value=self.limites_base), \
             patch.object(servicio, "obtener_deficit_base", return_value=self.deficit_base), \
             patch.object(
                 servicio.proyeccion_cache, "obtener_limites_base", new=AsyncMock(return_value=self.limites_base)
             ), \
             patch.object(
                 servicio,
                 "calcular_zonas",
                 new=AsyncMock(
                     side_effect=[
                         [PuntoZona(codigo_municipio="05001", valor=90.0, es_real=False)],
                         [],  # alcantarillado: 05001 no aparece (sin dato real/regresable)
                     ]
                 ),
             ):
            zonas = await servicio.calcular_zonas_score_privacion(2030, db=None)

        self.assertEqual(len(zonas), 1)
        self.assertEqual(zonas[0].codigo_municipio, "05001")
        self.assertIsNone(zonas[0].valor)

    async def test_falla_una_rpc_no_devuelve_score_a_medias(self):
        with patch.object(servicio, "calcular_limites_base", return_value=self.limites_base), \
             patch.object(servicio, "obtener_deficit_base", return_value=self.deficit_base), \
             patch.object(
                 servicio.proyeccion_cache, "obtener_limites_base", new=AsyncMock(return_value=self.limites_base)
             ), \
             patch.object(
                 servicio,
                 "calcular_zonas",
                 new=AsyncMock(
                     side_effect=[
                         [PuntoZona(codigo_municipio="05001", valor=90.0, es_real=False)],
                         ProyeccionTimeoutError("timeout simulado"),
                     ]
                 ),
             ):
            with self.assertRaises(ProyeccionTimeoutError):
                await servicio.calcular_zonas_score_privacion(2030, db=None)

    async def test_score_proyectado_anio_base_coincide_con_score_real(self):
        """El caso pedido explicitamente: si el año proyectado pedido ES
        el año base (2024) y los valores "proyectados" de acueducto/
        alcantarillado coinciden con los reales de ese año (como pasaria
        en la practica: calcular_zonas() en techo360-proyeccion devuelve
        el dato real cuando existe, no una regresion), el score
        proyectado debe ser IDENTICO al que calcularia routers/zonas.py
        con el mismo año como "real" -- misma formula, mismos limites
        (el año pedido Y el año base son el mismo, 2024)."""
        acueducto_real = 90.0
        alcantarillado_real = 80.0
        deficit_real = 5.0

        with patch.object(servicio, "calcular_limites_base", return_value=self.limites_base), \
             patch.object(
                 servicio, "obtener_deficit_base", return_value={"05001": deficit_real}
             ), \
             patch.object(
                 servicio.proyeccion_cache, "obtener_limites_base", new=AsyncMock(return_value=self.limites_base)
             ), \
             patch.object(
                 servicio,
                 "calcular_zonas",
                 new=AsyncMock(
                     side_effect=[
                         [PuntoZona(codigo_municipio="05001", valor=acueducto_real, es_real=True)],
                         [PuntoZona(codigo_municipio="05001", valor=alcantarillado_real, es_real=True)],
                     ]
                 ),
             ):
            zonas = await servicio.calcular_zonas_score_privacion(servicio.ANIO_BASE_SCORE, db=None)

        # Score "real" equivalente, calculado con la MISMA funcion
        # compartida y los MISMOS limites (porque el año pedido es el
        # año base) -- asi se prueba que el camino proyectado no
        # introduce ninguna transformacion extra.
        indicadores_zona_real = {
            "cobertura_acueducto": {
                "score_normalizado": normalizar_minmax(acueducto_real, *self.limites_base["cobertura_acueducto"])
            },
            "cobertura_alcantarillado": {
                "score_normalizado": normalizar_minmax(
                    alcantarillado_real, *self.limites_base["cobertura_alcantarillado"]
                )
            },
            "deficit_habitacional_cuantitativo": {
                "score_normalizado": normalizar_minmax(
                    deficit_real, *self.limites_base["deficit_habitacional_cuantitativo"]
                )
            },
        }
        score_real_equivalente = calcular_score_privacion(indicadores_zona_real)

        self.assertEqual(len(zonas), 1)
        self.assertEqual(zonas[0].valor, score_real_equivalente)
        self.assertTrue(zonas[0].es_real)


if __name__ == "__main__":
    unittest.main()
