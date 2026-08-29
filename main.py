from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import email, health, otp, zonas, usuarios, auth

app = FastAPI(
    title="Techo 360 - Backend",
    description="API backend del proyecto Techo 360.",
    version="0.1.0"
)

# Configuración de CORS
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
app.include_router(email.router)
app.include_router(otp.router)
app.include_router(zonas.router)
app.include_router(usuarios.router)
app.include_router(auth.router)
