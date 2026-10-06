import json

import redis.asyncio as aioredis

from core.config import settings

# 24h -- los datos de densidad_poblacional no cambian intraday (la carga
# DANE es un proceso manual de una sola corrida por año, no un pipeline
# que actualice varias veces al dia -- ver ingestion/actualizar_densidad_dane.py
# en este repo). No hace falta invalidacion explicita atada al pipeline
# de carga: si se recarga un año, el cache viejo expira solo en <=24h.
TTL_SEGUNDOS = 24 * 60 * 60


class ProyeccionCache:
    """Cache de las respuestas COMPLETAS de T-104/T-105 (no de cada
    municipio por separado) -- mismo patron que services/otp_service.py
    (un cliente de Redis propio, no compartido via app.state). Cache miss
    dispara el RPC real contra techo360-proyeccion; cache hit responde
    directo, sin tocar RabbitMQ ni la BD."""

    def __init__(self):
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    @staticmethod
    def _clave_zonas(anio: int) -> str:
        return f"proyeccion:zonas:{anio}"

    @staticmethod
    def _clave_serie(codigo_municipio: str, desde: int, hasta: int) -> str:
        return f"proyeccion:serie:{codigo_municipio}:{desde}:{hasta}"

    async def obtener_zonas(self, anio: int) -> list[dict] | None:
        crudo = await self._redis.get(self._clave_zonas(anio))
        return json.loads(crudo) if crudo is not None else None

    async def guardar_zonas(self, anio: int, zonas: list[dict]) -> None:
        await self._redis.set(self._clave_zonas(anio), json.dumps(zonas), ex=TTL_SEGUNDOS)

    async def obtener_serie(self, codigo_municipio: str, desde: int, hasta: int) -> list[dict] | None:
        crudo = await self._redis.get(self._clave_serie(codigo_municipio, desde, hasta))
        return json.loads(crudo) if crudo is not None else None

    async def guardar_serie(self, codigo_municipio: str, desde: int, hasta: int, serie: list[dict]) -> None:
        await self._redis.set(
            self._clave_serie(codigo_municipio, desde, hasta), json.dumps(serie), ex=TTL_SEGUNDOS
        )


# Instancia unica del proceso, mismo patron que email_service/otp_service
# (importado directo, no inyectado via Depends -- consistente con como ya
# se usan esos dos en el resto del backend).
proyeccion_cache = ProyeccionCache()
