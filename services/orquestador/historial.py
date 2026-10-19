"""7.1b / H1 Lectura del historial de UNA conversacion de UN usuario (claude/fase7-1b-adenda-modelo-final.md, seccion 3).

leer_intercambios(db, conversacion_id, usuario_id, antes_de_id) -> [{"pregunta", "respuesta"}] (el mas antiguo primero)
    Solo lectura, una consulta parametrizada sobre postgres-vector. AISLAMIENTO: la consulta une mensajes con
    conversaciones y exige a la vez conversacion_id Y usuario_id, de modo que una conversacion ajena (o de otro usuario)
    devuelve [] aunque se conozca su id. Los dos identificadores los pone el servidor (la conversacion ya resuelta para el
    usuario autenticado), nunca el cliente.
    Un intercambio es un mensaje del usuario en estado 'respondido' seguido de la respuesta del asistente; los mensajes en
    error, cancelados o sin respuesta no cuentan. `antes_de_id` deja fuera el mensaje en curso (y todo lo posterior, para
    que un reintento de un mensaje viejo no vea el futuro). Nunca se leen metadatos ni el CONTEXTO de turnos previos.
El recorte (2 intercambios, <= 400 tokens) y el saneado los hace el ensamblador (ensamblador.py).
"""
from sqlalchemy import text

MAX_CADENA = 8  # intercambios que se leen: bastan para la herencia encadenada (herencia.resolver_cadena)
_LECTURA = 2 * MAX_CADENA + 6  # mensajes leidos (margen para mensajes en error o cancelados)
_SIN_LIMITE_ID = 2**62

_SQL = text("""
    SELECT m.id, m.rol, m.contenido, m.estado
    FROM public.mensajes m
    JOIN public.conversaciones c ON c.id = m.conversacion_id
    WHERE c.id = :conversacion_id AND c.usuario_id = :usuario_id AND m.id < :antes_de_id
    ORDER BY m.id DESC
    LIMIT :limite
""")


def leer_intercambios(db, conversacion_id, usuario_id, antes_de_id=None, maximo: int = MAX_CADENA) -> list[dict]:
    if conversacion_id is None or usuario_id is None:
        return []
    filas = db.execute(_SQL, {
        "conversacion_id": int(conversacion_id), "usuario_id": int(usuario_id),
        "antes_de_id": _SIN_LIMITE_ID if antes_de_id is None else int(antes_de_id), "limite": _LECTURA,
    }).all()
    intercambios: list[dict] = []
    usuario = None
    for _, rol, contenido, estado in reversed(filas):
        if rol == "usuario":
            usuario = contenido if estado == "respondido" and (contenido or "").strip() else None
        elif rol == "asistente" and usuario is not None and (contenido or "").strip():
            intercambios.append({"pregunta": usuario, "respuesta": contenido})
            usuario = None
    return intercambios[-maximo:]
