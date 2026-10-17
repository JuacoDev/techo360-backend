"""Estados de los mensajes del chatbot (Fase C).

Cada mensaje de USUARIO tiene un estado (columna mensajes.estado, ver la
migracion 0002 de techo360-chatbot):

    pendiente   guardado, aun no enviado al asistente (instante)
    procesando  esperando la respuesta del asistente (RPC, hasta ~200 s)
    error       el asistente fallo (timeout, no disponible...) o no hubo respuesta
    cancelado   el usuario dejo de esperar
    respondido  tiene respuesta guardada (y los mensajes del ASISTENTE, siempre)

'pendiente', 'procesando', 'error' y 'cancelado' estan "sin resolver": hay a lo
sumo UNO por conversacion (indice unico parcial en la BD, a prueba de carreras).
Solo 'respondido' o ELIMINAR el mensaje desbloquean la conversacion.

Todas las transiciones son actualizaciones condicionales (compare-and-set,
`UPDATE ... WHERE estado IN (...) RETURNING`): dos reintentos simultaneos, o una
cancelacion que se cruza con la llegada de la respuesta, no se pisan -- gana uno
y el otro no actualiza nada.
"""
import logging

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.conversacion import Conversacion
from models.mensaje import Mensaje

logger = logging.getLogger(__name__)

PENDIENTE = "pendiente"
PROCESANDO = "procesando"
ERROR = "error"
CANCELADO = "cancelado"
RESPONDIDO = "respondido"

ESTADOS = (PENDIENTE, PROCESANDO, ERROR, CANCELADO, RESPONDIDO)
SIN_RESOLVER = (PENDIENTE, PROCESANDO, ERROR, CANCELADO)

# Los que de verdad esperan al asistente (error/cancelado ya no ocupan lugar en la cola).
EN_PROCESO = (PENDIENTE, PROCESANDO)

# Nombre del indice unico parcial de la migracion 0002: es el UNICO IntegrityError que
# significa "la conversacion ya tiene un mensaje sin resolver".
INDICE_UNICO_SIN_RESOLVER = "uq_mensajes_un_sin_resolver_por_conversacion"

# error_detalle viene del detalle que devuelve el worker: se acota (la BD admite 1000).
MAX_DETALLE = 500

# El RPC al asistente tiene tope de 200 s (services/chatbot_rpc_client.py): un
# 'procesando'/'pendiente' de mas de 4 min ya no tiene a nadie esperando.
MINUTOS_COLGADO = 4

DETALLE_TIMEOUT = "El asistente no respondió a tiempo."
DETALLE_COLGADO = "Sin respuesta del asistente (tiempo agotado)."
DETALLE_REINICIO = "Interrumpido por un reinicio del servidor."


class ConversacionBloqueada(Exception):
    """La conversacion ya tiene un mensaje de usuario sin resolver."""

    def __init__(self, mensaje_pendiente: Mensaje | None):
        super().__init__("La conversación ya tiene un mensaje sin resolver")
        self.mensaje_pendiente = mensaje_pendiente


class CapacidadAgotada(Exception):
    """No se admite otro mensaje: el usuario ya tiene demasiados en proceso (motivo='usuario')
    o la cola global del asistente esta llena (motivo='cola'). `limite` es el tope superado."""

    def __init__(self, motivo: str, limite: int):
        super().__init__(f"Capacidad agotada ({motivo}, limite {limite})")
        self.motivo = motivo
        self.limite = limite


def _de_usuario(usuario_id: int):
    """Condicion: el mensaje pertenece a una conversacion del usuario."""
    return Mensaje.conversacion_id.in_(select(Conversacion.id).where(Conversacion.usuario_id == usuario_id))


def verificar_capacidad(db: Session, usuario_id: int, max_por_usuario: int, max_cola: int) -> None:
    """Lanza CapacidadAgotada si el usuario ya tiene `max_por_usuario` mensajes en proceso
    ('usuario') o si en todo el sistema hay `max_cola` ('cola'). 0 desactiva el tope. Se llama
    DESPUES de barrer_colgados (un colgado no debe contar). Es un limite blando: dos envios
    simultaneos del mismo usuario pueden pasar a la vez por encima del tope en una unidad."""
    base = select(func.count()).select_from(Mensaje).where(Mensaje.rol == "usuario", Mensaje.estado.in_(EN_PROCESO))
    if max_por_usuario > 0 and (db.scalar(base.where(_de_usuario(usuario_id))) or 0) >= max_por_usuario:
        raise CapacidadAgotada("usuario", max_por_usuario)
    if max_cola > 0 and (db.scalar(base) or 0) >= max_cola:
        raise CapacidadAgotada("cola", max_cola)


