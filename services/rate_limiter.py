
import hashlib

from fastapi import HTTPException, Request


LUA_RATE_LIMIT = """
local cantidad = redis.call('INCR', KEYS[1])
if cantidad == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local restante = redis.call('TTL', KEYS[1])
return {cantidad, restante}
"""


def obtener_ip_cliente(request: Request) -> str:
    """Obtiene la IP reenviada por el proxy o la conexión directa."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "desconocida"


async def verificar_limite(
    request: Request,
    ambito: str,
    identificador: str,
    limite: int,
    ventana_segundos: int,
) -> None:
    redis_client = getattr(request.app.state, "redis_client", None)

    if redis_client is None:
        raise HTTPException(
            status_code=503,
            detail="El servicio de control de solicitudes no está disponible.",
        )

    digest = hashlib.sha256(
        identificador.encode("utf-8")
    ).hexdigest()

    clave = f"rate_limit:{ambito}:{digest}"

    try:
        resultado = await redis_client.eval(
            LUA_RATE_LIMIT,
            1,
            clave,
            ventana_segundos,
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="No se pudo verificar el límite de solicitudes.",
        ) from error

    cantidad = int(resultado[0])
    restante = max(int(resultado[1]), 1)

    if cantidad > limite:
        raise HTTPException(
            status_code=429,
            detail="Has superado el límite de solicitudes. Intenta más tarde.",
            headers={"Retry-After": str(restante)},
        )