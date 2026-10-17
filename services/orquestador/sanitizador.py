"""4.7 Sanitizacion del texto de salida (fase4-especificacion-2). Se aplica antes de entregar y de guardar.

Elimina etiquetas HTML y comentarios; elimina imagenes Markdown; convierte enlaces [texto](url) en solo
`texto` (sin allowlist de dominios en 0.4.0); quita los esquemas javascript: y data:; quita caracteres de
control y de direccion bidireccional; limita la longitud (tope de seguridad de 4.000 caracteres). Se suma al
skipHtml del frontend. El dataset no usa Markdown: en condiciones normales esto no cambia nada.
"""
import re
import unicodedata

MAX_SALIDA = 4000
_COMENTARIO = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_ETIQUETA = re.compile(r"</?[A-Za-z!?][^>]*>?")
_IMAGEN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_ENLACE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_ESQUEMA = re.compile(r"\b(?:javascript|vbscript|data)\s*:", re.IGNORECASE)
# marcas de direccion y aislamiento bidireccional (LRM, RLM, ALM, LRE..RLO, LRI..PDI)
_BIDI = dict.fromkeys([0x200E, 0x200F, 0x061C, *range(0x202A, 0x202F), *range(0x2066, 0x206A)])


def sanitizar_texto(texto: str) -> str:
    s = texto or ""
    s = _COMENTARIO.sub("", s)
    s = _ETIQUETA.sub("", s)
    s = _IMAGEN.sub("", s)
    s = _ENLACE.sub(r"\1", s)
    s = _ESQUEMA.sub("", s)
    s = s.translate(_BIDI)
    s = "".join(c for c in s if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cs", "Co"))
    s = s.strip()
    if len(s) > MAX_SALIDA:
        s = s[:MAX_SALIDA].rstrip()
    return s
