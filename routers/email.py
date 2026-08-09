"""
Router de prueba para el servicio de correo. El unico endpoint que expone
es una prueba de humo (envia un OTP de ejemplo) para verificar que la
integracion con Brevo funciona antes de conectarlo a los flujos reales de
registro/recuperacion de contrasena (que todavia no existen).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from services.email_service import (
    EmailQuotaExceededError,
    EmailSendError,
    email_service,
)

router = APIRouter(prefix="/email", tags=["email"])


class TestEmailRequest(BaseModel):
    to_email: EmailStr
    to_name: str = ""


@router.post("/test")
async def send_test_email(payload: TestEmailRequest):
    """Envia un correo de prueba (OTP de ejemplo) para confirmar que la
    integracion con Brevo esta configurada correctamente."""
    try:
        await email_service.send_otp(payload.to_email, "123456", payload.to_name)
    except EmailQuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except EmailSendError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "sent", "to": payload.to_email}
