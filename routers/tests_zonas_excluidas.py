"""Verificacion de que GET /api/zonas excluye el "Area En Litigio Cauca -
Huila" (municipio '00000', departamento '00') -- unittest (stdlib), igual
que services/tests_score_privacion.py. No usa base de datos real: db es
un doble que registra las consultas que recibe.

Uso:
    python -m routers.tests_zonas_excluidas
"""
import unittest

from routers import zonas


class _Resultado:
    def mappings(self):
        return self

    def all(self):
        return []


class _DbFalso:
    def __init__(self):
        self.consultas = []

    def execute(self, query, parametros=None):
        self.consultas.append((str(query), parametros or {}))
        return _Resultado()


def _llamar(nivel):
    db = _DbFalso()
    respuesta = zonas._construir_zonas(nivel, 2024, db)
    return respuesta, db.consultas


class TestExclusionAreaLitigio(unittest.TestCase):
    def test_constantes(self):
        self.assertEqual(zonas.CODIGOS_MUNICIPIO_EXCLUIDOS, ["00000"])
        self.assertEqual(zonas.CODIGOS_DEPARTAMENTO_EXCLUIDOS, ["00"])

    def test_municipio_filtra_00000_en_la_consulta_de_zonas(self):
        respuesta, consultas = _llamar("municipio")
        sql, parametros = consultas[0]
        self.assertIn("FROM zonas_municipio", sql)
        self.assertIn("codigo_municipio != ALL(:excluidos)", sql)
        self.assertEqual(parametros["excluidos"], ["00000"])
        self.assertEqual(respuesta["features"], [])

    def test_departamento_filtra_00_en_la_consulta_de_zonas(self):
        _, consultas = _llamar("departamento")
        sql, parametros = consultas[0]
        self.assertIn("FROM zonas_departamento", sql)
        self.assertIn("codigo_departamento != ALL(:excluidos)", sql)
        self.assertEqual(parametros["excluidos"], ["00"])

    def test_departamento_excluye_00000_tambien_en_el_join_de_indicadores(self):
        _, consultas = _llamar("departamento")
        joins = [c for c in consultas if "JOIN zonas_municipio" in c[0]]
        self.assertEqual(len(joins), 1)
        self.assertIn("zm.codigo_municipio != ALL(:municipios_excluidos)", joins[0][0])
        self.assertEqual(joins[0][1]["municipios_excluidos"], ["00000"])

    def test_no_se_ejecuta_ningun_delete_ni_ddl(self):
        for nivel in ("municipio", "departamento"):
            _, consultas = _llamar(nivel)
            for sql, _ in consultas:
                self.assertNotRegex(sql.upper(), r"\b(DELETE|DROP|ALTER|UPDATE|INSERT|TRUNCATE)\b")


if __name__ == "__main__":
    unittest.main()
