"""
Servicio de generacion y verificacion de codigos OTP (One-Time Password),
usados para registro por correo y recuperacion de contrasena.

Los codigos se almacenan en Redis con expiracion automatica -- no se
persisten en ninguna base de datos relacional, ya que son de un solo uso
y de vida muy corta (10 minutos, igual que dice el texto del correo que
ya envia email_service.send_otp).
"""
import logging
import random
import string

import redis.asyncio as aioredis

from core.config import settings
from services.email_service import email_service

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 10 * 60          # 10 minutos, coincide con el texto del correo
OTP_LENGTH = 6
MAX_VERIFY_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60       # no permitir reenviar antes de 60s

# El cooldown de arriba es POR CORREO -- no evita que la misma IP pida OTP
# para muchos correos distintos (o el mismo, cada 60s sostenido) y agote
# el cupo diario COMPARTIDO de Brevo (300/dia, ver email_service.py) antes
# que nadie mas pueda recibir un correo real. Limite adicional por IP:
# 10 solicitudes cada 15 minutos es generoso para un usuario legitimo
# (pedir + reintentar un par de veces si no le llego o se equivoco no
# se acerca a 10), pero acota el peor caso de una sola IP a 40/hora --
# muy por debajo del cupo diario compartido, sin necesitar autenticacion
# (este endpoint es pre-login a proposito).
IP_RATE_LIMIT_MAX = 10
IP_RATE_LIMIT_WINDOW_SECONDS = 15 * 60


class OTPTooManyAttemptsError(Exception):
    """Se agotaron los intentos de verificacion para este codigo."""
    pass


class OTPRateLimitError(Exception):
    """Se supero el limite de solicitudes de OTP desde esta IP en la ventana de tiempo."""

    def __init__(self, seconds_remaining: int):
        self.seconds_remaining = seconds_remaining
        super().__init__(f"Demasiadas solicitudes de OTP desde esta IP. Espera {seconds_remaining}s.")


class OTPInvalidError(Exception):
    """El codigo no existe, expiro, o no coincide."""
    pass


class OTPCooldownError(Exception):
    """Se pidio un reenvio antes de que pase el tiempo minimo entre envios."""

    def __init__(self, seconds_remaining: int):
        self.seconds_remaining = seconds_remaining
        super().__init__(f"Espera {seconds_remaining}s antes de solicitar otro codigo.")


class OTPService:
    def __init__(self):
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    def _code_key(self, email: str) -> str:
        return f"otp:{email}:code"

    def _attempts_key(self, email: str) -> str:
        return f"otp:{email}:attempts"

    def _cooldown_key(self, email: str) -> str:
        return f"otp:{email}:cooldown"

    def _ip_rate_limit_key(self, ip: str) -> str:
        return f"otp:ip_limit:{ip}"

    def _generate_code(self) -> str:
        return "".join(random.choices(string.digits, k=OTP_LENGTH))

    async def _check_ip_rate_limit(self, ip: str) -> None:
        """Mismo mecanismo (INCR + EXPIRE en el primer hit) que ya usa
        email_service._check_and_increment_quota() para el cupo diario --
        ventana fija, no deslizante, suficiente para este caso."""
        key = self._ip_rate_limit_key(ip)
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, IP_RATE_LIMIT_WINDOW_SECONDS)

        if count > IP_RATE_LIMIT_MAX:
            ttl_remaining = await self._redis.ttl(key)
            raise OTPRateLimitError(ttl_remaining if ttl_remaining > 0 else IP_RATE_LIMIT_WINDOW_SECONDS)

    async def request_otp(self, email: str, to_name: str = "", ip: str | None = None) -> None:
        """Genera un OTP nuevo, lo guarda en Redis, y lo envia por correo.

        `ip` es opcional (None si el router no la pudo determinar) para no
        romper la firma si algun dia se llama este metodo desde otro lugar
        sin contexto de request HTTP -- pero el router SIEMPRE la pasa en
        el uso real."""
        if ip:
            await self._check_ip_rate_limit(ip)

        cooldown_key = self._cooldown_key(email)
        ttl_remaining = await self._redis.ttl(cooldown_key)
        if ttl_remaining and ttl_remaining > 0:
            raise OTPCooldownError(ttl_remaining)

        code = self._generate_code()
        code_key = self._code_key(email)
        attempts_key = self._attempts_key(email)

        # pipeline para dejar la escritura atomica
        pipe = self._redis.pipeline()
        pipe.set(code_key, code, ex=OTP_TTL_SECONDS)
        pipe.delete(attempts_key)  # resetear intentos al pedir un codigo nuevo
        pipe.set(cooldown_key, "1", ex=RESEND_COOLDOWN_SECONDS)
        await pipe.execute()

        await email_service.send_otp(email, code, to_name)
        logger.info(f"OTP generado y enviado a {email}")

    async def verify_otp(self, email: str, code: str) -> bool:
        """Verifica el codigo. Si es correcto, lo invalida (un solo uso).
        Lanza excepciones especificas si expiro, no coincide, o se
        agotaron los intentos de verificacion."""
        code_key = self._code_key(email)
        attempts_key = self._attempts_key(email)

        stored_code = await self._redis.get(code_key)
        if stored_code is None:
            raise OTPInvalidError("El codigo expiro o no existe. Solicita uno nuevo.")

        attempts = await self._redis.incr(attempts_key)
        if attempts == 1:
            code_ttl = await self._redis.ttl(code_key)
            await self._redis.expire(attempts_key, code_ttl if code_ttl > 0 else OTP_TTL_SECONDS)

        if attempts > MAX_VERIFY_ATTEMPTS:
            await self._redis.delete(code_key)
            await self._redis.delete(attempts_key)
            raise OTPTooManyAttemptsError(
                f"Demasiados intentos fallidos ({MAX_VERIFY_ATTEMPTS}). Solicita un codigo nuevo."
            )

        if stored_code != code:
            raise OTPInvalidError(
                f"Codigo incorrecto. Intento {attempts}/{MAX_VERIFY_ATTEMPTS}."
            )

        # correcto: invalidar de inmediato (un solo uso)
        await self._redis.delete(code_key)
        await self._redis.delete(attempts_key)
        logger.info(f"OTP verificado correctamente para {email}")
        return True


otp_service = OTPService()
