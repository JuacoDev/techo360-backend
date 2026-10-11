import ipaddress

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr

from services.otp_service import (
    OTPCooldownError,
    OTPEmailLimitError,
    OTPInvalidError,
    OTPIpDailyLimitError,
    OTPRateLimitError,
    OTPTooManyAttemptsError,
    otp_service,
)


def _ip_valida(valor: str) -> str | None:
    try:
        return str(ipaddress.ip_address(valor.strip()))
    except ValueError:
        return None


def _obtener_ip_cliente(request: Request) -> str:
    """IP real del cliente, a prueba de cabeceras falsificadas.

    El backend solo es alcanzable a traves del nginx de techo360-frontend (`location
    /otp/` y `/auth/`), que fija `X-Real-IP $remote_addr` (SOBRESCRIBE lo que mande el
    cliente) y AÑADE la IP real al FINAL de `X-Forwarded-For` (`$proxy_add_x_forwarded_for`).
    Por eso:
      1. X-Real-IP (si es una IP valida);
      2. el ULTIMO valor de X-Forwarded-For (el que añadio nuestro proxy);
      3. la IP directa de la conexion.
    El PRIMER valor de X-Forwarded-For lo escribe el cliente: usarlo permitia saltarse el
    limite por IP de /otp/request mandando una cabecera distinta en cada peticion.
    SUPUESTO: un solo proxy de confianza delante. Si se agrega otro (p. ej. un terminador
    TLS del dominio institucional) hay que revisar esto: X-Real-IP seria la IP de ese proxy."""
    real = _ip_valida(request.headers.get("x-real-ip", ""))
    if real:
        return real
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ultimo = _ip_valida(forwarded.split(",")[-1])
        if ultimo:
            return ultimo
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
                "Debe esperar antes de solicitar otro código OTP, superó el "
                "límite de solicitudes desde su IP en la ventana actual (o su tope "
                "diario), o ya se pidieron 5 códigos para ese correo en la última hora."
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
            detail=str(e),
            headers={"Retry-After": str(max(e.seconds_remaining, 1))},
        )
    except OTPCooldownError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(max(e.seconds_remaining, 1))},
        )

    except OTPEmailLimitError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e)
        )

    except OTPIpDailyLimitError as e:
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