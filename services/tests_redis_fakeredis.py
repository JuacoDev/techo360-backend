"""Adaptador de fakeredis para repetir las pruebas de zonas_cache y
precalentamiento contra una implementacion REAL del protocolo de Redis
(en memoria), en vez del doble casero de tests_zonas_cache.py.

NO es una dependencia de produccion: fakeredis NO esta en requirements.txt y
este modulo solo se importa cuando se pide expresamente:

    python -m venv --system-site-packages .venv-pruebas
    .venv-pruebas/Scripts/pip install fakeredis          (o bin/pip en Linux)
    TECHO360_TEST_REDIS=fakeredis .venv-pruebas/Scripts/python -m services.tests_zonas_cache
    TECHO360_TEST_REDIS=fakeredis .venv-pruebas/Scripts/python -m services.tests_precalentamiento

Expone la misma interfaz que usan las pruebas (datos, vencimientos, escrituras,
caido) sobre un fakeredis.FakeRedis, de modo que el codigo bajo prueba habla
con un cliente redis-py de verdad (get/mget/set nx ex/pipeline MULTI/scan_iter/
delete).
"""
import fakeredis


class _Datos:
    """Vista tipo dict de las claves del servidor (str -> bytes)."""

    def __init__(self, cliente):
        self._r = cliente

    def _claves(self):
        return sorted(k.decode() for k in self._r.keys("*"))

    def __setitem__(self, clave, valor):
        self._r.set(clave, valor)

    def __getitem__(self, clave):
        valor = self._r.get(clave)
        if valor is None:
            raise KeyError(clave)
        return valor

    def __contains__(self, clave):
        return bool(self._r.exists(clave))

    def __iter__(self):
        return iter(self._claves())

    def __len__(self):
        return len(self._claves())

    def items(self):
        return [(k, self._r.get(k)) for k in self._claves()]

    def __eq__(self, otro):
        return dict(self.items()) == otro

    def __repr__(self):
        return repr(dict(self.items()))


class _Vencimientos:
    def __init__(self, cliente):
        self._r = cliente

    def __getitem__(self, clave):
        ms = self._r.pttl(clave)
        if ms is None or ms < 0:
            return None
        return round(ms / 1000)


class RedisFalso(fakeredis.FakeRedis):
    def __init__(self):
        self._servidor = fakeredis.FakeServer()
        super().__init__(server=self._servidor, decode_responses=False)
        self.datos = _Datos(self)
        self.vencimientos = _Vencimientos(self)

    @property
    def caido(self):
        return not self._servidor.connected

    @caido.setter
    def caido(self, valor):
        self._servidor.connected = not valor

    @property
    def escrituras(self):
        return self.dbsize()
