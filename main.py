import asyncio

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from ingestion.scheduler import iniciar_scheduler
from routers import auth, chatbot, health, otp, proyecciones ,usuarios, zonas, indicadores
from services import chatbot_estados
from services.chatbot_persistencia_consumer import iniciar_consumidor_persistencia
from db.session_vector import SessionLocalVector
from services.email_service import email_service
from services.precalentamiento import precalentar_tras_arranque

app = FastAPI(
    title="Techo 360 - Backend",
    description="API backend del proyecto Techo 360.",
    version="0.1.0"
)

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],  # frontend en Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router)
app.include_router(otp.router)
app.include_router(zonas.router)
app.include_router(usuarios.router)
app.include_router(proyecciones.router)
app.include_router(auth.router)
app.include_router(indicadores.router)
app.include_router(chatbot.router)

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

    # T-97 (rediseño): primer consumer de RabbitMQ que corre el backend
    # (antes solo tenia el RPC client de proyeccion, que abre conexion
    # por llamada). Corre in-process aqui, misma filosofia que el
    # scheduler DANE arriba -- sin proceso separado sin necesidad real.
    app.state.conexion_persistencia_chatbot = await iniciar_consumidor_persistencia(settings.rabbitmq_url)

    # Fase C: al arrancar ningun RPC al asistente sigue en vuelo (un solo worker):
    # todo mensaje 'pendiente'/'procesando' quedo huerfano por el reinicio -> 'error',
    # para que el usuario pueda reintentarlo o eliminarlo. Nunca tumba el arranque.
    def _barrer_mensajes_huerfanos() -> int:
        with SessionLocalVector() as db_vector:
            return chatbot_estados.barrer_al_arrancar(db_vector)

    try:
        huerfanos = await asyncio.to_thread(_barrer_mensajes_huerfanos)
        if huerfanos:
            print(f"[chatbot] {huerfanos} mensaje(s) 'procesando' pasaron a 'error' por el reinicio")
    except Exception as error:
        print(f"[chatbot] no se pudo barrer los mensajes huerfanos (se ignora): {error}")

    # D6: precalentar los caches ~90 s despues del arranque, en segundo plano
    # (no bloquea el startup ni el chequeo de salud del despliegue). Se guarda
    # la referencia para que el recolector no cancele la tarea.
    app.state.tarea_precalentamiento = asyncio.create_task(precalentar_tras_arranque())


@app.on_event("shutdown")
async def cerrar_conexiones():
    conexion = getattr(app.state, "conexion_persistencia_chatbot", None)
    if conexion is not None:
        await conexion.close()
