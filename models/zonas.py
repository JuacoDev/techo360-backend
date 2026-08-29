from sqlalchemy import Column, String, DateTime, ForeignKey
from geoalchemy2 import Geometry

from db.session import Base


class ZonaDepartamento(Base):
    __tablename__ = "zonas_departamento"

    codigo_departamento = Column(String(2), primary_key=True)
    nombre = Column(String(100), nullable=False)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)
    actualizado_en = Column(DateTime, nullable=False)


class ZonaMunicipio(Base):
    __tablename__ = "zonas_municipio"

    codigo_municipio = Column(String(6), primary_key=True)
    codigo_departamento = Column(
        String(2),
        ForeignKey("zonas_departamento.codigo_departamento"),
        nullable=False
    )
    nombre = Column(String(250), nullable=False)
    geom = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=False)
    actualizado_en = Column(DateTime, nullable=False)