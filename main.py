import asyncio

import redis.asyncio as aioredis
from fastapi import FastAPI

from core.config import settings
from ingestion.scheduler import iniciar_scheduler
from routers import email, health, otp
from services.email_service import email_service

app = FastAPI(
    title="Techo 360 - Backend",
    description="API backend del proyecto Techo 360.",
    version="0.1.0"
)

app.include_router(health.router)
app.include_router(email.router)
app.include_router(otp.router)


@app.on_event("startup")
async def iniciar_pipeline_datos():
    # Capturar el event loop AQUI, mientras estamos corriendo en el (startup
    # es async). notificar_admin se llama despues desde un hilo worker aparte
    # (el pipeline corre via asyncio.to_thread dentro del scheduler, para no
    # bloquear el backend) -- ese hilo NO tiene su propio event loop, asi que
    # asyncio.create_task() ahi adentro falla con "RuntimeError: no running
    # event loop" (confirmado en produccion). run_coroutine_threadsafe() es
    # la forma correcta de mandar una corutina desde un hilo distinto al loop
    # principal.
    loop = asyncio.get_running_loop()

    def notificar_admin(asunto: str, mensaje: str):
        asyncio.run_coroutine_threadsafe(
            email_service.send_alert(settings.admin_alert_email, asunto, mensaje), loop
        )

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    iniciar_scheduler(redis_client, settings.database_url, notificar_admin)
