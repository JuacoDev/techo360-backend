from fastapi import APIRouter


router = APIRouter()


@router.get(
    "/",
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
    summary="Verificar estado del servicio",
    description="Comprueba el estado actual del servicio backend."
)
def health():
    return {
        "status": "healthy"
    }


# prueba CI/CD
