"""Precalentamiento de caches (D6).

Sin esto, tras un reinicio del backend, un cambio de version de clave o el
vencimiento de una entrada, el primer usuario espera el calculo completo:
~30 s para generar la proyeccion 2027-2030 y ~0,5-1,6 s de servidor para
/api/zonas. Aqui se calculan por adelantado, SIN sobrecargar el servicio:

  - Un item a la vez, con pausa entre items (el backend y el servicio de
    proyeccion tienen 0,3 CPU cada uno).
  - Candado global en Redis (SET NX): si hay dos instancias, solo una trabaja.
  - Cualquier error se registra y se sigue con el siguiente item; nunca se
    propaga (no debe tumbar el arranque ni el scheduler).

Que se precalienta:
  - /api/zonas: departamento y municipio del año base 2024 (lo unico que
    pide el frontend, ver ANIOS_CACHEABLES en services/zonas_cache.py).
  - Proyeccion: limites base 2024 y score_privacion de 2027 a 2030 (el rango
    por defecto del mapa de proyeccion, Proyectadas.jsx).

Cuando se llama:
  - ~90 s despues del arranque del backend (precalentar_tras_arranque): omite
    lo que ya esta en cache.
  - Refresco diario 03:30 y tras la ingesta del lunes (forzar=True): recalcula
    y sobrescribe, sin borrar antes, para que nunca haya un hueco.
"""
import asyncio
import logging

from db.session import SessionLocal
from services.proyeccion_cache import proyeccion_cache
from services.proyeccion_service import (
    ANIO_BASE_SCORE,
    INDICADOR_SCORE_PRIVACION,
    calcular_zonas_score_privacion,
)
from services.score_privacion import calcular_limites_base
from services.zonas_cache import NIVELES, zonas_cache

logger = logging.getLogger(__name__)

DEMORA_ARRANQUE_SEGUNDOS = 90
PAUSA_ENTRE_ITEMS_SEGUNDOS = 2.0
ANIOS_PROYECCION = (2027, 2028, 2029, 2030)
ANIO_ZONAS = 2024

LOCK_CLAVE = "precalentamiento:lock"
LOCK_SEGUNDOS = 15 * 60


def _adquirir_lock_global() -> bool:
    return bool(zonas_cache.cliente.set(LOCK_CLAVE, "1", nx=True, ex=LOCK_SEGUNDOS))


def _liberar_lock_global() -> None:
    zonas_cache.cliente.delete(LOCK_CLAVE)


def _precalentar_zonas(nivel: str, anio: int, forzar: bool) -> str:
    # Import diferido: routers.zonas importa services.zonas_cache.
    from routers.zonas import _construir_zonas
    from services.zonas_cache import serializar

    if not forzar and zonas_cache.obtener_etag(nivel, anio) is not None:
        return "omitido (ya en cache)"
    if not zonas_cache.adquirir_lock(nivel, anio):
        return "omitido (otro proceso lo esta calculando)"
    try:
        db = SessionLocal()
        try:
            cuerpo = serializar(_construir_zonas(nivel, anio, db))
        finally:
            db.close()
        zonas_cache.guardar(nivel, anio, cuerpo)
        return "ok"
    finally:
        zonas_cache.liberar_lock(nivel, anio)


async def _precalentar_limites_base(forzar: bool) -> str:
    if not forzar and await proyeccion_cache.obtener_limites_base(ANIO_BASE_SCORE) is not None:
        return "omitido (ya en cache)"
    db = SessionLocal()
    try:
        limites = await asyncio.to_thread(calcular_limites_base, db, ANIO_BASE_SCORE)
    finally:
        db.close()
    await proyeccion_cache.guardar_limites_base(ANIO_BASE_SCORE, limites)
    return "ok"


async def _precalentar_proyeccion(anio: int, forzar: bool) -> str:
    if not forzar and await proyeccion_cache.obtener_zonas(INDICADOR_SCORE_PRIVACION, anio) is not None:
        return "omitido (ya en cache)"
    db = SessionLocal()
    try:
        zonas = await calcular_zonas_score_privacion(anio, db)
    finally:
        db.close()
    await proyeccion_cache.guardar_zonas(INDICADOR_SCORE_PRIVACION, anio, [z.model_dump() for z in zonas])
    return "ok"


async def precalentar(forzar: bool = False, pausa: float = PAUSA_ENTRE_ITEMS_SEGUNDOS) -> dict:
    """Devuelve {item: resultado}. Nunca lanza."""
    resumen: dict[str, str] = {}
    try:
        if not await asyncio.to_thread(_adquirir_lock_global):
            logger.info("Precalentamiento: otra instancia ya esta trabajando, se omite.")
            return {"precalentamiento": "omitido (otra instancia)"}
    except Exception as error:  # Redis caido: sin candado no se arriesga a duplicar trabajo
        logger.warning("Precalentamiento omitido, Redis no disponible: %s", error)
        return {"precalentamiento": f"omitido (Redis no disponible: {error})"}

    items = [(f"zonas:{nivel}:{ANIO_ZONAS}", lambda nivel=nivel: asyncio.to_thread(_precalentar_zonas, nivel, ANIO_ZONAS, forzar))
             for nivel in NIVELES]
    items.append((f"proyeccion:limites_base:{ANIO_BASE_SCORE}", lambda: _precalentar_limites_base(forzar)))
    items += [(f"proyeccion:{INDICADOR_SCORE_PRIVACION}:{anio}", lambda anio=anio: _precalentar_proyeccion(anio, forzar))
              for anio in ANIOS_PROYECCION]

    try:
        for indice, (nombre, ejecutar) in enumerate(items):
            if indice > 0:
                await asyncio.sleep(pausa)
            try:
                resumen[nombre] = await ejecutar()
            except Exception as error:
                logger.exception("Precalentamiento: fallo %s", nombre)
                resumen[nombre] = f"error: {error}"
    finally:
        try:
            await asyncio.to_thread(_liberar_lock_global)
        except Exception:
            logger.warning("Precalentamiento: no se pudo liberar el candado; vence solo en %s s.", LOCK_SEGUNDOS)

    logger.info("Precalentamiento terminado: %s", resumen)
    return resumen


async def precalentar_tras_arranque(demora: float = DEMORA_ARRANQUE_SEGUNDOS) -> None:
    """Tarea de segundo plano lanzada en el startup. Espera `demora` para no
    competir con el chequeo de salud del despliegue."""
    try:
        await asyncio.sleep(demora)
        await precalentar(forzar=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Precalentamiento tras arranque: error inesperado (ignorado)")
