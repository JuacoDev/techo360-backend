"""4.8 Persistencia sincrona de los metadatos del camino v2 (columnas de la migracion 0004 en mensajes).

aplicar_metadatos(db, mensaje_id, meta) hace UN UPDATE parametrizado sobre el mensaje del asistente DENTRO de
la transaccion de quien llama (cerrar_respondido / agregar_mensaje): o se guarda todo o nada, y si falla el
usuario recibe un error, no una respuesta sin registro. No hay migraciones nuevas ni columnas mapeadas en el ORM:
los caminos sin orquestador no cambian. Respeta consentimiento_entrenamiento como hoy (no lo toca).
"""
import json

from sqlalchemy import text

CAMPOS_META = (
    "version_contrato", "modelo", "modelo_digest", "version_prompt", "done_reason", "tokens_prompt",
    "tokens_respuesta", "duracion_ms", "contexto_ids", "contexto", "validacion_cifras", "regeneraciones", "grafica",
)
_JSONB = {"contexto", "validacion_cifras", "grafica"}


def payload_grafica(meta: dict | None) -> dict | None:
    """Lo unico de `meta["grafica"]` que se entrega al cliente (7.1): el payload de una grafica valida, con datos."""
    g = (meta or {}).get("grafica")
    return g["payload"] if isinstance(g, dict) and g.get("valida") and g.get("payload") else None


def adjuntar_grafica(mensaje, meta: dict | None) -> None:
    """Deja el payload en el objeto del mensaje del asistente para que MensajeResponse lo serialice (campo no mapeado)."""
    mensaje.grafica = payload_grafica(meta)


def adjuntar_graficas_guardadas(db, mensajes: list) -> None:
    """Listado de una conversacion: lee mensajes.grafica (migracion 0004) de los mensajes del asistente con grafica."""
    ids = [m.id for m in mensajes if m.rol == "asistente"]
    if not ids:
        return
    filas = db.execute(text("SELECT id, grafica FROM mensajes WHERE id = ANY(:ids) AND grafica IS NOT NULL"), {"ids": ids}).all()
    guardadas = {fila[0]: payload_grafica({"grafica": fila[1]}) for fila in filas}
    for m in mensajes:
        m.grafica = guardadas.get(m.id)


def _parametros(meta: dict) -> dict:
    p = {c: meta.get(c) for c in CAMPOS_META}
    p["regeneraciones"] = int(p["regeneraciones"] or 0)
    for c in _JSONB:
        v = p[c]
        # contexto ya viene compacto (texto JSON); las demas son dict. Se guardan como jsonb.
        p[c] = None if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return p


def aplicar_metadatos(db, mensaje_id: int, meta: dict) -> None:
    db.execute(
        text("""
            UPDATE mensajes SET
                version_contrato = :version_contrato, modelo = :modelo, modelo_digest = :modelo_digest,
                version_prompt = :version_prompt, done_reason = :done_reason, tokens_prompt = :tokens_prompt,
                tokens_respuesta = :tokens_respuesta, duracion_ms = :duracion_ms, contexto_ids = :contexto_ids,
                contexto = CAST(:contexto AS jsonb), validacion_cifras = CAST(:validacion_cifras AS jsonb),
                regeneraciones = :regeneraciones, grafica = CAST(:grafica AS jsonb)
            WHERE id = :id AND rol = 'asistente'
        """),
        {**_parametros(meta), "id": mensaje_id},
    )
