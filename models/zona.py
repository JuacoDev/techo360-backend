from sqlalchemy import Column, Integer, String, Float

from db.session import Base


class Zona(Base):
    __tablename__ = "zonas"

    id = Column(Integer, primary_key=True, index=True)

    municipio = Column(String(100), nullable=False)

    nombre = Column(String(150), nullable=False)

    latitud = Column(Float, nullable=False)

    longitud = Column(Float, nullable=False)