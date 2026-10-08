from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from db.session import Base


class Reporte(Base):
    __tablename__ = "reportes"
    __table_args__ = {"schema": "public"}

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(
        Integer,
        ForeignKey("public.mapcaloruser.id"),
        nullable=False,
        index=True,
    )
    titulo = Column(Text, nullable=False)
    contenido = Column(Text, nullable=False)
    estado = Column(String(20), nullable=False, default="listo", server_default="listo")
    formato = Column(String(10), nullable=True)
    solicitud = Column(JSONB, nullable=True)
    archivo_nombre = Column(String(255), nullable=True)
    error = Column(Text, nullable=True)
    fecha_creacion = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )