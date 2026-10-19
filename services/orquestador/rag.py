"""6.6 RAG curado en el constructor de contexto. Solo lectura de rag_fragmentos (migracion 0005, postgres-vector).

Recuperador elegido en 6.5 (claude/fase6-5-6-6-rag.md): busqueda de TEXTO de Postgres en español, calculada en la
consulta (to_tsvector('spanish', ...)), sin migracion nueva, sin embeddings y sin servicio adicional (el backend
tiene 512 MB y no cabe torch + e5). La pregunta se convierte en una consulta OR de sus lexemas; tema pesa A y texto B.
Se ordena con ts_rank (no ts_rank_cd): en las 40 conceptuales del 6.4 da recall@5 95 %, @2 87,5 % y @1 77,5 %, frente a
90 %, 77,5 % y 62,5 % con ts_rank_cd normalizado (variante elegida DESPUES de ver ambas sobre las mismas 40: sesgo optimista).

Reglas:
  * Solo se consulta con intencion conceptual (intencion.intencion_conceptual): los datos numericos nunca van por RAG.
  * Maximo MAX_FRAGMENTOS fragmentos y MAX_TOKENS_RAG tokens (estimar_tokens del contexto), con linea propia
    `[dN] <tema>: <texto>. Fuente: <fuente>, <fecha>.` (la «d» evita chocar con los ids [cN] de los datos).
    Un fragmento largo se corta en el ultimo fin de oracion que quepa; si ni una oracion cabe, se omite.
  * Tras CHATBOT_RAG_ACTIVO (apagado por defecto). Cualquier error (tablas ausentes, base caida, timeout) se
    degrada en silencio: el flujo sigue sin RAG y el log no lleva texto del usuario.
"""
import asyncio
import logging
import re

from sqlalchemy import text

from services.orquestador.contexto import estimar_tokens

logger = logging.getLogger(__name__)

MAX_FRAGMENTOS = 2
MAX_TOKENS_RAG = 350
MIN_CARACTERES_CORTE = 120  # un fragmento cortado debe conservar al menos esto para valer la pena
SQL_TIMEOUT = text("SET LOCAL statement_timeout = '2000ms'")  # constante: la guarda de la prueba 9 prohibe f-strings en SQL
CANDIDATOS = 5  # top-k que se pide a la base; luego se recorta a MAX_FRAGMENTOS por presupuesto

SQL_BUSCAR = text("""
WITH q AS (
    SELECT to_tsquery('spanish', string_agg(quote_literal(lexeme), ' | ')) AS tsq
    FROM unnest(to_tsvector('spanish', translate(:pregunta, 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')))
), docs AS (
    SELECT f.fragmento_id, f.tema, f.texto, f.fuente, f.fecha_fuente,
           setweight(to_tsvector('spanish', translate(f.tema, 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')), 'A') ||
           setweight(to_tsvector('spanish', translate(f.texto, 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')), 'B') AS v
    FROM rag_fragmentos f JOIN rag_documentos d ON d.id = f.documento_id
    WHERE f.activo AND d.activo
)
SELECT docs.fragmento_id, docs.tema, docs.texto, docs.fuente, docs.fecha_fuente
FROM docs, q WHERE docs.v @@ q.tsq
ORDER BY ts_rank(docs.v, q.tsq) DESC, docs.fragmento_id
LIMIT :k
""")


def buscar_fragmentos(sesion, pregunta: str, k: int = CANDIDATOS) -> list[dict]:
    """-> [{fragmento_id, tema, texto, fuente, fecha}] por relevancia. Sincrona; la sesion es de postgres-vector."""
    sesion.execute(SQL_TIMEOUT)
    filas = sesion.execute(SQL_BUSCAR, {"pregunta": pregunta, "k": k}).mappings().all()
    return [{"fragmento_id": f["fragmento_id"], "tema": f["tema"], "texto": f["texto"], "fuente": f["fuente"],
             "fecha": f["fecha_fuente"].isoformat() if f["fecha_fuente"] else ""} for f in filas]


def _recuperar_con_sesion_propia(pregunta: str) -> list[dict]:
    from db.session_vector import SessionLocalVector
    sesion = SessionLocalVector()
    try:
        sesion.execute(text("SET TRANSACTION READ ONLY"))
        return buscar_fragmentos(sesion, pregunta)
    finally:
        sesion.rollback()
        sesion.close()


async def recuperar(pregunta: str, buscar_fn=None) -> list[dict]:
    """Fragmentos candidatos para una pregunta conceptual; [] si algo falla (degradacion silenciosa)."""
    buscar_fn = buscar_fn or _recuperar_con_sesion_propia
    try:
        return await asyncio.to_thread(buscar_fn, pregunta)
    except Exception as exc:  # noqa: BLE001 -- el RAG nunca debe romper la respuesta
        logger.warning("RAG no disponible (%s); la respuesta sigue sin fragmentos.", type(exc).__name__)
        return []


# ---------------------------------------------------------------------------- formato de linea
def _una_linea(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _sin_punto_final(t: str) -> str:
    return t.rstrip().rstrip(".").rstrip()


def texto_linea(numero: int, f: dict, cuerpo: str | None = None) -> str:
    """`[dN] <tema>: <texto>. Fuente: <fuente>, <fecha>.` en una sola linea."""
    cuerpo = _sin_punto_final(_una_linea(f["texto"] if cuerpo is None else cuerpo))
    fecha = f", {f['fecha']}" if f.get("fecha") else ""
    return f"[d{numero}] {_una_linea(f['tema'])}: {cuerpo}. Fuente: {_una_linea(f['fuente'])}{fecha}."


def _corte_por_oracion(f: dict, numero: int, presupuesto: int) -> str | None:
    """La linea mas larga que cabe en `presupuesto` cortando el texto en un fin de oracion; None si no hay."""
    cuerpo = _una_linea(f["texto"])
    cortes = [m.end() for m in re.finditer(r"[.!?;:](?=\s|$)", cuerpo)]
    for fin in reversed(cortes):
        if fin < MIN_CARACTERES_CORTE:
            break
        linea = texto_linea(numero, f, cuerpo[:fin])
        if estimar_tokens(linea) <= presupuesto:
            return linea
    return None


def seleccionar_lineas(fragmentos: list[dict], presupuesto: int, max_fragmentos: int = MAX_FRAGMENTOS) -> list[str]:
    """Lineas [dN] por orden de relevancia, como maximo `max_fragmentos` y `presupuesto` tokens en total
    (prefijo por prioridad: si el segundo no cabe, no se salta a un tercero)."""
    presupuesto = min(presupuesto, MAX_TOKENS_RAG)
    lineas: list[str] = []
    for f in fragmentos:
        if len(lineas) >= max_fragmentos or presupuesto <= 0:
            break
        n = len(lineas) + 1
        linea = texto_linea(n, f)
        if estimar_tokens(linea) > presupuesto:
            linea = _corte_por_oracion(f, n, presupuesto)
            if linea is None:
                break
        lineas.append(linea)
        presupuesto -= estimar_tokens(linea)
    return lineas
