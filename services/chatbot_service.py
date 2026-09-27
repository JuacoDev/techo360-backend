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


def agregar_mensaje(db: Session, conversacion_id: int, rol: str, contenido: str) -> Mensaje:
    mensaje = Mensaje(conversacion_id=conversacion_id, rol=rol, contenido=contenido)
    db.add(mensaje)
    db.commit()
    db.refresh(mensaje)
    return mensaje
