from sqlalchemy import Column, Integer, String

from db.session import Base


class MapCalorUser(Base):
    __tablename__ = "MapCalorUser"

    id = Column(Integer, primary_key=True, index=True)
    correo = Column(String, unique=True, nullable=False, index=True)
    nameVar = Column(String, nullable=False)
    passwordVar = Column(String, nullable=False)