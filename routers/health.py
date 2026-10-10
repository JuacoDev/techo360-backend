from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.rabbitmq_health import consultar_estado_rabbitmq


router = APIRouter()


class RootResponse(BaseModel):
    status: str
    service: str


@router.get(
    "/",
    response_model=RootResponse,
    summary="Verificar funcionamiento del backend",
)
def root():
    return {
        "status": "ok",
        "service": "backend",
    }


@router.get(
    "/health",
    summary="Verificar backend, RabbitMQ y consumers",
)
async def health():
    estado = await consultar_estado_rabbitmq()

    if not estado["healthy"]:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                **estado,
            },
        )

    return {
        "status": "healthy",
        **estado,
    }