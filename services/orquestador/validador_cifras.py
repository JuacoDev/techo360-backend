"""4.5 Validador de cifras (fase4-especificacion-2). Determinista, sin modelo, sin escrituras.

validar_cifras(texto, lineas_contexto, items, pregunta) -> {"estado": "ok"|"abstencion", "no_validadas": [...]}

Se extraen los numeros del texto de la respuesta (94,2 % | 94.2 | 4.012 | 4012 | 1,5 millones). No se validan:
años 2000-2042 que esten en el contexto o en la pregunta, numeracion de listas y numeros dentro de palabras o
codigos. Regla: cada numero debe aparecer en las lineas de contexto, con tolerancia de formato; se acepta
redondeo A MENOS decimales que el contexto (94,2 -> 94 o 94,2; no 94,3). Derivados permitidos: diferencia o
suma de dos valores del contexto y variacion porcentual entre dos años de la misma variable-municipio,
redondeados. Lo demas (promedios regionales, estimaciones propias) no se valida.
"""
import re
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass
from itertools import combinations

ESCALAS = {"mil": 1e3, "millon": 1e6, "millones": 1e6, "billon": 1e12, "billones": 1e12}
# numero con miles en punto (4.012 | 1.234.567), decimal en coma o punto, o entero; con signo y escala opcionales
_NUMERO = re.compile(
    r"(?<![\w.,])(?P<signo>[-−–])?(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?![\w])"
    r"(?:\s*%)?(?:\s+(?P<escala>mil|millon|millones|billon|billones)\b)?",
    re.IGNORECASE,
)
_ANIO_MIN, _ANIO_MAX = 2000, 2042
_LISTA = re.compile(r"^\s*(?:\d{1,2}[.)])\s")


@dataclass(frozen=True)
class Numero:
    texto: str
    valor: float  # ya multiplicado por su escala
    decimales: int  # decimales escritos (en la unidad escrita, antes de la escala)
    escala: float = 1.0
    entero_sin_formato: bool = False


def _parsear(cruda: str) -> tuple[float, int]:
    """'4.012' -> (4012, 0); '94,2' -> (94.2, 1); '94.2' -> (94.2, 1); '1.234.567,5' -> (1234567.5, 1)."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", cruda):
        entero, _, dec = cruda.replace(".", "").partition(",")
        return float(entero + ("." + dec if dec else "")), len(dec)
    if "," in cruda:
        entero, dec = cruda.split(",", 1)
        return float(entero + "." + dec), len(dec)
    if "." in cruda:
        entero, dec = cruda.split(".", 1)
        return float(entero + "." + dec), len(dec)
    return float(cruda), 0


def extraer_numeros(texto: str) -> list[Numero]:
    salida = []
    for linea in (texto or "").split("\n"):
        inicio_lista = _LISTA.match(linea)
        for m in _NUMERO.finditer(linea):
            if inicio_lista and m.start("num") < inicio_lista.end():
                continue  # numeracion de lista
            previo = linea[m.start("num") - 1] if m.start("num") > 0 else ""
            if previo in "-_":  # codigo con guion (sp-0.4.0-1, ANIO-2018-x): no es un numero ni un negativo
                trozo = linea[linea.rfind(" ", 0, m.start("num")) + 1:linea.find(" ", m.end("num")) if linea.find(" ", m.end("num")) != -1 else len(linea)]
                if re.search(r"[A-Za-z_]", trozo):
                    continue
            valor, dec = _parsear(m.group("num"))
            if m.group("signo"):
                valor = -valor
            escala = ESCALAS.get((m.group("escala") or "").lower(), 1.0)
            salida.append(Numero(m.group(0).strip(), valor * escala, dec, escala, dec == 0 and escala == 1.0))
    return salida


def _es_anio(n: Numero) -> bool:
    return n.entero_sin_formato and _ANIO_MIN <= n.valor <= _ANIO_MAX


def _coincide(n: Numero, referencia: float) -> bool:
    """n es la referencia redondeada (mitad hacia arriba) a n.decimales, o igual. Un numero con MAS
    decimales que la referencia no coincide (94,25 contra 94,2)."""
    esperado = (Decimal(repr(abs(referencia))) / Decimal(repr(n.escala))).quantize(
        Decimal(1).scaleb(-n.decimales), rounding=ROUND_HALF_UP
    )
    escrito = (Decimal(repr(abs(n.valor))) / Decimal(repr(n.escala))).quantize(
        Decimal(1).scaleb(-n.decimales), rounding=ROUND_HALF_UP
    )
    return esperado == escrito


def _decimales_de(valor: float) -> int:
    s = f"{valor:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _valores_derivados(items: list[dict]) -> list[float]:
    derivados = []
    for a, b in combinations(items, 2):
        va, vb = a.get("valor"), b.get("valor")
        if va is None or vb is None:
            continue
        derivados.append(abs(va - vb))
        derivados.append(va + vb)
        if (a.get("variable"), a.get("codigodivipola")) == (b.get("variable"), b.get("codigodivipola")) \
                and a.get("anio") != b.get("anio") and va:
            antes, despues = sorted((a, b), key=lambda x: x["anio"])
            if antes["valor"]:
                derivados.append(abs((despues["valor"] - antes["valor"]) / antes["valor"] * 100.0))
    return derivados


def validar_cifras(texto: str, lineas_contexto: list[str], items: list[dict] | None = None, pregunta: str = "") -> dict:
    contexto_txt = "\n".join(lineas_contexto)
    numeros_ctx = [n for n in extraer_numeros(contexto_txt)]
    anios_permitidos = {int(n.valor) for n in numeros_ctx if _es_anio(n)} | {int(n.valor) for n in extraer_numeros(pregunta) if _es_anio(n)}
    # los valores del contexto, tal como estan escritos en sus lineas, mas los estructurados (items) si se dan
    referencias = [n.valor for n in numeros_ctx] + [float(i["valor"]) for i in (items or []) if i.get("valor") is not None]
    derivados = _valores_derivados(items or [])

    no_validadas, total = [], 0
    for n in extraer_numeros(texto):
        if _es_anio(n) and int(n.valor) in anios_permitidos:
            continue
        total += 1
        if any(_coincide(n, r) for r in referencias):
            continue
        if any(_coincide(n, d) for d in derivados):
            continue
        no_validadas.append(n.texto)
    if no_validadas:
        return {"estado": "no_validada", "no_validadas": no_validadas}
    return {"estado": "ok" if total else "abstencion", "no_validadas": []}
