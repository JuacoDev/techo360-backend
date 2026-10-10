from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr

from services.otp_service import (
    OTPCooldownError,
    OTPInvalidError,
    OTPRateLimitError,
    OTPTooManyAttemptsError,
    otp_service,
)


def _obtener_ip_cliente(request: Request) -> str:
    """X-Forwarded-For primero (la IP real del usuario, si hay un proxy
    reenviando -- ver nginx.conf en techo360-frontend, location /api/ y
    /auth/ ya lo hacen); si no viene, cae a la IP directa de la conexion.
    request.client.host solo, sin esto, daria siempre la IP del propio
    contenedor de nginx en produccion, no la del usuario real."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"

router = APIRouter(
    prefix="/otp",
    tags=["otp"]
)


class RequestOTPRequest(BaseModel):
    email: EmailStr
    name: str = ""


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    code: str


class OTPResponse(BaseModel):
    status: str
    email: EmailStr


@router.post(
    "/request",
    response_model=OTPResponse,
    status_code=200,
    summary="Solicitar código OTP",
    description="Genera y envía un nuevo código OTP al correo indicado.",
    responses={
        429: {
            "description": (
                "Debe esperar antes de solicitar otro código OTP, o superó "
                "el límite de solicitudes desde su IP en la ventana actual."
            )
        }
    }
)
async def request_otp(payload: RequestOTPRequest, request: Request):
    try:
        await otp_service.request_otp(
            payload.email,
            payload.name,
            ip=_obtener_ip_cliente(request),
        )

    except OTPRateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e)
        )

    except OTPCooldownError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e)
        )

    return {
        "status": "sent",
        "email": payload.email
    }


@router.post(
    "/verify",
    response_model=OTPResponse,
    status_code=200,
    summary="Verificar código OTP",
    description=(
        "Verifica un código OTP. Si es correcto, "
        "queda invalidado para impedir su reutilización."
    ),
    responses={
        400: {
            "description": "El código OTP es inválido."
        },
        429: {
            "description": "Se excedió el número máximo de intentos."
        }
    }
)
async def verify_otp(payload: VerifyOTPRequest):
    try:
        await otp_service.verify_otp(
            payload.email,
            payload.code
        )

    except OTPTooManyAttemptsError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e)
        )

    except OTPInvalidError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    # Constancia que /auth/register exige y consume (el backend no confia en que el
    # frontend haya pasado por aqui).
    await otp_service.marcar_verificado(payload.email)

    return {
        "status": "verified",
        "email": payload.email
    }