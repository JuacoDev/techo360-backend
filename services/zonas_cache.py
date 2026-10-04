"""Cache en Redis de GET /api/zonas (D6).

Sin cache, cada peticion recalculaba todo: ST_AsGeoJSON de 1.104 poligonos,
min-max, score, validacion Pydantic y serializacion (de ~0,5 s a ~1,6 s de
servidor, segun el nivel) para devolver SIEMPRE el mismo cuerpo. Aqui se
guarda el cuerpo ya serializado y comprimido con gzip, junto a su ETag.

Diseño (ver informe de la FASE B, B4):
  - Claves versionadas: zonas:v1:{nivel}:{anio} (cuerpo gzip) y
    zonas:v1:{nivel}:{anio}:etag (hash del JSON sin comprimir). Si cambia la
    forma de la respuesta o la consulta se sube a v2 y las viejas expiran solas.
  - Solo se cachean los años que el frontend usa (ANIOS_CACHEABLES): hoy solo
    2024 (ANIO_INDICADORES; /api/zonas?nivel=departamento sin anio tambien
    resuelve a 2024). Cualquier otro año se sigue calculando en cada
    peticion, como antes, SIN guardar nada: asi un usuario no puede llenar la
    memoria de Redis (128 MB, sin maxmemory) pidiendo años arbitrarios.
  - La respuesta NO depende del usuario: el endpoint solo usa al usuario para
    exigir sesion, nunca para construir el cuerpo. Por eso una sola entrada
    sirve a todos.
  - Anti-estampida: SET NX con vencimiento de 60 s; quien no consigue el
    candado espera a que el otro llene el cache (con tope) en vez de
    recalcular por duplicado (el backend tiene 0,3 CPU).
  - Si Redis falla, el endpoint sigue funcionando sin cache.
  - Cliente SINCRONO y de bytes (decode_responses=False): el endpoint es una
    funcion `def` (corre en el threadpool) y el valor es binario (gzip).
    El ProyeccionCache existente usa decode_responses=True y no sirve aqui.
"""
import gzip
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import redis

from core.config import settings

logger = logging.getLogger(__name__)

VERSION = "v1"
NIVELES = ("departamento", "municipio")
ANIOS_CACHEABLES = frozenset({2024})

# Algo mayor que el periodo de refresco (diario, 03:30) para que nunca haya
# un hueco entre que expira una entrada y se escribe la siguiente.
TTL_SEGUNDOS = 36 * 60 * 60
LOCK_SEGUNDOS = 60
ESPERA_MAXIMA_SEGUNDOS = 20
INTERVALO_ESPERA_SEGUNDOS = 0.25
NIVEL_COMPRESION = 6

# Si Redis falla (caido o inalcanzable), cada peticion pagaria el timeout de
# conexion antes de caer a calcular sin cache. Tras un fallo se deja de
# intentar durante CIRCUITO_SEGUNDOS: las peticiones van directo a BYPASS.
CIRCUITO_SEGUNDOS = 30
TIMEOUT_CONEXION_SEGUNDOS = 1
TIMEOUT_LECTURA_SEGUNDOS = 3

CACHE_CONTROL = "private, no-cache"

# Cabecera X-Cache, para saber de un vistazo (curl / pestaña Network) como se
# sirvio una respuesta:
#   HIT    -> servida desde Redis (tambien los 304, y quien esperaba el
#             candado y encontro el cache ya lleno)
#   MISS   -> calculada en esta peticion Y guardada en Redis
#   BYPASS -> sin cache: año/nivel no cacheable, o Redis caido
HIT = "HIT"
MISS = "MISS"
BYPASS = "BYPASS"


