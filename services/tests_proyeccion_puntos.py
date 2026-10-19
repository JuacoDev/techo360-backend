"""Pruebas de la proyeccion bajo demanda de puntos en el backend (3.1/3.2): cache Redis, lote, cliente
RPC y score con componentes completados. Sin Redis, RabbitMQ ni base (dobles).

Cubre los casos 9, 10 y 11 de claude/fase3-4-umbrales-y-metodos.md seccion 5 (los demas, de
metodo, estan en techo360-proyeccion/tests/test_puntos.py), el caso 12 para el score y la
compatibilidad del RPC.

Uso:
    python -m unittest services.tests_proyeccion_puntos
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from services import proyeccion_service as servicio
from services.origen import es_proyeccion
from services.proyeccion_cache import TTL_SEGUNDOS, ProyeccionCache, VERSION_PUNTO
from services.score_privacion import VARIABLE_ACUEDUCTO, VARIABLE_ALCANTARILLADO, VARIABLE_DEFICIT_COMPUESTO


def correr(coro):
    return asyncio.run(coro)


def punto(indicador="cobertura_acueducto", codigo="68001", anio=2012, valor=60.0, estado="calculado"):
    if estado == "calculado":
        return {"indicador": indicador, "codigo": codigo, "anio": anio, "estado": "calculado", "valor": valor,
                "origen": "proyeccion-techo360-interpolacion", "fiabilidad": 30.0, "es_real": False,
                "metodo": "interpolacion-lineal", "motivo": None}
    return {"indicador": indicador, "codigo": codigo, "anio": anio, "estado": "sin_dato", "valor": None,
            "origen": None, "fiabilidad": None, "es_real": False, "metodo": None, "motivo": "sin_historia"}


class CacheFalso:
    def __init__(self):
        self.datos = {}
        self.lecturas = 0

    async def obtener_punto(self, indicador, codigo, anio):
        self.lecturas += 1
        return self.datos.get((indicador, codigo, anio))

    async def guardar_punto(self, indicador, codigo, anio, p):
        self.datos[(indicador, codigo, anio)] = p


def pedido(indicador="cobertura_acueducto", codigo="68001", anio=2012):
    return {"indicador": indicador, "codigo": codigo, "anio": anio}


class ClaveYTtl(unittest.TestCase):
    def test_clave_y_ttl_de_36h(self):
        self.assertEqual(
            ProyeccionCache._clave_punto("cobertura_acueducto", "68001", 2012),
            f"proyeccion:{VERSION_PUNTO}:punto:cobertura_acueducto:68001:2012",
        )
        self.assertEqual(ProyeccionCache._clave_punto("cobertura_acueducto", "68001", 2012), "proyeccion:v3:punto:cobertura_acueducto:68001:2012")
        self.assertEqual(TTL_SEGUNDOS, 36 * 3600)

    def test_guardar_usa_el_ttl_vigente(self):
        cache = ProyeccionCache.__new__(ProyeccionCache)
        cache._redis = AsyncMock()
        correr(cache.guardar_punto("cobertura_acueducto", "68001", 2012, punto()))
        args, kwargs = cache._redis.set.call_args
        self.assertEqual(args[0], "proyeccion:v3:punto:cobertura_acueducto:68001:2012")
        self.assertEqual(kwargs["ex"], 36 * 3600)


class Cache(unittest.TestCase):
    def test_10_segunda_llamada_sale_del_cache_sin_recalcular(self):
        cache = CacheFalso()
        rpc = AsyncMock(return_value={"status": "ok", "puntos": [punto()]})
        with patch.object(servicio, "proyeccion_cache", cache), patch.object(servicio, "solicitar_proyeccion", rpc):
            primera = correr(servicio.completar_puntos([pedido()]))
            segunda = correr(servicio.completar_puntos([pedido()]))
        self.assertEqual(primera, segunda)
        self.assertEqual(rpc.await_count, 1)

    def test_el_cache_guarda_tambien_los_sin_dato(self):
        cache = CacheFalso()
        rpc = AsyncMock(return_value={"status": "ok", "puntos": [punto(estado="sin_dato")]})
        with patch.object(servicio, "proyeccion_cache", cache), patch.object(servicio, "solicitar_proyeccion", rpc):
            correr(servicio.completar_puntos([pedido()]))
            r = correr(servicio.completar_puntos([pedido()]))
        self.assertEqual(r[0]["estado"], "sin_dato")
        self.assertEqual(rpc.await_count, 1)

    def test_mezcla_de_cacheados_y_nuevos_conserva_el_orden(self):
        cache = CacheFalso()
        cache.datos[("cobertura_acueducto", "68001", 2011)] = punto(anio=2011, valor=55.0)
        rpc = AsyncMock(return_value={"status": "ok", "puntos": [punto(anio=2012), punto(anio=2013, valor=65.0)]})
        with patch.object(servicio, "proyeccion_cache", cache), patch.object(servicio, "solicitar_proyeccion", rpc):
            r = correr(servicio.completar_puntos([pedido(anio=2012), pedido(anio=2011), pedido(anio=2013)]))
        self.assertEqual([x["anio"] for x in r], [2012, 2011, 2013])
        enviados = rpc.await_args.args[0]["puntos"]
        self.assertEqual([p["anio"] for p in enviados], [2012, 2013])  # solo lo que no estaba en cache

    def test_redis_caido_calcula_sin_cache(self):
        class Roto:
            async def obtener_punto(self, *a):
                raise ConnectionError("Redis caido (simulado)")

            async def guardar_punto(self, *a):
                raise ConnectionError("Redis caido (simulado)")

        rpc = AsyncMock(return_value={"status": "ok", "puntos": [punto()]})
        with patch.object(servicio, "proyeccion_cache", Roto()), patch.object(servicio, "solicitar_proyeccion", rpc):
            self.assertEqual(correr(servicio.completar_puntos([pedido()]))[0]["valor"], 60.0)


class LoteYRpc(unittest.TestCase):
    def test_11_lote_de_41_es_error_de_validacion(self):
        rpc = AsyncMock()
        with patch.object(servicio, "proyeccion_cache", CacheFalso()), patch.object(servicio, "solicitar_proyeccion", rpc):
            with self.assertRaises(ValueError):
                correr(servicio.completar_puntos([pedido(anio=2008 + (i % 30)) for i in range(41)]))
        rpc.assert_not_awaited()

    def test_lote_de_40_pasa_y_se_envia_en_una_solicitud(self):
        pedidos = [pedido(codigo=f"{68000 + i}") for i in range(40)]
        rpc = AsyncMock(return_value={"status": "ok", "puntos": [punto(codigo=p["codigo"]) for p in pedidos]})
        with patch.object(servicio, "proyeccion_cache", CacheFalso()), patch.object(servicio, "solicitar_proyeccion", rpc):
            self.assertEqual(len(correr(servicio.completar_puntos(pedidos))), 40)
        self.assertEqual(rpc.await_count, 1)
        self.assertEqual(rpc.await_args.args[0]["tipo"], "puntos")

    def test_lote_vacio_indicador_y_tipos_invalidos(self):
        for malo in ([], None, [pedido(indicador="tenencia_informal_terrenos")], [pedido(anio="2012")], [pedido(codigo="")]):
            with self.subTest(repr(malo)):
                with patch.object(servicio, "proyeccion_cache", CacheFalso()), patch.object(servicio, "solicitar_proyeccion", AsyncMock()):
                    with self.assertRaises(ValueError):
                        correr(servicio.completar_puntos(malo))

    def test_error_del_servicio_se_traduce_como_en_zonas(self):
        casos = {"modelo_no_disponible": servicio.ModeloNoDisponibleError, "rango_invalido": servicio.RangoProyeccionInvalidoError}
        for tipo, excepcion in casos.items():
            with self.subTest(tipo):
                rpc = AsyncMock(return_value={"status": "error", "tipo": tipo, "detail": "x"})
                with patch.object(servicio, "proyeccion_cache", CacheFalso()), patch.object(servicio, "solicitar_proyeccion", rpc):
                    with self.assertRaises(excepcion):
                        correr(servicio.completar_puntos([pedido()]))

    def test_respuesta_con_otro_numero_de_puntos_es_error(self):
        rpc = AsyncMock(return_value={"status": "ok", "puntos": []})
        with patch.object(servicio, "proyeccion_cache", CacheFalso()), patch.object(servicio, "solicitar_proyeccion", rpc):
            with self.assertRaises(servicio.ModeloNoDisponibleError):
                correr(servicio.completar_puntos([pedido()]))

    def test_compatibilidad_calcular_zonas_sigue_enviando_tipo_zonas(self):
        rpc = AsyncMock(return_value={"status": "ok", "zonas": [
            {"codigo_municipio": "68001", "valor": 1.0, "es_real": True, "origen": "TerriData", "fiabilidad": 100.0}]})
        with patch.object(servicio, "solicitar_proyeccion", rpc):
            zonas = correr(servicio.calcular_zonas("densidad_poblacional", 2026))
        rpc.assert_awaited_once_with({"tipo": "zonas", "indicador": "densidad_poblacional", "anio": 2026})
        self.assertEqual(zonas[0].codigo_municipio, "68001")


LIMITES = {VARIABLE_ACUEDUCTO: (0.0, 100.0), VARIABLE_ALCANTARILLADO: (0.0, 100.0), VARIABLE_DEFICIT_COMPUESTO: (0.0, 100.0)}


def fila(valor, origen="TerriData", intervalo=None):
    return {"valuevar": valor, "originvar": origen, "intervaloconfianza": intervalo}


class Score(unittest.TestCase):
    def _score(self, filas, completados):
        async def leer(variable):
            return filas.get(variable)

        async def completar(pedidos):
            return [completados[p["indicador"]] for p in pedidos]

        return correr(servicio.calcular_score_punto("68001", 2021, None, leer_fila=leer, completar=completar, limites=LIMITES))

    def test_score_con_componentes_completados(self):
        # acueducto 80 (real), alcantarillado 60 y deficit 40 completados:
        # (1-0.8)/3 + (1-0.6)/3 + 0.4/3 = 0.3333
        r = self._score(
            {VARIABLE_ACUEDUCTO: fila(80.0)},
            {VARIABLE_ALCANTARILLADO: punto(VARIABLE_ALCANTARILLADO, valor=60.0),
             VARIABLE_DEFICIT_COMPUESTO: punto(VARIABLE_DEFICIT_COMPUESTO, valor=40.0)},
        )
        self.assertEqual((r["estado"], r["valor"]), ("calculado", 0.3333))
        self.assertEqual(r["origen"], "proyeccion-techo360-score")
        self.assertTrue(es_proyeccion(r["origen"]))
        self.assertIs(r["es_real"], False)
        self.assertEqual(r["fiabilidad"], 30.0)  # minimo de los componentes: 100 (real), 30, 30
        self.assertEqual([c["variable"] for c in r["componentes"]],
                         [VARIABLE_ACUEDUCTO, VARIABLE_ALCANTARILLADO, VARIABLE_DEFICIT_COMPUESTO])

    def test_9_un_componente_sin_dato_deja_el_score_sin_dato(self):
        r = self._score(
            {VARIABLE_ACUEDUCTO: fila(80.0), VARIABLE_ALCANTARILLADO: fila(70.0)},
            {VARIABLE_DEFICIT_COMPUESTO: punto(VARIABLE_DEFICIT_COMPUESTO, estado="sin_dato")},
        )
        self.assertEqual((r["estado"], r["motivo"], r["valor"]), ("sin_dato", "componente_sin_dato", None))

    def test_sin_hueco_no_se_recalcula(self):
        filas = {v: fila(50.0) for v in (VARIABLE_ACUEDUCTO, VARIABLE_ALCANTARILLADO, VARIABLE_DEFICIT_COMPUESTO)}
        r = self._score(filas, {})
        self.assertEqual((r["estado"], r["motivo"]), ("sin_dato", "sin_hueco"))

    def test_solo_se_completan_los_componentes_que_faltan(self):
        pedidos = []

        async def leer(variable):
            return fila(50.0) if variable != VARIABLE_DEFICIT_COMPUESTO else None

        async def completar(p):
            pedidos.extend(p)
            return [punto(VARIABLE_DEFICIT_COMPUESTO, valor=20.0)]

        correr(servicio.calcular_score_punto("68001", 2021, None, leer_fila=leer, completar=completar, limites=LIMITES))
        self.assertEqual([x["indicador"] for x in pedidos], [VARIABLE_DEFICIT_COMPUESTO])


if __name__ == "__main__":
    unittest.main()
