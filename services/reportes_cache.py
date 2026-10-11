"""Caché de 24 h de los reportes ya generados (Redis).

Qué se guarda: los BYTES del archivo (PDF/Excel) bajo la huella de la configuración del reporte
(sha256 del JSON canónico + usuario + día). Si el mismo usuario pide la misma configuración el mismo
día, se le entrega el archivo sin volver a generarlo; si venció o no cupo, se vuelve a generar desde
la configuración que SIEMPRE queda guardada en la BD (public.reportes.solicitud). Nunca es la fuente
de verdad: Redis puede vaciarse y el reporte se regenera.

Por qué lleva guardas (el Redis de producción tiene 128 MB, SIN maxmemory ni persistencia, y también
guarda OTP, topes de seguridad y la caché de zonas; un OOM ahí tumbaría el registro de usuarios):
  * tope por archivo (por defecto 3 MB): un reporte mayor no se cachea (queda en disco temporal);
  * tope de reportes cacheados por usuario (por defecto 5): el más antiguo se borra;
  * si Redis ya usa más de `reportes_cache_redis_max_usado` (por defecto 80 MB), no se escribe;
  * TTL de 24 h en cada clave y en el índice por usuario;
  * cualquier fallo de Redis se traga (log) y el flujo sigue con el disco.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date

import redis

from core.config import settings

logger = logging.getLogger(__name__)

PREFIJO = "reporte:v1:"
VERSION_HUELLA = "1"


def huella_de(solicitud: dict, usuario_id: int, dia: date | None = None) -> str:
    """sha256 de la configuración canónica (sin `reemplaza`, que no cambia el contenido), el usuario y el día."""
    config = {clave: valor for clave, valor in solicitud.items() if clave != "reemplaza"}
    canonico = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    base = f"{VERSION_HUELLA}|{usuario_id}|{(dia or date.today()).isoformat()}|{canonico}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


class CacheReportes:
    def __init__(self, fabrica_cliente=None):
        self._fabrica = fabrica_cliente or self._cliente_real
        self._cliente = None

    @staticmethod
    def _cliente_real():
        return redis.Redis.from_url(
            settings.redis_url,
            decode_responses=False,
            socket_timeout=2,
            socket_connect_timeout=2,
        )

    @property
    def cliente(self):
        if self._cliente is None:
            self._cliente = self._fabrica()
        return self._cliente

    @property
    def activa(self) -> bool:
        return bool(settings.reportes_cache_activa)

    # ------------------------------------------------------------ lectura
    def obtener(self, huella: str | None) -> bytes | None:
        if not huella or not self.activa:
            return None
        try:
            return self.cliente.get(PREFIJO + huella)
        except redis.RedisError:
            logger.warning("Caché de reportes: Redis no respondió al leer %s…", huella[:8], exc_info=True)
            return None

    def existe(self, huella: str | None) -> bool:
        if not huella or not self.activa:
            return False
        try:
            return bool(self.cliente.exists(PREFIJO + huella))
        except redis.RedisError:
            logger.warning("Caché de reportes: Redis no respondió al consultar %s…", huella[:8], exc_info=True)
            return False

    # ------------------------------------------------------------ escritura
    def guardar(self, huella: str, usuario_id: int, contenido: bytes) -> bool:
        """True si quedó en Redis; False si no se cacheó (tamaño, memoria, desactivada o error)."""
        if not self.activa or not huella:
            return False
        if len(contenido) > settings.reportes_cache_max_archivo:
            logger.info("Caché de reportes: %s bytes superan el tope; queda en disco.", len(contenido))
            return False
        try:
            usado = int(self.cliente.info("memory").get("used_memory", 0))
            if usado + len(contenido) > settings.reportes_cache_redis_max_usado:
                logger.warning("Caché de reportes: Redis usa %s bytes; no se escribe para no comprometerlo.", usado)
                return False
            ttl = settings.reportes_cache_ttl_s
            clave_indice = f"{PREFIJO}usuario:{usuario_id}"
            tuberia = self.cliente.pipeline()
            tuberia.set(PREFIJO + huella, contenido, ex=ttl)
            tuberia.zadd(clave_indice, {huella: time.time()})
            tuberia.expire(clave_indice, ttl)
            tuberia.execute()
            self._recortar_por_usuario(clave_indice)
            return True
        except redis.RedisError:
            logger.warning("Caché de reportes: Redis falló al guardar; el archivo queda en disco.", exc_info=True)
            return False

    def _recortar_por_usuario(self, clave_indice: str) -> None:
        sobrantes = self.cliente.zcard(clave_indice) - settings.reportes_cache_por_usuario
        if sobrantes > 0:
            viejos = self.cliente.zrange(clave_indice, 0, sobrantes - 1)
            if viejos:
                self.cliente.delete(*[PREFIJO + (v.decode() if isinstance(v, bytes) else v) for v in viejos])
                self.cliente.zrem(clave_indice, *viejos)

    def borrar(self, huella: str | None) -> None:
        if not huella:
            return
        try:
            self.cliente.delete(PREFIJO + huella)
        except redis.RedisError:
            logger.warning("Caché de reportes: no se pudo borrar %s…", huella[:8], exc_info=True)


cache_reportes = CacheReportes()
