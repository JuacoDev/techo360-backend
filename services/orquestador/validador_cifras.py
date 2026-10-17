"""4.5 Validador de cifras (fase4-especificacion-2, con las correcciones de la auditoria 4.10).
Determinista, sin modelo, sin escrituras.

validar_cifras(texto, lineas_contexto, items, pregunta) -> {"estado": "ok"|"abstencion"|"no_validada", "no_validadas": [...]}

Se extraen los numeros del texto de la respuesta (94,2 % | 94.2 | 4.012 | 4012 | 1,5 millones). No se validan:
años 2000-2042 que esten en el contexto o en la pregunta, numeracion de listas y numeros dentro de palabras o
codigos. Regla: cada numero debe aparecer en las lineas de contexto, con tolerancia de formato; se acepta
redondeo A MENOS decimales que el contexto (94,2 -> 94 o 94,2; no 94,3).

Derivados permitidos (estrechos a proposito: una regla amplia deja pasar cifras inventadas):
  * DIFERENCIA entre dos valores de la MISMA variable (nunca suma, nunca variables distintas), y solo si la
    oracion trae una palabra de comparacion (subio, bajo, diferencia, mas que, frente a, brecha...).
  * VARIACION PORCENTUAL entre dos años de la misma variable-municipio, con la misma condicion.
  * "N años" cuando N es la diferencia entre dos años presentes en el contexto; y conteos reales
    "N municipios / N años / N variables" iguales a los que hay en el contexto.
Lo demas (promedios regionales, estimaciones propias, sumas) no se valida: se marca como no validada.

LIMITE CONOCIDO (aceptado, no cambiar): un numero coincide con CUALQUIER valor del contexto aunque corresponda a otro
municipio, variable o año (el validador comprueba que la cifra exista en el contexto, no que este bien atribuida).
"""
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
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
_PALABRA_SIGUIENTE = re.compile(r"\s*([A-Za-zÁÉÍÓÚÜáéíóúüñÑ]+)")
_FIN_ORACION = re.compile(r"(?<=[.!?;])\s+|\n")

# Palabras que anuncian una comparacion (normalizadas: minusculas, sin tildes). Una diferencia o una variacion
# derivada solo se acepta en una oracion que contenga alguna.
PALABRAS_COMPARACION = frozenset({
    "subio", "subieron", "bajo", "bajaron", "aumento", "aumentaron", "disminuyo", "disminuyeron", "crecio", "crecieron",
    "cayo", "cayeron", "redujo", "redujeron", "incremento", "incrementaron", "diferencia", "diferencias", "variacion",
    "cambio", "cambiaron", "brecha", "mas", "menos", "mayor", "menor", "frente", "respecto", "comparado", "comparada",
    "versus", "supera", "superior", "inferior", "mejoro", "empeoro", "avanzo", "retrocedio",
})
PALABRAS_CONTEO = {"municipio": "municipios", "municipios": "municipios", "variable": "variables", "variables": "variables",
                   "ano": "anios", "anos": "anios"}


def _sin_tildes(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


@dataclass(frozen=True)
class Numero:
    texto: str
    valor: float  # ya multiplicado por su escala
    decimales: int  # decimales escritos (en la unidad escrita, antes de la escala)
    escala: float = 1.0
    entero_sin_formato: bool = False
    siguiente: str = ""  # palabra que sigue al numero (normalizada), p. ej. "anos", "municipios"
    oracion: str = ""  # oracion donde aparece (normalizada)


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


def _oracion_de(linea: str, posicion: int) -> str:
    inicio = 0
    for corte in _FIN_ORACION.finditer(linea):
        if corte.end() <= posicion:
            inicio = corte.end()
        else:
            return linea[inicio:corte.start()]
    return linea[inicio:]


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
            sig = _PALABRA_SIGUIENTE.match(linea, m.end())
            siguiente = _sin_tildes(sig.group(1)) if sig and escala == 1.0 else ""
            salida.append(Numero(m.group(0).strip(), valor * escala, dec, escala, dec == 0 and escala == 1.0,
                                 siguiente, _sin_tildes(_oracion_de(linea, m.start()))))
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


def _tiene_comparacion(oracion: str) -> bool:
    return any(p in PALABRAS_COMPARACION for p in re.findall(r"[a-z]+", oracion))


def valores_derivados(items: list[dict]) -> list[float]:
    """Diferencias entre valores de la MISMA variable y variaciones porcentuales entre años de la misma
    variable-municipio. Sin sumas ni mezcla de variables."""
    derivados = []
    for a, b in combinations(items, 2):
        va, vb = a.get("valor"), b.get("valor")
        if va is None or vb is None or a.get("variable") != b.get("variable"):
            continue
        derivados.append(abs(va - vb))
        if a.get("codigodivipola") == b.get("codigodivipola") and a.get("anio") != b.get("anio"):
            antes, despues = sorted((a, b), key=lambda x: x["anio"])
            if antes["valor"]:
                derivados.append(abs((despues["valor"] - antes["valor"]) / antes["valor"] * 100.0))
    return derivados


def _anios_del_contexto(lineas: list[str], items: list[dict]) -> set[int]:
    anios = {int(i["anio"]) for i in items if i.get("anio") is not None}
    for n in extraer_numeros("\n".join(lineas)):
        if _es_anio(n):
            anios.add(int(n.valor))
    return anios


def _conteo_valido(n: Numero, items: list[dict], anios: set[int]) -> bool:
    """'N años' = diferencia entre dos años presentes (o cantidad de años); 'N municipios / variables' = conteos
    reales del contexto."""
    if not n.entero_sin_formato or n.valor < 0 or n.siguiente not in PALABRAS_CONTEO:
        return False
    cuenta = int(n.valor)
    clase = PALABRAS_CONTEO[n.siguiente]
    if clase == "anios":
        if cuenta in {abs(a - b) for a, b in combinations(sorted(anios), 2)}:
            return True
        return cuenta == len(anios) and cuenta > 0
    campo = "codigodivipola" if clase == "municipios" else "variable"
    reales = {i.get(campo) for i in items}
    reales.discard(None)
    return cuenta == len(reales) and cuenta > 0


def validar_cifras(texto: str, lineas_contexto: list[str], items: list[dict] | None = None, pregunta: str = "") -> dict:
    items = items or []
    numeros_ctx = extraer_numeros("\n".join(lineas_contexto))
    anios_permitidos = {int(n.valor) for n in numeros_ctx if _es_anio(n)} | {int(n.valor) for n in extraer_numeros(pregunta) if _es_anio(n)}
    # los valores del contexto, tal como estan escritos en sus lineas, mas los estructurados (items) si se dan
    referencias = [n.valor for n in numeros_ctx] + [float(i["valor"]) for i in items if i.get("valor") is not None]
    derivados = valores_derivados(items)
    anios_contexto = _anios_del_contexto(lineas_contexto, items)

    no_validadas, total = [], 0
    for n in extraer_numeros(texto):
        if _es_anio(n) and int(n.valor) in anios_permitidos:
            continue
        total += 1
        if any(_coincide(n, r) for r in referencias):
            continue
        if _conteo_valido(n, items, anios_contexto):
            continue
        if derivados and _tiene_comparacion(n.oracion) and any(_coincide(n, d) for d in derivados):
            continue
        no_validadas.append(n.texto)
    if no_validadas:
        return {"estado": "no_validada", "no_validadas": no_validadas}
    return {"estado": "ok" if total else "abstencion", "no_validadas": []}
