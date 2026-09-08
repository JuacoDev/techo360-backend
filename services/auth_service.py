from datetime import datetime, timedelta, timezone
from sqlalchemy import or_

import bcrypt
import jwt
from sqlalchemy.orm import Session

from core.config import settings
from models.mapcalor_user import MapCalorUser

# bcrypt trunca (o en versiones nuevas, lanza error) mas alla de 72 bytes --
# se trunca aqui explicitamente antes de hashear/verificar para que una
# contraseña larga no rompa el request con un error 500 sin explicacion.
_BCRYPT_MAX_BYTES = 72


def hashear_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verificar_password(password: str, password_hash: str) -> bool:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))
    except ValueError:
        # password_hash no tiene formato bcrypt valido (dato legacy sin
        # migrar, o corrupto) -- nunca autentica, pero tampoco tumba el
        # request con un 500.
        return False


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

    query = db.query(MapCalorUser).filter(
        or_(
            MapCalorUser.correo == identifier,
            MapCalorUser.namevar == identifier
        )
    )

    usuario = query.first()

    if not usuario:
        return None

    if not verificar_password(password, usuario.passwordvar.strip()):
        return None

    return usuario

def registrar_usuario(db: Session, correo: str, namevar: str, password: str):
    existente = db.query(MapCalorUser).filter(
        or_(MapCalorUser.correo == correo, MapCalorUser.namevar == namevar)
    ).first()
    if existente:
        return None

    nuevo = MapCalorUser(
        correo=correo,
        namevar=namevar,
        passwordvar=hashear_password(password)
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo