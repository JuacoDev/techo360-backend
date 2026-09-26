from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from core.config import settings

# Motor SEPARADO del de db/session.py -- conversaciones/mensajes viven
# en postgres-vector, una base fisica distinta de postgres-principal
# (donde vive mapcalordb/mapcaloruser). Un solo engine/Session no puede
# apuntar a las dos bases a la vez.
engine_vector = create_engine(settings.database_vector_url, pool_pre_ping=True)
SessionLocalVector = sessionmaker(autocommit=False, autoflush=False, bind=engine_vector)
BaseVector = declarative_base()


def get_db_vector():
    db = SessionLocalVector()
    try:
        yield db
    finally:
        db.close()
