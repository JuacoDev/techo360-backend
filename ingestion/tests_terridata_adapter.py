"""
Verificacion rapida y sin red de _es_agregado_departamental() en
terridata_adapter.py -- el proyecto no tiene pytest en requirements.txt
ni una carpeta tests/ existente, asi que esto usa unittest (stdlib, sin
dependencias nuevas), siguiendo el mismo espiritu de verificacion manual
que ya usan zonas_igac_adapter.py / db.py (bloques `if __name__ ==
"__main__"`).

Casos cubiertos, tomados directo del diagnostico confirmado en vivo
contra la fuente real (ver el aviso en _es_agregado_departamental):
- Codigos que SI son agregado departamental (terminan en '000').
- Bogota D.C. ('11001'), que NO debe filtrarse aunque el nombre de su
  entidad coincida con el de un departamento.
- Municipios reales cuyo codigo NO termina en '000' (la mayoria).
- Un caso borde real: codigos que terminan en '00' pero no en '000' (ej.
  un municipio cuyo DIVIPOLA numerico termina justo ahi) no deben
  filtrarse -- el patron es '000' completo, no solo un cero final.

Uso:
    python -m ingestion.tests_terridata_adapter
"""
import unittest

from ingestion.terridata_adapter import _es_agregado_departamental


class TestEsAgregadoDepartamental(unittest.TestCase):
    def test_codigos_departamentales_reales_se_detectan(self):
        # Los 29 confirmados en vivo (ver reporte de diagnostico) -- una
        # muestra representativa, no la lista completa de 33.
        for codigo in ["05000", "15000", "50000", "91000", "99000", "68000"]:
            with self.subTest(codigo=codigo):
                self.assertTrue(_es_agregado_departamental(codigo))

    def test_bogota_no_se_filtra(self):
        self.assertFalse(_es_agregado_departamental("11001"))

    def test_municipios_reales_no_se_filtran(self):
        for codigo in ["68001", "05001", "76001", "54001", "13001", "76111", "15407"]:
            with self.subTest(codigo=codigo):
                self.assertFalse(_es_agregado_departamental(codigo))

    def test_codigo_con_ceros_pero_no_terminado_en_000(self):
        # Caso borde: un codigo real puede terminar en '00' sin ser un
        # agregado departamental (el patron exacto es '000', no "termina
        # en cero").
        self.assertFalse(_es_agregado_departamental("05200"))

    def test_codigo_vacio_o_con_espacios(self):
        self.assertFalse(_es_agregado_departamental(""))
        self.assertFalse(_es_agregado_departamental("   "))
        self.assertFalse(_es_agregado_departamental(None))

    def test_codigo_con_longitud_distinta_a_5_no_se_filtra(self):
        # Defensivo: si algun dia la fuente cambia el formato del codigo,
        # mejor no filtrar de mas (falso negativo) que filtrar de mas
        # (falso positivo que borraria un municipio real).
        self.assertFalse(_es_agregado_departamental("000"))
        self.assertFalse(_es_agregado_departamental("0500000"))


if __name__ == "__main__":
    unittest.main()
