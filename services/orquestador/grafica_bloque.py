"""4.6 Extractor del bloque de grafica (fase4-especificacion-2; contrato fase2-3).

procesar_grafica(texto, ...) -> (texto_sin_bloques, resultado | None)
resultado = {"spec": dict | None, "valida": bool, "motivo": str | None}

Se extrae el PRIMER bloque ```grafica (los demas se ignoran), se valida la estructura y la combinacion
(schemas/chatbot_grafica.py + validar_combinacion), que las zonas existan en zonas_municipio y que haya
datos (al menos 2 puntos por serie para `lineas`; usa mapcalordb y completar_puntos). SIEMPRE se quita el bloque del
texto entregado. En 0.4.0 la especificacion NO se entrega al frontend (eso es 7.6): solo se valida y se guarda en
mensajes.grafica. Bloque sin cierre o JSON roto: se descarta con su motivo.
"""
import re

from schemas.chatbot_grafica import ErrorGrafica, extraer_bloque, validar_bloque_json
from services import proyeccion_service as pservicio
from services.orquestador.alias import V_SCORE

_BLOQUE = re.compile(r"```grafica[ \t]*\r?\n.*?(?:\r?\n?```|\Z)", re.DOTALL)
MIN_PUNTOS_LINEAS = 2
MIN_PUNTOS_OTROS = 1


def quitar_bloques(texto: str) -> str:
    """Quita todos los bloques ```grafica (un bloque sin cierre se quita hasta el final del texto)."""
    return _BLOQUE.sub("", texto or "").strip()


async def _puntos_disponibles(variable, zona, anios, leer_serie_fn, completar, score_fn) -> int:
    if variable == V_SCORE:
        disponibles = 0
        for anio in anios:
            r = await score_fn(zona, anio)
            if r["estado"] == "calculado" or r.get("motivo") == "sin_hueco":
                disponibles += 1
        return disponibles
    serie = await leer_serie_fn(zona, variable)
    con_dato = {f["anio"] for f in serie if f["valor"] is not None}
    faltan = [a for a in anios if a not in con_dato]
    disponibles = sum(1 for a in anios if a in con_dato)
    for i in range(0, len(faltan), pservicio.MAX_LOTE_PUNTOS):
        lote = faltan[i:i + pservicio.MAX_LOTE_PUNTOS]
        puntos = await completar([{"indicador": variable, "codigo": zona, "anio": a} for a in lote])
        disponibles += sum(1 for p in puntos if p["estado"] == "calculado")
    return disponibles


async def procesar_grafica(texto: str, codigos_validos: set[str], *, leer_serie_fn, completar, score_fn):
    limpio = quitar_bloques(texto)
    if "```grafica" not in (texto or ""):
        return limpio, None
    try:
        bloque = extraer_bloque(texto)
        spec = validar_bloque_json(bloque)
    except ErrorGrafica as error:
        return limpio, {"spec": None, "valida": False, "motivo": f"{error.causa}: {error.detail}"[:300]}
    spec_dict = spec.model_dump()
    inexistentes = [z for z in spec.zonas if z not in codigos_validos]
    if inexistentes:
        return limpio, {"spec": spec_dict, "valida": False, "motivo": f"zona_inexistente: {len(inexistentes)} codigo(s)"}
    minimo = MIN_PUNTOS_LINEAS if spec.tipo == "lineas" else MIN_PUNTOS_OTROS
    anios = list(spec.anios)
    if anios:
        for variable in spec.variables:
            for zona in spec.zonas:
                n = await _puntos_disponibles(variable, zona, anios, leer_serie_fn, completar, score_fn)
                if n < minimo:
                    return limpio, {"spec": spec_dict, "valida": False,
                                    "motivo": f"datos_insuficientes: {variable} tiene {n} punto(s), se necesitan {minimo}"}
    return limpio, {"spec": spec_dict, "valida": True, "motivo": None}
