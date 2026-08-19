from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from core.config import settings
from models.mapcalor_user import MapCalorUser


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def verificar_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def crear_access_token(user_id: int, correo: str) -> str:
    ahora = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "correo": correo,
        "iat": ahora,
        "exp": ahora + timedelta(hours=1),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm="HS256"
    )


def autenticar_usuario(
    db: Session,
    correo: str,
    password: str
):
    usuario = (
        db.query(MapCalorUser)
        .filter(MapCalorUser.correo == correo)
        .first()
    )

    if not usuario:
        return None

    if not verificar_password(password, usuario.passwordVar):
        return None

    return usuario