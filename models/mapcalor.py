from sqlalchemy import Column, Integer, Text, Numeric
from db.session import Base


class MapCalor(Base):
    __tablename__ = "mapcalordb"

    # UNIQUE(codigoDivipola, nameVar, yearM) a nivel de tabla (cambio
    # 2026-08-30, ver ingestion/db.py) -- antes era por nombre de
    # municipio; se corrigio porque el nombre no es una clave confiable
    # (66 nombres del anexo del DANE se repiten entre departamentos
    # distintos, ej. "Sabanalarga" en Antioquia/Atlantico/Casanare).
    # No declarado aqui como UniqueConstraint porque esta clase ORM no
    # se usa para escribir en produccion (ver ingestion/db.py, que
    # escribe con SQL directo por el trigger BEFORE INSERT de Jeremy).
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