from datetime import datetime, timedelta, timezone
from sqlalchemy import or_

import jwt
from sqlalchemy.orm import Session

from core.config import settings
from models.mapcalor_user import MapCalorUser


def verificar_password(password: str, password_db: str) -> bool:
    # Comparación directa en texto plano
    return password == password_db


def crear_access_token(user_id: int, correo: str, name: str) -> str:
    ahora = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "correo": correo,
        "name": name,
        "iat": ahora,
        "exp": ahora + timedelta(hours=1),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm="HS256"
    )


def autenticar_usuario(db: Session, identifier: str, password: str):
    identifier = identifier.strip()

    print("DEBUG identifier repr:", repr(identifier))

    query = db.query(MapCalorUser).filter(
        or_(
            MapCalorUser.correo == identifier,
            MapCalorUser.namevar == identifier
        )
    )

    usuario = query.first()

    print("DEBUG usuario encontrado:", usuario)

    if not usuario:
        return None

    print("DEBUG correo en BD:", repr(usuario.correo))
    print("DEBUG namevar en BD:", repr(usuario.namevar))
    print("DEBUG password recibida:", repr(password))
    print("DEBUG password en BD:", repr(usuario.passwordvar))

    if not verificar_password(password, usuario.passwordvar.strip()):
        return None

    return usuario