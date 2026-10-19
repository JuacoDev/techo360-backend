"""Mapa origen (originvar de mapcalordb) -> nombre legible de la fuente (fase4-especificacion-1, 4.2).
UN solo archivo. Cualquier origen de proyeccion que no este aqui se llama "Proyección Techo 360".
"""
from services.origen import es_proyeccion

FUENTE_PROYECCION = "Proyección Techo 360"

FUENTES_EXACTAS = {
    "TerriData": "DNP - TerriData",
    "DANE-CNPV2018": "DANE - CNPV 2018",
    "IGAC Catastro": "IGAC Catastro",
    "score-privacion": "Techo 360 - Score de privación",
    # La densidad proyectada sale de las proyecciones de poblacion del DANE (fase2-6, ejemplo c2).
    "proyeccion-poblacion-sobre-area": "DANE - Proyecciones de población",
}


def fuente_de_origen(origen: str | None) -> str:
    if origen in FUENTES_EXACTAS:
        return FUENTES_EXACTAS[origen]
    if es_proyeccion(origen):
        return FUENTE_PROYECCION
    return origen or "Fuente no identificada"
