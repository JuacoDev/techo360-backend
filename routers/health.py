from pydantic import BaseModel
from fastapi import APIRouter


router = APIRouter()


class RootResponse(BaseModel):
    status: str
    service: str


class HealthResponse(BaseModel):
    status: str


@router.get(
    "/",
    response_model=RootResponse,
    status_code=200,
    summary="Verificar funcionamiento del backend",
    description="Comprueba que el backend de Techo 360 se encuentre funcionando correctamente."
)
def root():
    return {
        "status": "ok",
        "service": "backend"
    }


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=200,
    summary="Verificar estado del servicio",
    description="Comprueba el estado actual del servicio backend."
)
def health():
    return {
        "status": "healthy"
    }