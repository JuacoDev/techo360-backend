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

    latitud = Column(Numeric)
    longitud = Column(Numeric)