def serializar(contenido: dict) -> bytes:
    """Mismo JSON que produce la respuesta normal de FastAPI (compacto, sin
    escapar tildes), pero sin pasar por la validacion Pydantic de
    ~214.000 coordenadas, que no aporta nada: el dict lo construimos nosotros."""
    return json.dumps(
        contenido, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _etag_de(cuerpo: bytes) -> str:
    return hashlib.md5(cuerpo).hexdigest()


class ZonasCache:
    def __init__(self, cliente=None):
        self._cliente = cliente
        self._sin_redis_hasta = 0.0

    def redis_disponible(self) -> bool:
        """False mientras el circuito este abierto (hubo un fallo reciente)."""
        return time.monotonic() >= self._sin_redis_hasta

    def marcar_caido(self) -> None:
        self._sin_redis_hasta = time.monotonic() + CIRCUITO_SEGUNDOS

    @property
    def cliente(self):
        if self._cliente is None:
            self._cliente = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=False,
                socket_connect_timeout=TIMEOUT_CONEXION_SEGUNDOS,
                socket_timeout=TIMEOUT_LECTURA_SEGUNDOS,
            )
        return self._cliente

    @staticmethod
    def cacheable(nivel: str, anio: int) -> bool:
        return nivel in NIVELES and anio in ANIOS_CACHEABLES

    @staticmethod
    def _clave(nivel: str, anio: int) -> str:
        return f"zonas:{VERSION}:{nivel}:{anio}"

    @classmethod
    def _clave_etag(cls, nivel: str, anio: int) -> str:
        return f"{cls._clave(nivel, anio)}:etag"

    @staticmethod
    def _clave_lock(nivel: str, anio: int) -> str:
        return f"zonas:{VERSION}:lock:{nivel}:{anio}"

    def obtener_etag(self, nivel: str, anio: int) -> str | None:
        crudo = self.cliente.get(self._clave_etag(nivel, anio))
        return crudo.decode("ascii") if crudo is not None else None

    def obtener(self, nivel: str, anio: int) -> tuple[str, bytes] | None:
        etag, cuerpo = self.cliente.mget(self._clave_etag(nivel, anio), self._clave(nivel, anio))
        if etag is None or cuerpo is None:
            return None
        return etag.decode("ascii"), cuerpo

    def guardar(self, nivel: str, anio: int, cuerpo_json: bytes) -> tuple[str, bytes]:
        """Comprime una sola vez y guarda cuerpo y ETag juntos (MULTI/EXEC)."""
        etag = _etag_de(cuerpo_json)
        comprimido = gzip.compress(cuerpo_json, NIVEL_COMPRESION, mtime=0)
        pipeline = self.cliente.pipeline(transaction=True)
        pipeline.set(self._clave(nivel, anio), comprimido, ex=TTL_SEGUNDOS)
        pipeline.set(self._clave_etag(nivel, anio), etag, ex=TTL_SEGUNDOS)
        pipeline.execute()
        return etag, comprimido

    def adquirir_lock(self, nivel: str, anio: int) -> bool:
        return bool(self.cliente.set(self._clave_lock(nivel, anio), "1", nx=True, ex=LOCK_SEGUNDOS))

    def liberar_lock(self, nivel: str, anio: int) -> None:
        self.cliente.delete(self._clave_lock(nivel, anio))

    def invalidar(self) -> int:
        """Borra las entradas de zonas (cuerpo y ETag) con SCAN -- nunca
        KEYS, que bloquea Redis. No toca los candados ni otras claves."""
        borradas = 0
        for nivel in NIVELES:
            for clave in self.cliente.scan_iter(match=f"zonas:{VERSION}:{nivel}:*", count=100):
                borradas += int(self.cliente.delete(clave))
        return borradas


zonas_cache = ZonasCache()


@dataclass
class RespuestaZonas:
    status: int
    cuerpo: bytes = b""
    encabezados: dict = field(default_factory=dict)


def _acepta_gzip(accept_encoding: str | None) -> bool:
    return bool(accept_encoding) and re.search(r"\bgzip\b(?!\s*;\s*q=0(\.0+)?\s*(,|$))", accept_encoding.lower()) is not None


