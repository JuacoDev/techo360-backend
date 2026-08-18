from sqlalchemy import Column, Integer, String

from db.session import Base


class Usuario(Base):
    __tablename__ = "Usuarios"

    id = Column(Integer, primary_key=True, index=True)

    municipio = Column(String(100), nullable=False)

    nombre = Column(String(150), nullable=False)

    valor = Column(String(150), nullable=False)

    año = Column(Integer, primary_key=True, index=True)

    origen  = Column(String(150), nullable=False)
