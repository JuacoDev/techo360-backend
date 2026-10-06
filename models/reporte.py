from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
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
    fecha_creacion = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )