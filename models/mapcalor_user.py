from sqlalchemy import Column, Integer, Text
from db.session import Base

class MapCalorUser(Base):
    __tablename__ = "mapcaloruser"
    __table_args__ = {"schema": "public"}

    id = Column(Integer, primary_key=True, index=True)
    correo = Column(Text, nullable=False)
    namevar = Column(Text, nullable=False)
    passwordvar = Column(Text, nullable=False)
