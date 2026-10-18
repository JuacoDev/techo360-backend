"""4.1 Resolutor de entidades (fase4-especificacion-1). Determinista, sin modelo, sin escrituras.

resolver(texto, catalogo, hoy) -> Resolucion
    municipios  [{divipola, nombre, departamento}]   (maximo 5; siempre codigo DIVIPOLA de 5 digitos)
    variables   [id]                                  (vacio = pregunta conceptual o sin variable)
    anios       [int]  rango  (a, b) | None  anio_implicito  aclaracion None | {tipo, texto}

Fuente unica de municipios: zonas_municipio (universo de 1.104 sin 00000 ni 01001), cargada con
cargar_catalogo(db) o armada a mano en las pruebas. Nunca se resuelve por nombre a un dato.
"""
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text

from services.orquestador.alias import (
    ALIAS_DEPARTAMENTOS,
    ALIAS_MUNICIPIOS,
    ALIAS_VARIABLES,
    CONECTORES,
    NOMBRE_VISIBLE_DEPARTAMENTO,
    NOMBRES_SENSIBLES,
)

MAX_MUNICIPIOS = 5
ANIO_MIN, ANIO_MAX = 2000, 2042
# Intencion geografica ("¿en qué departamento está X?", "¿a qué departamento pertenece X?", "¿dónde queda X?"):
# cuenta como intencion de datos para aclarar homonimos. Sobre texto normalizado (sin tildes ni puntuacion).
_INTENCION_GEOGRAFICA = re.compile(
    r"\b(?:(?:en|a|de) que departamento (?:esta|queda|se encuentra|se ubica|pertenece|es)"
    r"|donde (?:queda|esta|se encuentra|se ubica))\b"
)
CODIGOS_FUERA_DEL_UNIVERSO = ("00000", "01001")


