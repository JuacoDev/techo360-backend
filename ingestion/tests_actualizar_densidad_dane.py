"""Pruebas de las funciones puras de ingestion/actualizar_densidad_dane.py tras INC-001 (sin base de datos,
sin red, sin ejecutar el script).

    python -m unittest ingestion.tests_actualizar_densidad_dane -v
"""
import unittest

from ingestion import actualizar_densidad_dane as m
from services.origen import es_proyeccion, es_real


class TestOrigenIncidente001(unittest.TestCase):
    def test_origen_unico_para_2024_2042(self):
        for anio in range(2024, 2043):
            self.assertEqual(m.origen_para(anio), "proyeccion-poblacion-sobre-area", anio)

    def test_el_origen_que_escribe_es_proyeccion_y_no_real(self):
        for anio in (2024, 2026, 2027, 2042):
            self.assertTrue(es_proyeccion(m.origen_para(anio)), anio)
            self.assertFalse(es_real(m.origen_para(anio)), anio)

    def test_ya_no_usa_el_nombre_antiguo(self):
        self.assertNotIn("DANE", m.origen_para(2030))

    def test_records_con_origen_nuevo_e_intervalo_30(self):
        records = m.construir_records(2030, {"05001": ("Medellín", 6758.56), "68001": ("Bucaramanga", 4036.03)})
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertEqual(r.fuente, "proyeccion-poblacion-sobre-area")
            self.assertEqual(r.intervalo_confianza, 30.0)
            self.assertEqual(r.variable, "densidad_poblacional")
            self.assertEqual(r.anio, 2030)
        self.assertEqual({r.codigo_divipola: r.valor for r in records}, {"05001": 6758.56, "68001": 4036.03})

    def test_no_toca_el_origen_huerfano(self):
        self.assertEqual(m.ORIGEN_HUERFANO, "TerriData-proyeccion")


if __name__ == "__main__":
    unittest.main()