def _coincide(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    for candidato in if_none_match.split(","):
        candidato = candidato.strip()
        if candidato == "*":
            return True
        if candidato.startswith("W/"):
            candidato = candidato[2:]
        if candidato.strip('"') == etag:
            return True
    return False


def _encabezados(estado: str, etag: str | None = None) -> dict:
    encabezados = {"Cache-Control": CACHE_CONTROL, "Vary": "Accept-Encoding", "X-Cache": estado}
    if etag is not None:
        encabezados["ETag"] = f'"{etag}"'
    return encabezados


def _respuesta_desde_entrada(estado: str, etag: str, comprimido: bytes, acepta_gzip: bool) -> RespuestaZonas:
    encabezados = _encabezados(estado, etag)
    if acepta_gzip:
        encabezados["Content-Encoding"] = "gzip"
        return RespuestaZonas(200, comprimido, encabezados)
    return RespuestaZonas(200, gzip.decompress(comprimido), encabezados)


def _respuesta_sin_cache(cuerpo: bytes) -> RespuestaZonas:
    return RespuestaZonas(200, cuerpo, _encabezados(BYPASS))


def _calcular_con_candado(cache: ZonasCache, nivel: str, anio: int, construir: Callable[[], dict]):
    """Devuelve ((etag, comprimido), estado): HIT si el cache ya estaba lleno
    (lo lleno otro proceso), MISS si lo lleno esta peticion."""
    if cache.adquirir_lock(nivel, anio):
        try:
            existente = cache.obtener(nivel, anio)  # otro proceso pudo llenarlo justo antes
            if existente is not None:
                return existente, HIT
            return cache.guardar(nivel, anio, serializar(construir())), MISS
        finally:
            try:
                cache.liberar_lock(nivel, anio)
            except redis.RedisError:
                logger.warning("No se pudo liberar el candado de zonas; vence solo en %s s.", LOCK_SEGUNDOS)

    # Otro proceso esta calculando: esperar a que llene el cache.
    limite = time.monotonic() + ESPERA_MAXIMA_SEGUNDOS
    while time.monotonic() < limite:
        time.sleep(INTERVALO_ESPERA_SEGUNDOS)
        existente = cache.obtener(nivel, anio)
        if existente is not None:
            return existente, HIT
    logger.warning("zonas %s/%s: el candado no libero a tiempo; se calcula sin esperar.", nivel, anio)
    return cache.guardar(nivel, anio, serializar(construir())), MISS


def resolver(
    cache: ZonasCache,
    nivel: str,
    anio: int,
    construir: Callable[[], dict],
    if_none_match: str | None = None,
    accept_encoding: str | None = None,
) -> RespuestaZonas:
    """Logica completa de GET /api/zonas con cache, independiente de FastAPI
    para poder probarla con un Redis simulado."""
    if not cache.cacheable(nivel, anio):
        return _respuesta_sin_cache(serializar(construir()))

    if not cache.redis_disponible():  # circuito abierto: no se toca Redis
        return _respuesta_sin_cache(serializar(construir()))

    try:
        if if_none_match:
            etag_actual = cache.obtener_etag(nivel, anio)
            if etag_actual is not None and _coincide(if_none_match, etag_actual):
                return RespuestaZonas(304, b"", _encabezados(HIT, etag_actual))

        entrada = cache.obtener(nivel, anio)
        estado = HIT
        if entrada is None:
            entrada, estado = _calcular_con_candado(cache, nivel, anio, construir)
    except redis.RedisError as error:
        logger.warning("Redis no disponible para zonas %s/%s (%s); se sirve sin cache.", nivel, anio, error)
        cache.marcar_caido()
        return _respuesta_sin_cache(serializar(construir()))

    etag, comprimido = entrada
    if _coincide(if_none_match, etag):
        return RespuestaZonas(304, b"", _encabezados(estado, etag))
    return _respuesta_desde_entrada(estado, etag, comprimido, _acepta_gzip(accept_encoding))


def invalidar_cache_zonas() -> int:
    """Borra el cache de /api/zonas (SCAN). Mejor esfuerzo: nunca lanza.
    Se llama al terminar la ingesta (scheduler o carga manual) y la
    sincronizacion de zonas, que son lo unico que cambia el contenido."""
    try:
        borradas = zonas_cache.invalidar()
        logger.info("Cache de zonas invalidado (%s claves).", borradas)
        return borradas
    except Exception as error:
        logger.warning("No se pudo invalidar el cache de zonas: %s", error)
        return 0
