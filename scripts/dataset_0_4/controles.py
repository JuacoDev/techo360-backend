"""Controles automaticos por par (5.2, fase5-1 §6). Cada funcion devuelve None si pasa o un MOTIVO (texto corto) si falla;
`generar.py` descarta el par y cuenta el motivo. Los controles de tokens, similitud y diversidad estan en `generar.py`.

 1 prueba dorada            -> dorada()
 2 validador 4.5 / numeros  -> cifras()
 3 B sin «aproximadamente», «estimo», «alrededor», «cerca de» -> palabras_b()
 5 forma: sin emojis, Markdown, LightGBM ni repos sin prefijo `techo360-` -> forma()
 + reglas de fase5-1 §2 derivadas: cita (Municipio (Departamento)) y anio, advertencia solo en proyecciones.
"""
import json
import re

from services.orquestador.contexto import LINEA_SIN_CONTEXTO
from services.orquestador.validador_cifras import _es_anio, extraer_numeros, validar_cifras

from . import lengua as lg

EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF⭐⬆✅❌️‍]")
MARKDOWN = re.compile(r"(\*\*|__|`|^#{1,6}\s|^\s*[-*•]\s|^\s*\d+[.)]\s|\[[^\]]+\]\([^)]+\)|\|.+\|)", re.MULTILINE)
REPO_SIN_PREFIJO = re.compile(r"(?<!techo360-)\b(ai-service|proyeccion-service)\b", re.IGNORECASE)
PALABRAS_B = ("aproximadamente", "estimo", "alrededor", "cerca de")
MAX_PALABRAS = 140


def dorada(datos, c) -> str | None:
    """El user se reconstruye byte a byte desde `entrada` (ida y vuelta por JSON, como queda en el archivo)."""
    entrada = json.loads(json.dumps(c.entrada, ensure_ascii=False))
    if datos.renderizar(entrada)["user"] != c.user:
        return "dorada_user_difiere"
    return None


def _lineas_validador(c) -> list[str]:
    bloque = c.user.split("\n\nPREGUNTA DEL USUARIO\n")[0]
    return [l for l in bloque.split("\n")[1:] if l and l != LINEA_SIN_CONTEXTO]


def cifras(c, b3_con_cifras: bool = False) -> str | None:
    texto, fam = c.respuesta, c.familia
    pregunta = c.entrada["pregunta"]
    lineas = _lineas_validador(c)
    usa_validador = fam in ("A", "D", "E", "F") or (c.subfamilia == "B3" and b3_con_cifras)
    if usa_validador:
        r = validar_cifras(texto, lineas, c.render["items"], pregunta)
        if r["estado"] == "no_validada":
            return "validador_cifra_no_validada"
        if fam == "A" and r["estado"] != "ok":
            return "a_sin_cifras"
        return None
    permitidos = {int(n.valor) for n in extraer_numeros("\n".join(lineas)) if _es_anio(n)}
    permitidos |= {int(n.valor) for n in extraer_numeros(pregunta) if _es_anio(n)}
    for n in extraer_numeros(texto):
        if not (_es_anio(n) and int(n.valor) in permitidos):
            return "numero_no_permitido_en_bc"
    return None


def palabras_b(c) -> str | None:
    if c.familia != "B":
        return None
    bajo = lg.sin_tildes(c.respuesta.lower())
    for p in PALABRAS_B:
        if p in bajo:
            return "palabra_prohibida_b"
    if "aproxim" in bajo:
        return "palabra_prohibida_b"
    return None


def forma(c) -> str | None:
    t = c.respuesta
    if "Techo 360" in t and "Techo 360" not in c.user.split("\n\nPREGUNTA DEL USUARIO\n")[0]:
        return "marca_con_numero"
    if EMOJI.search(t):
        return "emoji"
    if MARKDOWN.search(t):
        return "markdown"
    if "lightgbm" in t.lower():
        return "lightgbm"
    if REPO_SIN_PREFIJO.search(t):
        return "repo_sin_prefijo"
    if "  " in t or "\n" in t or t != t.strip():
        return "espacios_o_saltos"
    if re.search(r"\.\.(?!\.)", t):
        return "doble_punto"
    if len(t.split()) > MAX_PALABRAS:
        return "respuesta_larga"
    return None


def reglas_respuesta(c) -> str | None:
    """Reglas 2 y 3 de fase5-1 §2 sobre el contexto leido: cada municipio citado con su departamento, cada anio presente,
    advertencia y fiabilidad SOLO cuando hay proyeccion."""
    ctx, t, sub = c.ctx, c.respuesta, c.subfamilia
    if c.familia not in ("A", "D", "E", "F") or not ctx.datos:
        return None
    muestra = ctx.datos
    if sub == "A7":
        return None  # solo se cita una muestra; la forma se prueba en las pruebas del generador
    for d in muestra:
        if d.cita not in t:
            return "falta_municipio_con_departamento"
        if str(d.anio) not in t:
            return "falta_anio"
    proy = [d for d in muestra if not d.real]
    bajo = t.lower()
    if proy:
        if not any(p in bajo for p in ("estimaci", "estimad", "proyecc", "proyectad")):
            return "proyeccion_sin_aviso"
        if not any(f"{d.fiabilidad} %" in t for d in proy):
            return "proyeccion_sin_fiabilidad"
    else:
        if any(p in bajo for p in ("estimaci", "estimad", "proyecc", "fiabilidad", "proyectad")):
            return "advertencia_en_dato_real"
    return None


def todos(datos, c, b3_con_cifras: bool = False) -> str | None:
    for f in (lambda: dorada(datos, c), lambda: cifras(c, b3_con_cifras), lambda: palabras_b(c), lambda: forma(c), lambda: reglas_respuesta(c)):
        m = f()
        if m:
            return m
    return None
