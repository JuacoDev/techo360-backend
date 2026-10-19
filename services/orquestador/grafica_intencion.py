"""7.1 / W1 Intencion de grafica y linea de opciones (claude/fase7-1-especificacion.md, seccion 2, puntos 1 y 2).

intencion_grafica(texto, resolucion) -> IntencionGrafica(activa, evolucion)
  activa     el usuario pide una grafica (o un tipo de grafica) Y el resolutor encontro al menos un municipio sin
             aclaracion pendiente. Determinista, sin modelo ni base, como intencion_conceptual.
  evolucion  la grafica es una serie en el tiempo: palabras de evolucion/tendencia, o ninguna pista de otro tipo
             (barras, ranking, medidor, comparar). Solo con evolucion y SIN años en la pregunta el constructor de
             contexto trae la serie completa (contexto.py).

linea_opciones(municipios) -> la linea «Gráfica disponible. ...» del CONTEXTO (sin [cN]): el modelo copia de ahi los ids
de tipo y variable y los codigos DIVIPOLA en vez de recordarlos. Solo se agrega con intencion de grafica.
"""
import re
from dataclasses import dataclass

from services.orquestador.resolutor import normalizar
from services.reportes_graficas_catalogo import VARIABLES

PREFIJO_OPCIONES = "Gráfica disponible."
# Subconjunto que el modelo aprende en 0.4.1 (plan 7.1/7.2; decision: gauge, no bullet). El validador acepta el catalogo completo.
TIPOS_MODELO = ("lineas", "barras_ranking", "barras_agrupadas", "gauge")

_PIDE_GRAFICA = re.compile(
    r"\b(?:graficas?|graficos?|graficar|grafic\w*|diagramas?|medidor(?:es)?|gauge|en barras|barras|en lineas"
    r"|visualiz\w+|dibuja\w*|plot)\b"
    # tipos que no se emiten (G4): tambien activan la linea para que el modelo sepa que tipos si puede mostrar
    r"|\b(?:radar|dispersion|scatter|treemap|histograma|boxplot|dumbbell|donut|pastel|torta|coropletico)\b"
)
_EVOLUCION = re.compile(
    r"\b(?:evolucion\w*|tendencia|historic[oa]s?|historia|a lo largo|en el tiempo|ha (?:cambiado|variado|evolucionado|subido|bajado)"
    r"|serie(?:s)? (?:de )?tiempo|series?|en lineas|lineas|a traves de los anos|cada ano|por ano|anual(?:es)?|desde \d{4})\b"
)
_OTRO_TIPO = re.compile(
    r"\b(?:barras|ranking|medidor(?:es)?|gauge|compar\w+|versus|vs|ordenad\w+|top|radar|dispersion|scatter|treemap|histograma"
    r"|boxplot|dumbbell|donut|pastel|torta|coropletico|diagrama de caja)\b"
)


@dataclass(frozen=True)
class IntencionGrafica:
    activa: bool = False
    evolucion: bool = False


SIN_INTENCION = IntencionGrafica()


def intencion_grafica(texto: str, resolucion) -> IntencionGrafica:
    """Ver el docstring del modulo. `resolucion` es la del resolutor (4.1): sin municipio o con aclaracion pendiente
    nunca hay intencion de grafica (no hay zona que graficar)."""
    if resolucion is None or not resolucion.municipios or resolucion.aclaracion:
        return SIN_INTENCION
    n = normalizar(texto)
    if not n or not _PIDE_GRAFICA.search(n):
        return SIN_INTENCION
    if _EVOLUCION.search(n):
        return IntencionGrafica(True, True)
    return IntencionGrafica(True, not _OTRO_TIPO.search(n))


def linea_opciones(municipios: list[dict]) -> str:
    zonas = "; ".join(f"{m['divipola']} = {m['nombre']} ({m['departamento']})" for m in municipios)
    texto = (
        f"{PREFIJO_OPCIONES} Tipos: {', '.join(TIPOS_MODELO)}. Variables: {', '.join(VARIABLES)}. Zonas: {zonas}"
    )
    return texto if texto.endswith(".") else texto + "."  # «Bogotá, D.C.» ya termina en punto
