from uuid import UUID

from sqlalchemy.orm import Session

from models.conversacion import Conversacion
from models.mensaje import Mensaje

# Capa compartida entre el router HTTP (routers/chatbot.py, T-98/T-101) y
# el consumer de RabbitMQ (services/chatbot_persistencia_consumer.py,
# evento "intercambio_completo" publicado por techo360-chatbot) -- ambos
# caminos deben terminar en las mismas dos funciones para que no puedan
# divergir en como se escribe conversaciones/mensajes.


def crear_conversacion(db: Session, usuario_id: int, consentimiento_entrenamiento: bool) -> Conversacion:
    conversacion = Conversacion(
        usuario_id=usuario_id,
        consentimiento_entrenamiento=consentimiento_entrenamiento,
    )
    db.add(conversacion)
    db.commit()
    db.refresh(conversacion)
    return conversacion


def interpretar_identificador(valor) -> tuple[str, object] | None:
    """Identificador de conversacion recibido en la ruta: ("public_id", UUID) si es un
    UUID valido, ("id", int) si son solo digitos (compatibilidad mientras dure la
    transicion), None en cualquier otro caso."""
    texto = str(valor).strip()
    if texto.isdigit() and len(texto) <= 18:
        return ("id", int(texto))
    try:
        return ("public_id", UUID(texto))
    except ValueError:
        return None


def resolver_conversacion(db: Session, identificador, usuario_id: int) -> Conversacion | None:
    """La conversacion de usuario_id que corresponde a `identificador` (public_id o id
    entero), o None si no existe, es ajena o el identificador no es valido.
    SEGURIDAD: SIEMPRE filtra por usuario_id."""
    interpretado = interpretar_identificador(identificador)
    if interpretado is None:
        return None
    campo, valor = interpretado
    columna = Conversacion.public_id if campo == "public_id" else Conversacion.id
    return db.query(Conversacion).filter(columna == valor, Conversacion.usuario_id == usuario_id).first()


def conversacion_de_usuario(db: Session, conversacion_id: int, usuario_id: int) -> Conversacion | None:
    """La conversacion SOLO si existe y es de usuario_id (None si no existe o es ajena).

    SEGURIDAD: punto unico de verificacion de propiedad para los caminos que no
    pasan por get_current_user (el consumer de RabbitMQ)."""
    return (
        db.query(Conversacion)
        .filter(Conversacion.id == conversacion_id, Conversacion.usuario_id == usuario_id)
        .first()
    )


def agregar_mensaje(db: Session, conversacion_id: int, rol: str, contenido: str, metadatos: dict | None = None) -> Mensaje:
    """`metadatos` (solo el camino v2 del orquestador, 4.8): se guardan en la misma transaccion; None = como siempre."""
    mensaje = Mensaje(conversacion_id=conversacion_id, rol=rol, contenido=contenido)
    db.add(mensaje)
    if metadatos is not None:
        from services.orquestador.persistencia import aplicar_metadatos

        db.flush()
        try:
            aplicar_metadatos(db, mensaje.id, metadatos)
        except Exception:
            db.rollback()
            raise
    db.commit()
    db.refresh(mensaje)
    return mensaje
