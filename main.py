from fastapi import FastAPI

from routers import email, health, otp, zonas, usuarios

app = FastAPI(
    title="Techo 360 - Backend",
    description="API backend del proyecto Techo 360.",
    version="0.1.0"
)

app.include_router(health.router)
app.include_router(email.router)
app.include_router(otp.router)
app.include_router(zonas.router)
app.include_router(usuarios.router)
