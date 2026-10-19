"""4.3 Ensamblador de mensajes (fase4-especificacion-1). Sin historial; sin escrituras.

mensajes = [system, user]; user = "CONTEXTO\\n<lineas>\\n\\nPREGUNTA DEL USUARIO\\n<pregunta saneada>".
Presupuesto de entrada: system (241 tokens medidos) + plantilla + contexto + pregunta <= 1.648 tokens
(num_ctx 2048 - NUM_PREDICT 400); si se excede se recorta el CONTEXTO, nunca el system ni la pregunta.
La salida cumple el esquema PeticionV2 del contrato 2.2 (schemas/chatbot_contrato_v2.py).
"""
import re
import unicodedata

from services.orquestador.contexto import (
    LINEA_SIN_CONTEXTO,
    MAX_ELEMENTOS,
    MAX_TOKENS_CONTEXTO,
    ContextoConstruido,
    compactar_contexto,
    estimar_tokens,
)
from services.orquestador.prompts import VERSION_PROMPT_ACTUAL, cargar_system

MAX_PREGUNTA = 1500
TOKENS_SYSTEM = 241  # medido con el tokenizer de Llama 3.2 (claude/fase2-6)
TOKENS_PLANTILLA = 40  # encabezados de rol, fecha y etiquetas CONTEXTO / PREGUNTA (medido ~35)
PRESUPUESTO_ENTRADA = 2048 - 400
_DELIMITADORES = {"CONTEXTO", "PREGUNTA DEL USUARIO", "DOCUMENTOS"}
_ETIQUETA_ID = re.compile(r"^\s*\[[cd]\d+\]", re.IGNORECASE)  # [cN] datos, [dN] fragmentos del RAG


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
    tope_contexto = MAX_TOKENS_CONTEXTO
    while True:
        items, lineas, omitidos = contexto.seleccionar(MAX_ELEMENTOS, tope_contexto)
        contenido = mensaje_usuario(lineas, saneada)
        total = TOKENS_SYSTEM + TOKENS_PLANTILLA + estimar_tokens(contenido)
        if total <= PRESUPUESTO_ENTRADA or tope_contexto <= 0:
            break
        tope_contexto = max(0, tope_contexto - max(10, total - PRESUPUESTO_ENTRADA))
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
            "rag_fragmentos": list(contexto.rag_usados),
            "tokens_estimados": total,
            "dentro_del_presupuesto": total <= PRESUPUESTO_ENTRADA,
        },
    }
