from sqlalchemy import Column, Integer, Text, Numeric
from db.session import Base


class MapCalor(Base):
    __tablename__ = "mapcalordb"

    id = Column(Integer, primary_key=True)
    municipios = Column(Text, nullable=False)
    nameVar = Column(Text, nullable=False)
    valueVar = Column(Numeric)
    yearM = Column(Integer, nullable=False)
    originVar = Column(Text)

    # NULL para todo dato real observado -- solo se llena para filas de
    # relleno/estimacion (ver convencion de originVar documentada en
    # ingestion/db.py). Nombre en minuscula a proposito, sin mapeo
    # explicito de columna: coincide tal cual con el nombre real que
    # Postgres pliega (ver aviso del modulo sobre nameVar/yearM/originVar,
    # que SI necesitarian Column(name="...") si algun dia se usan de
    # verdad -- intervaloconfianza no tiene ese problema al ser todo
    # minuscula desde el nombre del atributo).
    intervaloconfianza = Column(Numeric, nullable=True)

    latitud = Column(Numeric)
    longitud = Column(Numeric)