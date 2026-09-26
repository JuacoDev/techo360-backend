from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.session_vector import get_db_vector
from models.conversacion import Conversacion
from models.mapcalor_user import MapCalorUser
from routers.auth import get_current_user
from schemas.chatbot import ConversacionResponse, CrearConversacionRequest

router = APIRouter(
    prefix="/chatbot",
    tags=["chatbot"],
)


@router.post(
    "/conversaciones",
    response_model=ConversacionResponse,
    status_code=201,
    summary="Crear una conversacion nueva",
    description=(
        "Crea una fila en conversaciones (postgres-vector) para el "
        "usuario autenticado. consentimiento_entrenamiento se guarda "
        "como snapshot al momento de crear la conversacion -- si el "
        "usuario cambia su consentimiento despues, esta fila conserva "
        "el valor que tenia en este momento (ver AVISO en la migracion, "
        "techo360-chatbot/migrations/0001_conversaciones_mensajes.sql)."
    ),
    responses={401: {"description": "Requiere sesión iniciada."}},
)
def crear_conversacion(
    datos: CrearConversacionRequest,
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    conversacion = Conversacion(
        usuario_id=current_user.id,
        consentimiento_entrenamiento=datos.consentimiento_entrenamiento,
    )
    db.add(conversacion)
    db.commit()
    db.refresh(conversacion)
    return conversacion


@router.get(
    "/conversaciones",
    response_model=list[ConversacionResponse],
    summary="Listar las conversaciones del usuario autenticado",
    description=(
        "Devuelve solo las conversaciones del usuario autenticado -- "
        "es historial personal, no una vista de administrador (no hay "
        "sistema de roles todavia, ver AVISO en routers/zonas.py). "
        "Ordenadas por fecha descendente (la mas reciente primero)."
    ),
    responses={401: {"description": "Requiere sesión iniciada."}},
)
def listar_conversaciones(
    db: Session = Depends(get_db_vector),
    current_user: MapCalorUser = Depends(get_current_user),
):
    return (
        db.query(Conversacion)
        .filter(Conversacion.usuario_id == current_user.id)
        .order_by(Conversacion.fecha.desc())
        .all()
    )
