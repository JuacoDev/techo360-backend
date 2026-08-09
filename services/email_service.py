"""
Servicio de envio de correos transaccionales via Brevo (RF-03 / RNF-02 del
Modulo 7 - Email Sender).

Usa la API REST de Brevo (no SMTP propio) y lleva un contador diario en
Redis para avisar cuando se acerca al limite del plan gratuito (300/dia)
en vez de descubrirlo cuando ya empezaron a fallar los envios.
"""
import logging
from datetime import date

import httpx
import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
DAILY_LIMIT = 300
WARNING_THRESHOLD = 0.85  # avisar al 85% del limite (255/300)


class EmailSendError(Exception):
    """La API de Brevo respondio con un error al intentar enviar."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Brevo respondio {status_code}: {detail}")


class EmailQuotaExceededError(Exception):
    """Se alcanzo el limite diario de correos antes de intentar enviar."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(f"Limite diario de correos alcanzado: {count}/{DAILY_LIMIT}")


class EmailService:
    def __init__(self):
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    def _counter_key(self) -> str:
        return f"email:sent_count:{date.today().isoformat()}"

    async def _check_and_increment_quota(self) -> int:
        key = self._counter_key()
        count = await self._redis.incr(key)
        if count == 1:
            # 26h de TTL (no 24h exactas) para cubrir el dia completo aunque
            # el primer envio del dia haya sido tarde, sin arrastrar el
            # contador al dia siguiente.
            await self._redis.expire(key, 26 * 60 * 60)

        if count > DAILY_LIMIT:
            logger.error(
                f"Limite diario de Brevo excedido ({count}/{DAILY_LIMIT}). "
                f"Correo NO enviado."
            )
            raise EmailQuotaExceededError(count)
        elif count >= DAILY_LIMIT * WARNING_THRESHOLD:
            logger.warning(
                f"Acercandose al limite diario de Brevo: {count}/{DAILY_LIMIT} "
                f"correos enviados hoy."
            )
        return count

    async def _send(self, to_email: str, to_name: str, subject: str, html_content: str) -> None:
        await self._check_and_increment_quota()

        payload = {
            "sender": {"email": settings.brevo_sender_email, "name": settings.brevo_sender_name},
            "to": [{"email": to_email, "name": to_name or to_email}],
            "subject": subject,
            "htmlContent": html_content,
        }
        headers = {
            "api-key": settings.brevo_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(BREVO_API_URL, json=payload, headers=headers)

        if response.status_code >= 400:
            logger.error(
                f"Error enviando correo via Brevo a {to_email}: "
                f"{response.status_code} - {response.text}"
            )
            raise EmailSendError(response.status_code, response.text)

        logger.info(f"Correo '{subject}' enviado a {to_email} via Brevo.")

    # ------------------------------------------------------------
    # Casos de uso concretos (RF del modulo 7 + lo que ya definimos
    # para autenticacion: OTP y recuperacion de contrasena)
    # ------------------------------------------------------------

    async def send_otp(self, to_email: str, otp_code: str, to_name: str = "") -> None:
        subject = "Tu codigo de verificacion - Techo 360"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 480px;">
          <h2 style="color:#1F4E5A;">Techo 360</h2>
          <p>Tu codigo de verificacion es:</p>
          <p style="font-size: 28px; font-weight: bold; letter-spacing: 4px;">{otp_code}</p>
          <p>Este codigo expira en 10 minutos. Si no solicitaste esto, ignora este correo.</p>
        </div>
        """
        await self._send(to_email, to_name, subject, html)

    async def send_password_reset(self, to_email: str, reset_link: str, to_name: str = "") -> None:
        subject = "Recuperacion de contrasena - Techo 360"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 480px;">
          <h2 style="color:#1F4E5A;">Techo 360</h2>
          <p>Solicitaste recuperar tu contrasena. Haz clic en el siguiente enlace:</p>
          <p><a href="{reset_link}">Restablecer contrasena</a></p>
          <p>Si no solicitaste esto, ignora este correo — tu contrasena actual sigue siendo valida.</p>
        </div>
        """
        await self._send(to_email, to_name, subject, html)

    async def send_alert(self, to_email: str, subject: str, message: str, to_name: str = "") -> None:
        """Para el administrador: alertas de cambios criticos, fallos del
        job de actualizacion de datos, etc. (RF-01 del modulo 7)."""
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 480px;">
          <h2 style="color:#1F4E5A;">Techo 360 — Alerta del sistema</h2>
          <p>{message}</p>
        </div>
        """
        await self._send(to_email, to_name, subject, html)


email_service = EmailService()