def mensaje_sin_resolver(db: Session, conversacion_id: int) -> Mensaje | None:
    return db.scalars(
        select(Mensaje)
        .where(Mensaje.conversacion_id == conversacion_id, Mensaje.rol == "usuario", Mensaje.estado.in_(SIN_RESOLVER))
        .order_by(Mensaje.id)
        .limit(1)
    ).first()


def obtener_de_usuario(db: Session, mensaje_id: int, usuario_id: int) -> Mensaje | None:
    """El mensaje, solo si es de una conversacion del usuario (si no, None: 404)."""
    return db.scalars(select(Mensaje).where(Mensaje.id == mensaje_id, _de_usuario(usuario_id))).first()


def crear_pendiente(db: Session, conversacion_id: int, contenido: str) -> Mensaje:
    """Guarda el mensaje del usuario como 'pendiente'. Si la conversacion ya tiene
    uno sin resolver lanza ConversacionBloqueada (409): primero se mira, y el
    indice unico parcial de la BD cubre la carrera de dos envios simultaneos."""
    existente = mensaje_sin_resolver(db, conversacion_id)
    if existente is not None:
        raise ConversacionBloqueada(existente)
    mensaje = Mensaje(conversacion_id=conversacion_id, rol="usuario", contenido=contenido, estado=PENDIENTE)
    db.add(mensaje)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        nombre = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        if nombre != INDICE_UNICO_SIN_RESOLVER:
            raise  # otro error de integridad (p. ej. la conversacion ya no existe): NO es un 409
        raise ConversacionBloqueada(mensaje_sin_resolver(db, conversacion_id)) from error
    db.refresh(mensaje)
    return mensaje


def _cambiar(
    db: Session,
    mensaje_id: int,
    desde: tuple,
    hacia: str,
    usuario_id: int | None = None,
    detalle: str | None = None,
    nuevo_intento: bool = False,
    intento: int | None = None,
) -> Mensaje | None:
    """UPDATE condicional: solo si el estado actual esta en `desde` (y, si se da,
    `intento` es el intento vigente). Devuelve el mensaje actualizado, o None si ya no
    estaba en ninguno de esos estados / ya hay otro intento."""
    valores = {"estado": hacia, "actualizado_en": func.now(), "error_detalle": detalle[:MAX_DETALLE] if detalle else detalle}
    if nuevo_intento:
        valores["intentos"] = Mensaje.intentos + 1  # el intento nuevo invalida a los RPC anteriores
    condiciones = [Mensaje.id == mensaje_id, Mensaje.rol == "usuario", Mensaje.estado.in_(desde)]
    if intento is not None:
        condiciones.append(Mensaje.intentos == intento)
    if usuario_id is not None:
        condiciones.append(_de_usuario(usuario_id))
    mensaje = db.scalars(update(Mensaje).where(*condiciones).values(**valores).returning(Mensaje)).first()
    db.commit()
    if mensaje is not None:
        db.refresh(mensaje)
    return mensaje


def marcar_procesando(db: Session, mensaje_id: int) -> Mensaje | None:
    """pendiente -> procesando (primer intento: intentos pasa a 1)."""
    return _cambiar(db, mensaje_id, (PENDIENTE,), PROCESANDO, nuevo_intento=True)


def reintentar(db: Session, mensaje_id: int, usuario_id: int) -> Mensaje | None:
    """error|cancelado -> procesando (atomico: dos reintentos a la vez, gana uno)."""
    return _cambiar(db, mensaje_id, (ERROR, CANCELADO), PROCESANDO, usuario_id=usuario_id, nuevo_intento=True)


def cancelar(db: Session, mensaje_id: int, usuario_id: int) -> Mensaje | None:
    """pendiente|procesando|error -> cancelado. Si estaba procesando, el RPC sigue
    en vuelo: cuando llegue la respuesta, cerrar_respondido no encontrara el
    mensaje en 'procesando' y la descartara (el worker no se puede abortar)."""
    return _cambiar(db, mensaje_id, (PENDIENTE, PROCESANDO, ERROR), CANCELADO, usuario_id=usuario_id)


def marcar_error(db: Session, mensaje_id: int, detalle: str, intento: int | None = None) -> Mensaje | None:
    """pendiente|procesando -> error. No pisa un 'cancelado' (el usuario ya decidio) ni
    a un intento posterior: con `intento`, solo actua si ese sigue siendo el vigente."""
    return _cambiar(db, mensaje_id, (PENDIENTE, PROCESANDO), ERROR, detalle=detalle, intento=intento)


