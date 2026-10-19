"""6.6 Enrutador de intencion: decide si una pregunta es CONCEPTUAL (definicion, metodo, relacion) y por tanto
puede consultar el RAG curado. Determinista, sin modelo, sin base de datos.

Regla (plan 0.4 seccion 8: los datos numericos SIEMPRE van por SQL, nunca por RAG):
  verdadero  solo si NO hay municipio resuelto, no hay aclaracion pendiente, la pregunta toca un termino del dominio
             y calza con un patron de definicion/metodo/relacion («que es», «como se calcula», «por que», «en que se
             diferencia», «cuantas dimensiones», «quien desarrollo»...).
  falso      para numeros (con municipio, con año), municipios con o sin variable, rankings, saludos, preguntas fuera
             del dominio e intentos de inyeccion.
Es conservador a proposito: una pregunta conceptual que no calza solo pierde el fragmento (el modelo responde como
antes de la 6.6); una pregunta de datos que calzara metería texto del RAG junto a cifras.
"""
import re

from services.orquestador.resolutor import normalizar

_PATRONES = tuple(re.compile(p) for p in (
    r"\bque (?:es|son|significa|significan|quiere decir|mide|miden|indica|indican|representa|representan)\b",
    r"\bque (?:variables|indicadores|componentes|metodos|metodo|fuentes|dimensiones|encuestas|estados|limitacion|limitaciones|insignia|diferencia|diferencias)\b",
    r"\bque (?:puede|pueden|puedes|no puede|no pueden) (?:responder|hacer|contestar)\b",
    r"\bque municipios se (?:tienen en cuenta|usan|incluyen|consideran)\b",
    r"\bcomo se (?:calcula|calculan|mide|miden|proyecta|proyectan|estima|estiman|normaliza|normalizan|define|definen|construye|construyen|obtiene|obtienen|llama|llaman|usa|usan|hace|hacen)\b",
    r"\bcomo (?:se si|sabe|saber|se sabe|puedo saber|distingo|diferencio|interpreto|se interpreta|se lee)\b",
    r"\bpor que\b",
    r"\ben que se diferencia(?:n)?\b",
    r"\bque diferencia(?:s)? hay\b",
    r"\bdiferencia entre\b",
    r"\bcuantas? (?:dimensiones|variables|indicadores|componentes|metodos|fragmentos)\b",
    r"\bcuantos municipios hay\b",
    r"\bquien (?:desarrollo|creo|diseno|definio|calcula|elabora|publica)\b",
    r"\bcuando se considera\b",
    r"\bcon que (?:encuestas?|escala|fuente|metodo)\b",
    r"\bde donde (?:sale|salen|vienen|viene|provienen|proviene|se obtiene|se obtienen)\b",
    r"\bde que (?:ano|fuente|anos) (?:es|son)\b",
    r"\bcual (?:es|son) (?:la|las|el|los) (?:unidad|definicion|formula|metodologia|fuente|fuentes|diferencia|escala|limitacion|limitaciones)\b",
    r"\bpuede (?:el|la) (?:score|indice|ipm|dato|valor)\b",
    r"\b(?:explica|explicame|define|definir|describe|describeme)\b",
))
_DOMINIO = frozenset({
    "score", "ipm", "indice", "privacion", "pobreza", "multidimensional", "conpes", "dane", "dnp", "deficit",
    "habitacional", "cuantitativo", "cualitativo", "densidad", "poblacional", "tenencia", "informal", "cobertura",
    "coberturas", "acueducto", "alcantarillado", "fiabilidad", "proyeccion", "proyecciones", "proyectan",
    "proyecta", "proyectado", "proyectados", "estimado", "estimada", "estimados", "dato", "datos",
    "divipola", "municipio", "municipios", "municipalizadas", "litigio", "hogar", "hogares", "dimensiones",
    "indicadores", "asistente", "chat", "mensaje", "mensajes", "panel", "insignia", "plataforma", "techo",
    "cnpv", "censo", "upra", "igac", "catastro", "predios",
})
_INYECCION = re.compile(
    r"\b(?:ignora|ignore|olvida|omite|salta|desactiva|revela|muestrame|dime|imprime|repite)\b.*\b(?:reglas|instrucciones|prompt|sistema|system|configuracion|internas?)\b"
    r"|\b(?:instrucciones|reglas|prompt) (?:internas?|del sistema|ocultas?)\b|\bsystem prompt\b|\bjailbreak\b|\bdeveloper mode\b"
)
_CIFRA_PEDIDA = re.compile(r"\b(?:cuanto|cuantos habitantes|cuantas personas|ranking|mas alto|mas bajo|mayor score|promedio)\b")


def intencion_conceptual(texto: str, resolucion) -> bool:
    """True si la pregunta es de definicion/metodo/relacion y puede consultar el RAG. `resolucion` es la del
    resolutor (4.1): con municipios o una aclaracion pendiente nunca es conceptual."""
    if resolucion is not None and (resolucion.municipios or resolucion.aclaracion):
        return False
    n = normalizar(texto)
    if not n or _INYECCION.search(n) or _CIFRA_PEDIDA.search(n):
        return False
    if not any(p in _DOMINIO for p in n.split()):
        return False
    return any(p.search(n) for p in _PATRONES)
