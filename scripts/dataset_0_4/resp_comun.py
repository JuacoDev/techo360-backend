"""Piezas comunes de las plantillas de respuesta (5.2): tiempos verbales, avisos de proyeccion, listas y eleccion
equilibrada de plantillas. Ninguna cifra se escribe aqui: llegan en los `Dato` leidos del contexto renderizado."""
import random

from . import lengua as lg

# --------------------------------------------------------------------------- eleccion equilibrada
class Eleccion:
    """Reparte las plantillas de forma pareja (bolsa barajada con la semilla del generador)."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self._bolsas: dict[str, list[int]] = {}

    def indice(self, clave: str, n: int) -> int:
        clave = f"{clave}#{n}"  # misma clave con listas de distinto tamaño = bolsas distintas
        bolsa = self._bolsas.get(clave)
        if not bolsa:
            bolsa = list(range(n))
            self.rng.shuffle(bolsa)
            self._bolsas[clave] = bolsa
        return bolsa.pop()

    def de(self, clave: str, opciones: list):
        i = self.indice(clave, len(opciones))
        return i, opciones[i]


# --------------------------------------------------------------------------- tiempos
def tiempos(anio: int) -> dict:
    if anio < 2026:
        return {"fue": "fue", "reg": "registró", "tuvo": "tuvo", "pres": "presentó", "sereg": "Se registró", "est": "se estimó"}
    if anio == 2026:
        return {"fue": "es", "reg": "registra", "tuvo": "tiene", "pres": "presenta", "sereg": "Se registra", "est": "se estima"}
    return {"fue": "será", "reg": "registrará", "tuvo": "tendrá", "pres": "presentará", "sereg": "Se registrará", "est": "se proyecta"}


# --------------------------------------------------------------------------- avisos de proyeccion
AVISOS = [
    "Es una estimación con fiabilidad de {f}, no un dato observado.",
    "Se trata de una proyección, con una fiabilidad de {f}.",
    "Este valor es una estimación (fiabilidad de {f}).",
    "Ten en cuenta que es una proyección y su fiabilidad es de {f}.",
    "Es un dato proyectado, no medido, con fiabilidad de {f}.",
    "Como es una estimación, su fiabilidad es de {f}.",
    "Cabe aclarar que es una estimación, con fiabilidad de {f}.",
    "Es una estimación y no un dato real; su fiabilidad es de {f}.",
    "Recuerda que se trata de una estimación (fiabilidad de {f}).",
    "Es un valor estimado, con una fiabilidad de {f}.",
    "Se trata de una estimación cuya fiabilidad es de {f}.",
    "Aclaro que es una proyección, con fiabilidad de {f}.",
]
AVISOS_VARIOS = [
    "Todos son valores proyectados, con {f}.",
    "Se trata de estimaciones, con {f}.",
    "Ninguno es un dato observado: son estimaciones con {f}.",
    "Son proyecciones: {f}.",
    "Estos valores son estimaciones, con {f}.",
    "Aclaro que son proyecciones, con {f}.",
]


def texto_fiabilidad(datos: list, con_articulo: bool = False) -> str:
    """«30 %» o «31 % a 55 %»; siempre valores que aparecen en las lineas del contexto (nunca un promedio)."""
    f = sorted({d.fiabilidad for d in datos if d.fiabilidad is not None})
    if not f:
        raise ValueError("Dato proyectado sin fiabilidad en el contexto")
    return f"{f[0]} %" if len(f) == 1 else f"{f[0]} % a {f[-1]} %"


def fiab_frase(datos: list) -> str:
    """«fiabilidad de 30 %» / «fiabilidad de 31 % a 55 %»."""
    return f"fiabilidad de {texto_fiabilidad(datos)}"


def aviso(datos: list, el: Eleccion, clave: str = "aviso") -> tuple[int, str]:
    """Aviso de proyeccion para uno o varios datos proyectados. Devuelve (indice, texto)."""
    if len(datos) == 1:
        i, p = el.de(clave + "1", AVISOS)
        return i, p.format(f=texto_fiabilidad(datos))
    i, p = el.de(clave + "n", AVISOS_VARIOS)
    return 100 + i, p.format(f=fiab_frase(datos))


def lista_anios(datos: list) -> str:
    return lg.unir([str(d.anio) for d in datos])


def sust(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def datos_de(n: int) -> str:
    return "el dato de" if n == 1 else "los datos de"


def ser(n: int) -> str:
    return "es" if n == 1 else "son"


def unir_pc(partes: list[str], con_y: bool = True) -> str:
    """Una lista con punto y coma: 'a; b y c' (o 'a; b; c' si con_y es falso)."""
    partes = [p for p in partes if p]
    if len(partes) <= 1:
        return "".join(partes)
    if not con_y:
        return "; ".join(partes)
    return "; ".join(partes[:-1]) + " y " + partes[-1]


def ultimo_cierre(texto: str) -> str:
    texto = lg.cerrar(texto.strip())
    return texto if texto.endswith((".", "?", "!", ":")) else texto + "."
