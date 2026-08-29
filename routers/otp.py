from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from services.otp_service import (
    OTPCooldownError,
    OTPInvalidError,
    OTPTooManyAttemptsError,
    otp_service,
)

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
            "description": "Debe esperar antes de solicitar otro código OTP."
        }
    }
)
async def request_otp(payload: RequestOTPRequest):
    try:
        await otp_service.request_otp(
            payload.email,
            payload.name
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

    return {
        "status": "verified",
        "email": payload.email
    }