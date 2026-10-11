import json

import redis.asyncio as aioredis

from core.config import settings

# 36 h (antes 24 h, ver TTL_SEGUNDOS) -- los datos de densidad_poblacional no cambian intraday (la carga
# DANE es un proceso manual de una sola corrida por año, no un pipeline
# que actualice varias veces al dia -- ver ingestion/actualizar_densidad_dane.py
# en este repo). No hace falta invalidacion explicita atada al pipeline
# de carga: si se recarga un año, el cache viejo expira solo en <=36h (el precalentamiento
# de services/precalentamiento.py lo refresca a diario a las 03:30).
# 36 h (antes 24 h): mayor que el refresco diario de services/precalentamiento.py,
# para que nunca haya un hueco entre que expira una entrada y se reescribe.
TTL_SEGUNDOS = 36 * 60 * 60

# Version del contrato de zonas (ver AVISO v3 en ProyeccionCache). Subirla invalida
# TODAS las entradas de zonas sin tocar Redis a mano.
VERSION_ZONAS = "v3"


class ProyeccionCache:
    """Cache de las respuestas COMPLETAS de T-104/T-105 (no de cada
    municipio por separado) -- mismo patron que services/otp_service.py
    (un cliente de Redis propio, no compartido via app.state). Cache miss
    dispara el RPC real contra techo360-proyeccion; cache hit responde
    directo, sin tocar RabbitMQ ni la BD.

    >>> AVISO -- clave de zonas versionada e indicador-aware (score de
    >>> privacion por proyeccion) <<<
    La clave vieja (`proyeccion:zonas:{anio}`) no incluia el indicador
    porque solo existia un indicador (densidad_poblacional). Ahora que
    /api/proyecciones/zonas acepta mas de uno (densidad_poblacional,
    score_privacion), una clave sin indicador mezclaria datos de uno con
    el otro bajo el mismo anio. La clave nueva (`proyeccion:v2:zonas:
    {indicador}:{anio}`) nunca lee la vieja -- las entradas viejas que
    sigan vivas en Redis (hasta por TTL_SEGUNDOS) simplemente quedan sin
    uso y expiran solas, no se leen ni se borran a mano.

    >>> AVISO -- v3 (2026-10-10): cambio de CONTRATO de zonas <<<
    Las entradas de zonas ahora llevan origen/fiabilidad/componentes y es_real
    significa "no es proyeccion-*" (antes: "hay una fila no proyectada"). Las
    entradas `proyeccion:v2:zonas:*` que siguen vivas en produccion (TTL hasta 36 h)
    tienen el contrato VIEJO: reusar el prefijo v2 las serviria tal cual. Por eso zonas
    pasa a `proyeccion:v3:zonas:`. Las claves de limites_base (`v2`) y de serie
    (`proyeccion:serie:`) NO cambian de contenido y conservan su prefijo.
    scripts/limpiar_cache_proyeccion.py borra las v2 de zonas si se quiere no esperar
    al TTL (por defecto solo lista; no se ejecuta en produccion sin autorizacion)."""

    def __init__(self):
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    @staticmethod
    def _clave_zonas(indicador: str, anio: int) -> str:
        return f"proyeccion:{VERSION_ZONAS}:zonas:{indicador}:{anio}"

    @staticmethod
    def _clave_serie(codigo_municipio: str, desde: int, hasta: int) -> str:
        return f"proyeccion:serie:{codigo_municipio}:{desde}:{hasta}"

    @staticmethod
    def _clave_limites_base(anio_base: int) -> str:
        return f"proyeccion:v2:limites_base:{anio_base}"

    async def obtener_zonas(self, indicador: str, anio: int) -> list[dict] | None:
        crudo = await self._redis.get(self._clave_zonas(indicador, anio))
        return json.loads(crudo) if crudo is not None else None

    async def guardar_zonas(self, indicador: str, anio: int, zonas: list[dict]) -> None:
        await self._redis.set(self._clave_zonas(indicador, anio), json.dumps(zonas), ex=TTL_SEGUNDOS)

    async def obtener_limites_base(self, anio_base: int) -> dict | None:
        crudo = await self._redis.get(self._clave_limites_base(anio_base))
        return json.loads(crudo) if crudo is not None else None

    async def guardar_limites_base(self, anio_base: int, limites: dict) -> None:
        await self._redis.set(
            self._clave_limites_base(anio_base), json.dumps(limites), ex=TTL_SEGUNDOS
        )

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
