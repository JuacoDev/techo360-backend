"""Utilidades de redaccion del generador 5.2: nombres, listas, tiempos verbales y lectura del contexto.

Las respuestas se arman SOLO con lo que trae el contexto ya renderizado por el orquestador: `LeerContexto`
convierte items y lineas en registros (`Dato`, `SinDato`, `Ubicacion`) y de ahi salen nombres, anios y valores.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from services.orquestador import alias as al
from services.orquestador import contexto as cx

PARTICULAS = {"De", "Del", "La", "Las", "Los", "Y", "El", "En"}

# --- variables -------------------------------------------------------------------------------------
# (articulo + nombre en minuscula, forma con «de» contraida)
FRASE_VARIABLE = {
    al.V_ACUEDUCTO: ("la cobertura de acueducto", "de la cobertura de acueducto"),
    al.V_ALCANTARILLADO: ("la cobertura de alcantarillado", "de la cobertura de alcantarillado"),
    al.V_DEFICIT_CUANTITATIVO: ("el déficit habitacional cuantitativo", "del déficit habitacional cuantitativo"),
    al.V_DEFICIT_CUALITATIVO: ("el déficit habitacional cualitativo", "del déficit habitacional cualitativo"),
    al.V_DENSIDAD: ("la densidad poblacional", "de la densidad poblacional"),
    al.V_SCORE: ("el score de privación", "del score de privación"),
}
# Frases con las que se PREGUNTA por una variable (todas las reconoce el resolutor real): (con articulo, sin articulo)
PREGUNTA_VARIABLE = {
    al.V_ACUEDUCTO: [("la cobertura de acueducto", "cobertura de acueducto"), ("el acueducto", "acueducto"),
                     ("la cobertura del servicio de acueducto", "cobertura del servicio de acueducto"),
                     ("la cobertura de acueducto", "cobertura de acueducto")],
    al.V_ALCANTARILLADO: [("la cobertura de alcantarillado", "cobertura de alcantarillado"), ("el alcantarillado", "alcantarillado"),
                          ("la cobertura del servicio de alcantarillado", "cobertura del servicio de alcantarillado"),
                          ("la cobertura de alcantarillado", "cobertura de alcantarillado")],
    al.V_DEFICIT_CUANTITATIVO: [("el déficit habitacional cuantitativo", "déficit habitacional cuantitativo"),
                                ("el déficit cuantitativo", "déficit cuantitativo"),
                                ("el déficit de vivienda cuantitativo", "déficit de vivienda cuantitativo")],
    al.V_DEFICIT_CUALITATIVO: [("el déficit habitacional cualitativo", "déficit habitacional cualitativo"),
                               ("el déficit cualitativo", "déficit cualitativo"),
                               ("el déficit de vivienda cualitativo", "déficit de vivienda cualitativo")],
    al.V_DENSIDAD: [("la densidad poblacional", "densidad poblacional"), ("la densidad de población", "densidad de población"),
                    ("la densidad poblacional", "densidad poblacional")],
    al.V_SCORE: [("el score de privación", "score de privación"), ("el índice de privación", "índice de privación"),
                 ("el score de privación", "score de privación")],
}
GENERO_PLURAL = {
    al.V_ACUEDUCTO: "las coberturas", al.V_ALCANTARILLADO: "las coberturas",
}


def sin_tildes(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(texto: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", sin_tildes(texto.lower()))).strip()


def cap1(texto: str) -> str:
    """Mayuscula inicial sin tocar el resto; salta signos de apertura."""
    for i, c in enumerate(texto):
        if c.isalpha():
            return texto[:i] + c.upper() + texto[i + 1:]
        if c.isdigit():
            return texto
    return texto


def nombre_natural(nombre: str) -> str:
    """'Santa Rosa De Cabal' -> 'Santa Rosa de Cabal' (el catalogo trae cada palabra capitalizada)."""
    partes = nombre.split(" ")
    return " ".join(p.lower() if i and p in PARTICULAS else p for i, p in enumerate(partes))


def informal(texto: str) -> str:
    """Pregunta de usuario descuidada: minusculas, sin tildes ni signos de interrogacion."""
    s = sin_tildes(texto.lower()).replace("¿", "").replace("?", "").replace("¡", "").replace("!", "")
    return re.sub(r"\s+", " ", s).strip()


def unir(items: list[str], conj: str = "y") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def cerrar(texto: str) -> str:
    """Quita puntos duplicados por nombres que ya terminan en punto (Bogotá, D.C.) sin tocar los puntos suspensivos."""
    return re.sub(r"(?<!\.)\.\.(?!\.)", ".", texto).strip()


def verbo_ser(anio: int) -> str:
    """fue / es / sera segun el anio frente a la fecha del proyecto (2026)."""
    return "fue" if anio < 2026 else ("es" if anio == 2026 else "será")


def var_frase(variable: str) -> str:
    return FRASE_VARIABLE[variable][0]


def var_de(variable: str) -> str:
    return FRASE_VARIABLE[variable][1]


def nombre_variable(variable: str) -> str:
    """Nombre como aparece en las lineas del contexto, en minuscula inicial."""
    n = al.NOMBRE_VARIABLE[variable]
    return n[0].lower() + n[1:]


# --- lectura del contexto ----------------------------------------------------------------------
@dataclass
class Dato:
    id: str
    variable: str
    divipola: str
    municipio: str
    departamento: str
    anio: int
    implicito: bool
    valor: float
    texto_valor: str
    real: bool
    fiabilidad: int | None
    fuente: str

    @property
    def cita(self) -> str:
        return f"{self.municipio} ({self.departamento})"


@dataclass
class SinDato:
    variable: str
    municipio: str
    departamento: str
    anio: str  # texto: «2012» o «sin año con dato real»
    motivo: str  # texto literal del contexto


@dataclass
class Ubicacion:
    municipio: str
    departamento: str


@dataclass
class Contexto:
    datos: list[Dato] = field(default_factory=list)
    sin_datos: list[SinDato] = field(default_factory=list)
    ubicaciones: list[Ubicacion] = field(default_factory=list)
    omitidos: int = 0
    lineas: list[str] = field(default_factory=list)


_SIN_DATO = re.compile(r"^Sin dato para (?P<var>.+?), (?P<mun>.+) \((?P<dep>[^()]+)\), (?P<anio>\d{4}|sin año con dato real): (?P<motivo>.+)\.$")
_UBICACION = re.compile(r"^Ubicación: (?P<mun>.+) pertenece al departamento de (?P<dep>.+)\.$")
_OMISION = re.compile(r"^Se omitieron (?P<n>\d+) datos por el límite de contexto\.$")
_VARIABLE_POR_NOMBRE = {v: k for k, v in al.NOMBRE_VARIABLE.items()}


def leer_contexto(render: dict, datos) -> Contexto:
    """render = Datos.renderizar(entrada). Los valores salen de los items del orquestador y se verifican contra
    la linea de texto (la cifra escrita debe ser la que el modelo vera)."""
    ctx = Contexto(lineas=list(render["lineas"]))
    por_id = {i["id"]: i for i in render["items"]}
    for linea in ctx.lineas:
        m = re.match(r"^\[(c\d+)\] ", linea)
        if m:
            item = por_id[m.group(1)]
            mun = datos.municipio(item["codigodivipola"])
            texto = cx.formato_valor(item["variable"], item["valor"])
            assert f"{mun['nombre']} ({mun['departamento']})" in linea and f": {texto}. " in linea, linea
            ctx.datos.append(Dato(
                id=item["id"], variable=item["variable"], divipola=mun["divipola"], municipio=mun["nombre"],
                departamento=mun["departamento"], anio=int(item["anio"]), implicito="(último año con dato real)" in linea,
                valor=float(item["valor"]), texto_valor=texto, real=bool(item["es_real"]),
                fiabilidad=item.get("fiabilidad"), fuente=item["fuente"],
            ))
            continue
        m = _SIN_DATO.match(linea)
        if m:
            ctx.sin_datos.append(SinDato(_VARIABLE_POR_NOMBRE[m.group("var")], m.group("mun"), m.group("dep"), m.group("anio"), m.group("motivo")))
            continue
        m = _UBICACION.match(linea)
        if m:
            ctx.ubicaciones.append(Ubicacion(m.group("mun"), m.group("dep")))
            continue
        m = _OMISION.match(linea)
        if m:
            ctx.omitidos = int(m.group("n"))
            continue
        if linea != cx.LINEA_SIN_CONTEXTO:
            raise ValueError(f"Linea de contexto no reconocida: {linea!r}")
    return ctx


# --- cifras derivadas (reglas 4 de fase5-1: diferencia y variacion porcentual) -----------------------
def decimales_variable(variable: str, valor: float) -> int:
    unidad = cx.UNIDAD[variable]
    if unidad == "porcentaje":
        return 1
    if unidad == "hab/km²":
        return 0 if abs(valor) >= 100 else 1
    return 4


def texto_diferencia(variable: str, a: float, b: float) -> str:
    """|a-b| con los decimales de la variable; porcentajes en puntos porcentuales."""
    d = abs(a - b)
    dec = decimales_variable(variable, max(abs(a), abs(b)))
    n = cx.formato_es(d, dec)
    unidad = cx.UNIDAD[variable]
    if unidad == "porcentaje":
        return f"{n} puntos porcentuales"
    if unidad == "hab/km²":
        return f"{n} hab/km²"
    return n


def texto_variacion(antes: float, despues: float) -> str | None:
    if not antes:
        return None
    v = abs((despues - antes) / antes * 100.0)
    return f"{cx.formato_es(v, 1)} %"
