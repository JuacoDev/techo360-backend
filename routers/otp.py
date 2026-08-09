"""
Router del servicio OTP. Expone endpoints genericos (request/verify) que
luego van a usar los flujos reales de registro y recuperacion de
contrasena, cuando existan.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from services.otp_service import (
    OTPCooldownError,
    OTPInvalidError,
    OTPTooManyAttemptsError,
    otp_service,
)

router = APIRouter(prefix="/otp", tags=["otp"])


class RequestOTPRequest(BaseModel):
    email: EmailStr
    name: str = ""


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    code: str


@router.post("/request")
async def request_otp(payload: RequestOTPRequest):
    """Genera y envia un codigo OTP nuevo al correo indicado."""
    try:
        await otp_service.request_otp(payload.email, payload.name)
    except OTPCooldownError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return {"status": "sent", "email": payload.email}


@router.post("/verify")
async def verify_otp(payload: VerifyOTPRequest):
    """Verifica un codigo OTP. Si es correcto, queda invalidado (un solo uso)."""
    try:
        await otp_service.verify_otp(payload.email, payload.code)
    except OTPTooManyAttemptsError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except OTPInvalidError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "verified", "email": payload.email}
