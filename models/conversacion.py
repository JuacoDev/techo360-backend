from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from db.session_vector import BaseVector


class Conversacion(BaseVector):
    """Mapea 1:1 la tabla `conversaciones` de postgres-vector (ver
    techo360-chatbot/migrations/0001_conversaciones_mensajes.sql) --
    esquema definido alla, este modelo solo lo espeja para que el
    backend pueda leer/escribir."""

    __tablename__ = "conversaciones"
    __table_args__ = {"schema": "public"}

    id = Column(BigInteger, primary_key=True, index=True)
    # Identificador PUBLICO y opaco (migracion 0003 de techo360-chatbot). Lo genera
    # la BD (gen_random_uuid); la API lo expone y lo acepta en lugar del id entero
    # secuencial. REQUIERE la migracion 0003 aplicada antes de desplegar este modelo.
    public_id = Column(UUID(as_uuid=True), nullable=False, unique=True, server_default=text("gen_random_uuid()"))
    usuario_id = Column(Integer, nullable=False, index=True)
    consentimiento_entrenamiento = Column(Boolean, nullable=False)
    fecha = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
