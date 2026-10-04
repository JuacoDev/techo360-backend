from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Text
from sqlalchemy.sql import func

from db.session_vector import BaseVector


class Mensaje(BaseVector):
    """Mapea 1:1 la tabla `mensajes` de postgres-vector (ver
    techo360-chatbot/migrations/0001_conversaciones_mensajes.sql).
    `rol` tiene un CHECK constraint en BD ('usuario'/'asistente') --
    no se repite aqui como Enum para no duplicar la fuente de verdad
    del esquema; un valor invalido falla en la BD, no en Python."""

    __tablename__ = "mensajes"
    __table_args__ = {"schema": "public"}

    id = Column(BigInteger, primary_key=True, index=True)
    conversacion_id = Column(
        BigInteger, ForeignKey("public.conversaciones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rol = Column(Text, nullable=False)
    contenido = Column(Text, nullable=False)
    # NULL = sin feedback todavia (ver AVISO en la migracion) -- nunca
    # se inicializa en False/True por defecto.
    feedback_util = Column(Boolean, nullable=True)
    fecha = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Fase C (migracion 0002 de techo360-chatbot): estado del mensaje. Para los
    # mensajes del ASISTENTE es siempre 'respondido'. Ver services/chatbot_estados.py.
    estado = Column(Text, nullable=False, server_default="respondido")
    error_detalle = Column(Text, nullable=True)
    actualizado_en = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