def normalizar(texto: str) -> str:
    """Minusculas, sin tildes, sin puntuacion, espacios colapsados."""
    s = unicodedata.normalize("NFKD", (texto or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", s)).strip()


def tokenizar(texto: str) -> list[tuple[str, bool]]:
    """[(palabra normalizada, empieza con mayuscula en el texto original)]; misma segmentacion que
    normalizar(texto).split()."""
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return [(m.group().lower(), m.group()[0].isupper()) for m in re.finditer(r"[A-Za-z0-9]+", s)]


@dataclass(frozen=True)
class Municipio:
    divipola: str
    nombre: str
    cod_departamento: str
    departamento: str


class Catalogo:
    """Indices de municipios y departamentos para la coincidencia por palabras completas."""

    def __init__(self, municipios: list[Municipio]):
        self.municipios = list(municipios)
        self.por_codigo = {m.divipola: m for m in self.municipios}
        self.por_nombre: dict[str, list[Municipio]] = defaultdict(list)
        for m in self.municipios:
            self.por_nombre[normalizar(m.nombre)].append(m)
        for alias, codigo in ALIAS_MUNICIPIOS.items():
            m = self.por_codigo.get(codigo)
            if m is not None and m not in self.por_nombre[alias]:
                self.por_nombre[alias].append(m)
        self.nombre_departamento: dict[str, str] = {}
        for m in self.municipios:
            self.nombre_departamento.setdefault(
                m.cod_departamento, NOMBRE_VISIBLE_DEPARTAMENTO.get(m.cod_departamento, m.departamento)
            )
        self.departamentos: dict[str, str] = {}  # nombre normalizado -> codigo
        for codigo, nombre in self.nombre_departamento.items():
            self.departamentos[normalizar(nombre)] = codigo
            for alias in ALIAS_DEPARTAMENTOS.get(codigo, ()):
                self.departamentos[alias] = codigo
        claves = list(self.por_nombre) + list(self.departamentos)
        self.max_tokens = max((len(k.split()) for k in claves), default=1)


def cargar_catalogo(db) -> Catalogo:
    """SELECT parametrizado de zonas_municipio (+ nombre del departamento). Solo lectura."""
    filas = db.execute(
        text("""
            SELECT m.codigo_municipio, m.codigo_departamento, m.nombre AS municipio, d.nombre AS departamento
            FROM public.zonas_municipio m
            LEFT JOIN public.zonas_departamento d ON d.codigo_departamento = m.codigo_departamento
            WHERE m.codigo_municipio <> ALL(:excluidos)
            ORDER BY m.codigo_municipio
        """),
        {"excluidos": list(CODIGOS_FUERA_DEL_UNIVERSO)},
    ).mappings().all()
    return Catalogo([
        Municipio(f["codigo_municipio"], f["municipio"], f["codigo_departamento"], f["departamento"] or "")
        for f in filas
    ])


@dataclass
class Resolucion:
    municipios: list[dict] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    anios: list[int] = field(default_factory=list)
    rango: tuple[int, int] | None = None
    anio_implicito: bool = True
    aclaracion: dict | None = None

    def a_dict(self) -> dict:
        return {
            "municipios": self.municipios, "variables": self.variables, "anios": self.anios,
            "rango": list(self.rango) if self.rango else None, "anio_implicito": self.anio_implicito,
            "aclaracion": self.aclaracion,
        }


# ----------------------------------------------------------------------------------- años
_RANGOS = (
    re.compile(r"(?<!\d)(20[0-4]\d)\s*[-–—]\s*(20[0-4]\d)(?!\d)"),
    re.compile(r"entre\s+(?:el\s+)?(20[0-4]\d)\s+y\s+(?:el\s+)?(20[0-4]\d)(?!\d)"),
    re.compile(r"de(?:sde)?\s+(?:el\s+)?(20[0-4]\d)\s+(?:a|hasta)\s+(?:el\s+)?(20[0-4]\d)(?!\d)"),
    re.compile(r"del\s+(20[0-4]\d)\s+al\s+(20[0-4]\d)(?!\d)"),
)
_ANIO = re.compile(r"(?<![\d\-–—])(20[0-4]\d)(?![\d\-–—])")


def resolver_anios(texto: str, hoy: date) -> tuple[list[int], tuple[int, int] | None]:
    bajo = unicodedata.normalize("NFKD", (texto or "").lower())
    bajo = "".join(c for c in bajo if not unicodedata.combining(c))
    anios: set[int] = set()
    rango = None
    for patron in _RANGOS:
        for m in patron.finditer(bajo):
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            if ANIO_MIN <= a and b <= ANIO_MAX:
                anios.update(range(a, b + 1))
                rango = (a, b) if rango is None else (min(rango[0], a), max(rango[1], b))
        bajo = patron.sub(" ", bajo)
    for m in _ANIO.finditer(bajo):
        y = int(m.group(1))
        if ANIO_MIN <= y <= ANIO_MAX:
            anios.add(y)
    norm = normalizar(texto)
    if re.search(r"\beste ano\b", norm):
        anios.add(hoy.year)
    if re.search(r"\b(el )?ano pasado\b", norm):
        anios.add(hoy.year - 1)
    return sorted(anios), rango


# ------------------------------------------------------------------------------ variables
def resolver_variables(tokens: list[str], ocupado: set[int]) -> list[str]:
    """Alias de variables por palabras completas, la frase mas larga primero; ignora los tokens ya usados
    por municipios o departamentos."""
    n_max = max(len(k.split()) for k in ALIAS_VARIABLES)
    usado = set(ocupado)
    encontradas: list[tuple[int, tuple]] = []
    candidatos = []
    for i in range(len(tokens)):
        for n in range(min(n_max, len(tokens) - i), 0, -1):
            clave = " ".join(tokens[i:i + n])
            if clave in ALIAS_VARIABLES:
                candidatos.append((n, i, clave))
    for n, i, clave in sorted(candidatos, key=lambda c: (-c[0], c[1])):
        rango = set(range(i, i + n))
        if rango & usado:
            continue
        usado |= rango
        encontradas.append((i, ALIAS_VARIABLES[clave]))
    variables: list[str] = []
    for _, ids in sorted(encontradas, key=lambda e: e[0]):
        for v in ids:
            if v not in variables:
                variables.append(v)
    return variables


# ------------------------------------------------------------------------------ municipios
@dataclass
class _Span:
    inicio: int
    fin: int  # exclusivo
    clave: str
    es_mun: bool
    es_dep: bool
    tipo: str = ""
    departamento: str | None = None  # codigo de departamento enlazado (solo municipios)
    forzado_dep: bool = False

    @property
    def n(self) -> int:
        return self.fin - self.inicio


def _spans(tokens: list[str], cat: Catalogo) -> list[_Span]:
    candidatos = []
    for i in range(len(tokens)):
        for n in range(min(cat.max_tokens, len(tokens) - i), 0, -1):
            clave = " ".join(tokens[i:i + n])
            es_mun, es_dep = clave in cat.por_nombre, clave in cat.departamentos
            if es_mun or es_dep:
                candidatos.append(_Span(i, i + n, clave, es_mun, es_dep))
    elegidos: list[_Span] = []
    usado: set[int] = set()
    for s in sorted(candidatos, key=lambda c: (-c.n, c.inicio)):
        r = set(range(s.inicio, s.fin))
        if r & usado:
            continue
        usado |= r
        elegidos.append(s)
    elegidos.sort(key=lambda s: s.inicio)
    for s in elegidos:  # "departamento de X" / "en el departamento X": siempre departamento (4.1b)
        j = s.inicio - 1
        while j >= 0 and tokens[j] in ("de", "del", "el", "la"):
            j -= 1
        s.forzado_dep = s.es_dep and j >= 0 and tokens[j] in ("departamento", "dpto", "depto")
    hay_mun_puro = any(s.es_mun and not s.es_dep for s in elegidos)
    for s in elegidos:
        if s.forzado_dep:
            s.tipo = "dep"
        elif s.es_mun and s.es_dep:
            s.tipo = "dep" if hay_mun_puro else "mun"  # "Caldas" sola es municipio; "Rionegro, Caldas" lleva departamento
        else:
            s.tipo = "mun" if s.es_mun else "dep"
    # Dos nombres que son a la vez municipio y departamento ("Caldas, Boyacá", "Córdoba de Bolívar",
    # "Nariño, Nariño"): el segundo es el departamento del primero si ese municipio existe alli.
    for x, y in zip(elegidos, elegidos[1:]):
        if not (x.tipo == y.tipo == "mun" and x.es_dep and y.es_dep and not x.forzado_dep and not y.forzado_dep):
            continue
        if not all(t in CONECTORES for t in tokens[x.fin:y.inicio]):
            continue
        if any(m.cod_departamento == cat.departamentos[y.clave] for m in cat.por_nombre[x.clave]):
            y.tipo = "dep"
        elif any(m.cod_departamento == cat.departamentos[x.clave] for m in cat.por_nombre[y.clave]):
            x.tipo = "dep"
    return elegidos


def _enlazar_departamentos(tokens: list[str], spans: list[_Span], cat: Catalogo) -> None:
    muns = [s for s in spans if s.tipo == "mun"]
    deps = [s for s in spans if s.tipo == "dep"]

    def solo_conectores(a: int, b: int) -> bool:
        return all(t in CONECTORES for t in tokens[a:b])

    sin_enlazar = []
    for d in deps:
        codigo = cat.departamentos[d.clave]
        previos = [m for m in muns if m.fin <= d.inicio and m.departamento is None and solo_conectores(m.fin, d.inicio)]
        siguientes = [m for m in muns if m.inicio >= d.fin and m.departamento is None and solo_conectores(d.fin, m.inicio)]
        if previos:
            max(previos, key=lambda m: m.fin).departamento = codigo
        elif siguientes:
            min(siguientes, key=lambda m: m.inicio).departamento = codigo
        else:
            sin_enlazar.append(codigo)
    codigos = set(sin_enlazar)
    if len(codigos) == 1:  # un departamento suelto en la pregunta: aplica a los municipios sin enlace
        unico = next(iter(codigos))
        for m in muns:
            if m.departamento is None:
                m.departamento = unico


def _listar(entradas: list[Municipio]) -> str:
    return ", ".join(f"{m.nombre} ({m.departamento})" for m in entradas)


def resolver(texto: str, cat: Catalogo, hoy: date | None = None) -> Resolucion:
    hoy = hoy or date.today()
    palabras = tokenizar(texto)
    tokens = [p for p, _ in palabras]
    spans = _spans(tokens, cat)
    _enlazar_departamentos(tokens, spans, cat)
    # 4.1b: un nombre "sensible" (coincide con una palabra comun) solo cuenta como municipio si va
    # capitalizado, precedido de "de/del/en" o acompañado de un departamento.
    for s in spans:
        if s.tipo == "mun" and s.clave in NOMBRES_SENSIBLES:
            capitalizado = palabras[s.inicio][1]
            precedido = s.inicio > 0 and tokens[s.inicio - 1] in ("de", "del", "en")
            if not (capitalizado or precedido or s.departamento is not None):
                s.tipo = "ignorado"
    muns = [s for s in spans if s.tipo == "mun"]
    ocupado = {i for s in spans for i in range(s.inicio, s.fin)}
    res = Resolucion()
    res.variables = resolver_variables(tokens, ocupado)
    res.anios, res.rango = resolver_anios(texto, hoy)
    res.anio_implicito = not res.anios
    intencion_geografica = bool(_INTENCION_GEOGRAFICA.search(normalizar(texto)))

    if len({s.clave for s in muns}) > MAX_MUNICIPIOS:
        res.aclaracion = {"tipo": "demasiados",
                          "texto": f"Mencionas más de {MAX_MUNICIPIOS} municipios. Indica hasta {MAX_MUNICIPIOS} para poder responder."}
        return res

    vistos: set[str] = set()
    for s in muns:
        entradas = cat.por_nombre[s.clave]
        nombre = entradas[0].nombre
        if s.departamento is not None:
            elegidas = [m for m in entradas if m.cod_departamento == s.departamento]
            if not elegidas:
                reales = " o ".join(dict.fromkeys(cat.nombre_departamento[m.cod_departamento] for m in entradas))
                pedido = cat.nombre_departamento[s.departamento]
                opciones = " o ".join(f"{m.nombre} ({cat.nombre_departamento[m.cod_departamento]})" for m in entradas)
                res.aclaracion = {"tipo": "inconsistente",
                                  "texto": f"{nombre} pertenece a {reales}, no a {pedido}. ¿Quieres consultar {opciones}?"}
                res.municipios = []
                return res
            entradas = elegidas
        if len(entradas) > 1:
            if not (res.variables or res.anios or intencion_geografica):
                continue  # 4.1b: sin intencion de datos un homonimo no pide aclaracion; simplemente no se resuelve
            res.aclaracion = {"tipo": "homonimo",
                              "texto": f"Hay varios municipios llamados {nombre}: {_listar(entradas)}. ¿A cuál te refieres?"}
            res.municipios = []
            return res
        m = entradas[0]
        if m.divipola not in vistos:
            vistos.add(m.divipola)
            res.municipios.append({"divipola": m.divipola, "nombre": m.nombre,
                                   "departamento": cat.nombre_departamento[m.cod_departamento]})
    return res
