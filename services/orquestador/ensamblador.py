"""4.3 Ensamblador de mensajes (fase4-especificacion-1). Sin historial; sin escrituras.

mensajes = [system, user]; user = "CONTEXTO\\n<lineas>\\n\\nPREGUNTA DEL USUARIO\\n<pregunta saneada>".
Presupuesto de entrada (6.7b): system + plantilla + contexto + RAG + pregunta <= OLLAMA_NUM_CTX - OLLAMA_NUM_PREDICT - 64
(3072 - 400 - 64 = 2.608 tokens), estimado con 2,3 caracteres por token TAMBIEN para el system (optimista seria 3,0:
Ollama descarta el system sin error si el prompt no cabe). Si se excede, en este orden: 1) se quitan fragmentos del RAG
(el menos relevante primero), 2) se recortan las lineas de contexto de menor prioridad (las ultimas; las lineas
obligatorias «Sin dato» y de ubicacion no se tocan), 3) si aun asi no cabe (la pregunta sola no cabe) no se arma peticion
valida: notas["pregunta_no_cabe"] = True y el orquestador responde un mensaje fijo SIN llamar al modelo.
Nunca se recorta el system ni la pregunta.
La salida cumple el esquema PeticionV2 del contrato 2.2 (schemas/chatbot_contrato_v2.py).
"""
import math
import re
import unicodedata
from dataclasses import replace

from core.config import settings
from services.orquestador.contexto import (
    CARACTERES_POR_TOKEN,
    LINEA_SIN_CONTEXTO,
    MAX_ELEMENTOS,
    MAX_TOKENS_CONTEXTO,
    ContextoConstruido,
    compactar_contexto,
    estimar_tokens,
)
from services.orquestador.prompts import VERSION_PROMPT_ACTUAL, cargar_system

MAX_PREGUNTA = 1500
TOKENS_PLANTILLA = 40  # encabezados de rol, fecha y etiquetas CONTEXTO / PREGUNTA (medido ~35)
CARACTERES_POR_TOKEN_PROMPT = 2.3  # conservador para TODO el prompt (el contexto de datos conserva 3,0 en contexto.py)
MARGEN_TOKENS = 64
NUM_CTX = settings.ollama_num_ctx
NUM_PREDICT = settings.ollama_num_predict
PRESUPUESTO_ENTRADA = NUM_CTX - NUM_PREDICT - MARGEN_TOKENS
MENSAJE_PREGUNTA_LARGA = (
    "Tu pregunta es demasiado larga para que pueda responderla. ¿Puedes acortarla o dividirla en preguntas más "
    "cortas?"
)
_DELIMITADORES = {"CONTEXTO", "PREGUNTA DEL USUARIO", "DOCUMENTOS"}
_ETIQUETA_ID = re.compile(r"^\s*\[[cd]\d+\]", re.IGNORECASE)  # [cN] datos, [dN] fragmentos del RAG


def estimar_tokens_prompt(texto: str) -> int:
    return math.ceil(len(texto) / CARACTERES_POR_TOKEN_PROMPT) + 1


def sanear_pregunta(texto: str) -> tuple[str, bool]:
    """-> (pregunta saneada, fue_cortada). Quita caracteres de control, colapsa espacios, neutraliza las
    lineas que imitan los delimitadores del mensaje y corta a MAX_PREGUNTA caracteres."""
    limpio = "".join(ch for ch in (texto or "") if ch in "\n\t" or not unicodedata.category(ch).startswith("C"))
    lineas = [re.sub(r"[ \t]+", " ", linea).strip() for linea in limpio.split("\n")]
    salida: list[str] = []
    for linea in lineas:
        if not linea and (not salida or not salida[-1]):
            continue  # sin lineas en blanco al inicio ni repetidas
        if linea.rstrip(":").strip().upper() in _DELIMITADORES or _ETIQUETA_ID.match(linea):
            linea = "» " + linea
        salida.append(linea)
    pregunta = "\n".join(salida).strip()
    cortada = len(pregunta) > MAX_PREGUNTA
    return (pregunta[:MAX_PREGUNTA].rstrip() if cortada else pregunta), cortada


def mensaje_usuario(lineas: list[str], pregunta: str) -> str:
    return "CONTEXTO\n" + "\n".join(lineas or [LINEA_SIN_CONTEXTO]) + "\n\nPREGUNTA DEL USUARIO\n" + pregunta


def ensamblar(pregunta: str, contexto: ContextoConstruido, metadatos: dict, system: str | None = None) -> dict:
    """-> {"peticion": cuerpo PeticionV2, "contexto_compacto": str, "contexto_ids": [...], "notas": {...}}.

    `metadatos`: usuario_id y consentimiento_entrenamiento (obligatorios) mas conversacion_id, mensaje_id e
    intento si se conocen. Se anade version_prompt."""
    system = cargar_system() if system is None else system
    saneada, cortada = sanear_pregunta(pregunta)
    base = TOKENS_PLANTILLA + estimar_tokens_prompt(system)

    def armar(ctx, tope):
        items, lineas, omitidos = ctx.seleccionar(MAX_ELEMENTOS, tope)
        contenido = mensaje_usuario(lineas, saneada)
        return items, omitidos, contenido, base + estimar_tokens_prompt(contenido)

    # 1) si no cabe, fuera fragmentos del RAG (el menos relevante primero)
    ctx, tope_contexto = contexto, MAX_TOKENS_CONTEXTO
    items, omitidos, contenido, total = armar(ctx, tope_contexto)
    quitar = len(ctx.rag_usados)
    while total > PRESUPUESTO_ENTRADA and quitar > 0:
        quitar -= 1
        ctx = replace(contexto, fragmentos_rag=contexto.fragmentos_rag[:quitar], rag_usados=[])
        items, omitidos, contenido, total = armar(ctx, tope_contexto)
    # 2) luego las lineas de contexto de menor prioridad (las ultimas); el tope se expresa en tokens de contexto (3,0)
    while total > PRESUPUESTO_ENTRADA and tope_contexto > 0:
        exceso = (total - PRESUPUESTO_ENTRADA) * CARACTERES_POR_TOKEN_PROMPT / CARACTERES_POR_TOKEN
        tope_contexto = max(0, tope_contexto - max(10, math.ceil(exceso)))
        items, omitidos, contenido, total = armar(ctx, tope_contexto)
    pregunta_no_cabe = total > PRESUPUESTO_ENTRADA  # 3) ni sin contexto opcional: la pregunta sola no cabe
    peticion = {
        "version_contrato": 2,
        "mensajes": [{"rol": "system", "contenido": system}, {"rol": "user", "contenido": contenido}],
        "contexto": items,
        "metadatos": {**metadatos, "version_prompt": VERSION_PROMPT_ACTUAL},
    }
    return {
        "peticion": peticion,
        "contexto_compacto": compactar_contexto(items),
        "contexto_ids": [i["id"] for i in items],
        "notas": {
            "pregunta_cortada": cortada,
            "omitidos": omitidos,
            "rag_fragmentos": list(ctx.rag_usados),
            "tokens_estimados": total,
            "dentro_del_presupuesto": not pregunta_no_cabe,
            "pregunta_no_cabe": pregunta_no_cabe,
        },
    }
