"""
Paso 7: programa el pipeline para que corra solo, dentro del proceso del
Backend Principal (no como contenedor aparte, para no gastar mas RAM del
presupuesto ya ajustado).

Como integrarlo en techo360-backend (main.py):

    from services.email_service import email_service  # ya existe, tiene su propio redis async
    from ingestion.scheduler import iniciar_scheduler

    ADMIN_EMAIL = "juan.tellez@upb.edu.co"  # o el que corresponda

    @app.on_event("startup")
    async def startup():
        import redis.asyncio as aioredis
        # IMPORTANTE: capturar el event loop AQUI, mientras estamos
        # corriendo dentro de el (startup es async). notificar_admin se
        # va a llamar despues desde un hilo worker aparte (el pipeline
        # corre via asyncio.to_thread dentro del scheduler, para no
        # bloquear el backend) -- ese hilo NO tiene su propio event loop,
        # asi que asyncio.create_task() ahi adentro falla con
        # "RuntimeError: no running event loop" (esto paso de verdad en
        # produccion la primera vez que corrio el scheduler). La forma
        # correcta de mandar una corutina desde un hilo distinto al loop
        # principal es run_coroutine_threadsafe(), no create_task().
        loop = asyncio.get_running_loop()

        def notificar_admin(asunto: str, mensaje: str):
            asyncio.run_coroutine_threadsafe(
                email_service.send_alert(ADMIN_EMAIL, asunto, mensaje), loop
            )

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        iniciar_scheduler(redis_client, settings.database_url, notificar_admin)
"""
import asyncio
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

# Semanal, no diario -- estas fuentes no cambian todos los dias, y correr
# mas seguido de lo necesario solo gasta CPU sin ningun beneficio real.
CRON_SEMANAL = {"day_of_week": "mon", "hour": 3, "minute": 0}  # lunes 3am, hora de bajo trafico


async def _ya_corrio_hoy(redis_client) -> bool:
    """Guardia contra doble ejecucion. El backend se reinicia seguido
    con cada deploy del CI/CD -- sin esto, un reinicio a media tarde
    podria disparar el pipeline dos veces el mismo dia."""
    clave = f"pipeline_datos:ultima_corrida:{date.today().isoformat()}"
    return await redis_client.exists(clave) == 1


async def _marcar_corrida_de_hoy(redis_client):
    clave = f"pipeline_datos:ultima_corrida:{date.today().isoformat()}"
    await redis_client.set(clave, "1", ex=26 * 60 * 60)  # 26h, mismo patron que el resto del proyecto


async def _job_pipeline(redis_client, database_url: str, notificar_admin):
    if await _ya_corrio_hoy(redis_client):
        logger.info("El pipeline de datos ya corrio hoy, se omite esta ejecucion.")
        return

    logger.info("Iniciando corrida programada del pipeline de datos...")
    from ingestion.run import run  # import diferido para no acoplar el modulo al arranque del backend

    # Se marca ANTES de correr, no despues -- si el pipeline tarda mas de
    # lo esperado y el backend se reinicia a mitad de la corrida, no
    # queremos que un segundo arranque lo dispare de nuevo por encima
    # del que ya esta corriendo.
    await _marcar_corrida_de_hoy(redis_client)

    # run() es sincrono (httpx.Client + psycopg2 sincronos, puede tardar
    # varios segundos entre las llamadas HTTP y las escrituras a BD) --
    # se corre en un thread aparte para NO bloquear el event loop de
    # FastAPI mientras tanto (si no, se congelarian todas las demas
    # peticiones del backend durante ese rato).
    await asyncio.to_thread(
        run,
        anio=date.today().year,
        notificar_admin=notificar_admin,
        database_url=database_url,
    )


def iniciar_scheduler(redis_client, database_url: str, notificar_admin=None) -> AsyncIOScheduler:
    """AsyncIOScheduler (no BackgroundScheduler) a proposito: corre en el
    mismo event loop que ya usa FastAPI, sin abrir threads/procesos
    nuevos por su cuenta (el thread para el run() sincrono se abre solo
    durante la corrida real, no permanentemente)."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _job_pipeline,
        trigger="cron",
        args=[redis_client, database_url, notificar_admin],
        id="pipeline_datos_semanal",
        replace_existing=True,
        **CRON_SEMANAL,
    )
    scheduler.start()
    logger.info(f"Scheduler del pipeline de datos iniciado (corre {CRON_SEMANAL}).")
    return scheduler


if __name__ == "__main__":
    # Prueba manual de la guardia contra doble ejecucion, con un Redis
    # async simulado (sin necesitar un Redis real ni el resto de FastAPI).
    class _FakeAsyncRedis:
        def __init__(self):
            self._store = {}

        async def exists(self, clave):
            return 1 if clave in self._store else 0

        async def set(self, clave, valor, ex=None):
            self._store[clave] = valor

    async def _probar():
        redis_falso = _FakeAsyncRedis()

        assert await _ya_corrio_hoy(redis_falso) is False
        print("1. Antes de marcar: no ha corrido hoy -- OK")

        await _marcar_corrida_de_hoy(redis_falso)
        assert await _ya_corrio_hoy(redis_falso) is True
        print("2. Despues de marcar: si ha corrido hoy -- OK")

        print()
        print("Guardia contra doble ejecucion (version async) funciona correctamente.")

    asyncio.run(_probar())
