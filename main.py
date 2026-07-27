from fastapi import FastAPI

from routers import health

app = FastAPI(title="Techo 360 - Backend")

app.include_router(health.router)