def cerrar_respondido(
    db: Session, mensaje_id: int, contenido_respuesta: str, intento: int | None = None, metadatos: dict | None = None
) -> tuple[Mensaje, Mensaje] | None:
    """procesando -> respondido + guarda la respuesta del asistente, en UNA
    transaccion. Devuelve (mensaje_usuario, mensaje_asistente), o None si el
    mensaje ya no estaba 'procesando' (cancelado/eliminado/barrido mientras el
    asistente pensaba) o si ya hay un intento posterior (`intento` no es el vigente):
    en ese caso NO se guarda la respuesta.

    `metadatos` (solo el camino v2 del orquestador, 4.8): se guardan en el mensaje del asistente en la MISMA
    transaccion; con None (por defecto) el comportamiento es el de siempre."""
    condiciones = [Mensaje.id == mensaje_id, Mensaje.rol == "usuario", Mensaje.estado == PROCESANDO]
    if intento is not None:
        condiciones.append(Mensaje.intentos == intento)
    try:
        usuario = db.scalars(
            update(Mensaje)
            .where(*condiciones)
            .values(estado=RESPONDIDO, error_detalle=None, actualizado_en=func.now())
            .returning(Mensaje)
        ).first()
        if usuario is None:
            db.rollback()
            return None
        asistente = Mensaje(conversacion_id=usuario.conversacion_id, rol="asistente", contenido=contenido_respuesta, estado=RESPONDIDO)
        db.add(asistente)
        if metadatos is not None:
            from services.orquestador.persistencia import aplicar_metadatos

            db.flush()
            aplicar_metadatos(db, asistente.id, metadatos)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(usuario)
    db.refresh(asistente)
    return usuario, asistente


def eliminar(db: Session, mensaje_id: int, usuario_id: int) -> str:
    """Borra un mensaje de usuario sin resolver. Devuelve 'eliminado', 'no_encontrado'
    o 'en_proceso' (esta 'procesando': hay que cancelarlo primero)."""
    borrado = db.scalars(
        delete(Mensaje)
        .where(Mensaje.id == mensaje_id, Mensaje.rol == "usuario", Mensaje.estado.in_((PENDIENTE, ERROR, CANCELADO)), _de_usuario(usuario_id))
        .returning(Mensaje.id)
    ).first()
    db.commit()
    if borrado is not None:
        return "eliminado"
    existente = obtener_de_usuario(db, mensaje_id, usuario_id)
    if existente is not None and existente.rol == "usuario" and existente.estado == PROCESANDO:
        return "en_proceso"
    return "no_encontrado"


def barrer_colgados(db: Session, minutos: int = MINUTOS_COLGADO) -> int:
    """pendiente|procesando con mas de `minutos` sin cambios -> error. Se llama de
    forma perezosa (al listar mensajes y al enviar): no hace falta un job."""
    filas = db.execute(
        update(Mensaje)
        .where(
            Mensaje.rol == "usuario",
            Mensaje.estado.in_((PENDIENTE, PROCESANDO)),
            Mensaje.actualizado_en < func.now() - text(f"interval '{int(minutos)} minutes'"),
        )
        .values(estado=ERROR, error_detalle=DETALLE_COLGADO, actualizado_en=func.now())
    )
    db.commit()
    return filas.rowcount or 0


def barrer_al_arrancar(db: Session) -> int:
    """Al arrancar el backend (un solo worker) ningun RPC sigue en vuelo: todo
    'pendiente'/'procesando' quedo huerfano por el reinicio -> error.

    SUPUESTO: UN solo contenedor/worker del backend. `docker compose up -d` detiene el
    contenedor viejo ANTES de crear el nuevo, asi que el barrido no se cruza con un RPC
    en vuelo. Con 2+ replicas el arranque de una romperia el RPC en vuelo de la otra
    (su respuesta valida se descartaria): habria que acotar por antiguedad
    (actualizado_en < now() - 4 min) o quitar este barrido y dejar el perezoso."""
    filas = db.execute(
        update(Mensaje)
        .where(Mensaje.rol == "usuario", Mensaje.estado.in_((PENDIENTE, PROCESANDO)))
        .values(estado=ERROR, error_detalle=DETALLE_REINICIO, actualizado_en=func.now())
    )
    db.commit()
    return filas.rowcount or 0
