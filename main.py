from fastapi import FastAPI

from routers import email, health

app = FastAPI(title="Techo 360 - Backend")

app.include_router(health.router)
app.include_router(email.router)
