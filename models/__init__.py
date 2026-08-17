# Los modelos de SQLAlchemy (tablas) van aqui.
# IMPORTANTE: el esquema definitivo depende del spike de fuentes de datos
# de la primera HU del Sprint 1 -- no crear modelos aqui hasta confirmar
# la granularidad geografica real disponible (comuna/barrio vs municipio).
#
# Convencion a seguir cuando se agregue el primero:
#
# from db.session import Base
# from sqlalchemy import Column, Integer, String
#from models.zona import Zona
#
# class Vivienda(Base):
#     __tablename__ = "viviendas"
#     id = Column(Integer, primary_key=True)
