from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from services.email_service import (
    EmailQuotaExceededError,
    EmailSendError,
    email_service,
)

router = APIRouter(
    prefix="/email",
    tags=["email"]
)


class TestEmailRequest(BaseModel):
    to_email: EmailStr
    to_name: str = ""


class TestEmailResponse(BaseModel):
    status: str
    to: EmailStr


@router.post(
    "/test",
    response_model=TestEmailResponse,
    status_code=200,
    summary="Enviar correo de prueba",
    description=(
        "Envía un correo de prueba con un OTP de ejemplo "
        "para confirmar que la integración con Brevo funciona correctamente."
    ),
    responses={
        429: {
            "description": "Se excedió el límite de envío de correos."
        },
        502: {
            "description": "Error al enviar el correo mediante Brevo."
        }
    }
)
async def send_test_email(payload: TestEmailRequest):
    try:
        await email_service.send_otp(
            payload.to_email,
            "123456",
            payload.to_name
        )

    except EmailQuotaExceededError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e)
        )

    except EmailSendError as e:
        raise HTTPException(
            status_code=502,
            detail=str(e)
        )

    return {
        "status": "sent",
        "to": payload.to_email
    }