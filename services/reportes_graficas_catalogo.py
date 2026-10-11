"""Catálogo de gráficas del reporte y sus reglas de compatibilidad (tipo × modo × variables).

Autoridad en el backend: el esquema de la solicitud (schemas/reporte.py) valida cada gráfica con
`validar_combinacion`. El frontend (pages/Reportes/graficasCatalogo.js) replica estas reglas para
mostrar solo lo compatible; si se cambia una, hay que cambiar la otra (las pruebas de ambos lados
fijan los mismos casos).

Reglas de visualización que se aplican aquí:
  * no se mezclan porcentajes, índices y hab/km² en un mismo eje: las gráficas de un solo eje exigen
    variables de la misma unidad (radar, dispersión, correlaciones y paneles las normalizan o separan);
  * el pastel/donut solo para dos partes (déficit cuantitativo + cualitativo);
  * nada en 3D.
"""
from __future__ import annotations

from dataclasses import dataclass

VARIABLES = (
    "score_privacion",
    "cobertura_acueducto",
    "cobertura_alcantarillado",
    "deficit_habitacional_cuantitativo",
    "deficit_habitacional_cualitativo",
    "densidad_poblacional",
)
COBERTURAS = ("cobertura_acueducto", "cobertura_alcantarillado")
DEFICITS = ("deficit_habitacional_cuantitativo", "deficit_habitacional_cualitativo")

# Unidad (grupo de eje) de cada variable.
GRUPO_UNIDAD = {
    "score_privacion": "indice",
    "cobertura_acueducto": "porcentaje",
    "cobertura_alcantarillado": "porcentaje",
    "deficit_habitacional_cuantitativo": "porcentaje",
    "deficit_habitacional_cualitativo": "porcentaje",
    "densidad_poblacional": "densidad",
}

MODOS = ("individual", "comparar", "general")
MAX_ZONAS_COMPARAR = 10
MAX_GRAFICAS = 20


@dataclass(frozen=True)
class TipoGrafica:
    id: str
    nombre: str
    modos: frozenset
    min_variables: int
    max_variables: int
    misma_unidad: bool = False
    # Variables permitidas (None = todas).
    permitidas: frozenset | None = None
    # Mínimo de zonas para el modo general (una distribución de 2 puntos no dice nada).
    min_zonas_general: int = 1
    # Necesita el universo nacional (promedio, mínimo, máximo, cuartiles de los municipios).
    necesita_nacional: bool = False
    necesita_geometria: bool = False


TIPOS: dict[str, TipoGrafica] = {
    tipo.id: tipo
    for tipo in (
        TipoGrafica("coropletico", "Mapa coroplético", frozenset({"comparar", "general"}), 1, 1, necesita_geometria=True, min_zonas_general=2),
        TipoGrafica("barras_ranking", "Barras ordenadas (ranking)", frozenset({"comparar", "general"}), 1, 1, necesita_nacional=True, min_zonas_general=2),
        TipoGrafica("barras_agrupadas", "Barras agrupadas", frozenset({"individual", "comparar", "general"}), 2, 4, misma_unidad=True),
        TipoGrafica("barras_apiladas", "Barras apiladas", frozenset({"individual", "comparar", "general"}), 1, 2, permitidas=frozenset(COBERTURAS + DEFICITS)),
        TipoGrafica("lineas", "Evolución anual (líneas)", frozenset({"individual", "comparar", "general"}), 1, 1),
        TipoGrafica("radar", "Radar vs. promedio nacional", frozenset({"individual", "comparar"}), 3, 6, necesita_nacional=True),
        TipoGrafica("dispersion", "Dispersión (scatter)", frozenset({"comparar", "general"}), 2, 3, min_zonas_general=3),
        TipoGrafica("histograma", "Histograma", frozenset({"general"}), 1, 1, necesita_nacional=True, min_zonas_general=3),
        TipoGrafica("boxplot", "Diagrama de caja (boxplot)", frozenset({"general"}), 1, 1, necesita_nacional=True, min_zonas_general=3),
        TipoGrafica("bullet", "Bullet vs. promedio nacional", frozenset({"individual"}), 1, 1, necesita_nacional=True),
        TipoGrafica("gauge", "Indicador tipo gauge", frozenset({"individual"}), 1, 1, permitidas=frozenset(VARIABLES) - {"densidad_poblacional"}, necesita_nacional=True),
        TipoGrafica("dumbbell", "Brecha entre dos variables (dumbbell)", frozenset({"comparar", "general"}), 2, 2, misma_unidad=True),
        TipoGrafica("donut", "Proporción entre déficits (donut)", frozenset({"individual"}), 2, 2, permitidas=frozenset(DEFICITS)),
        TipoGrafica("treemap", "Treemap", frozenset({"general"}), 1, 1, min_zonas_general=2),
        TipoGrafica("correlaciones", "Mapa de calor de correlaciones", frozenset({"general"}), 3, 6, min_zonas_general=4),
        TipoGrafica("paneles", "Paneles (small multiples)", frozenset({"comparar", "general"}), 2, 6, min_zonas_general=2),
    )
}


def validar_combinacion(tipo: str, modo: str, variables: list[str], zonas_del_modo: int) -> str | None:
    """Mensaje de error si la combinación no es válida; None si lo es.

    `zonas_del_modo`: zonas que usará la gráfica (individual 1, comparar las elegidas, general las
    seleccionadas en el reporte)."""
    definicion = TIPOS.get(tipo)
    if definicion is None:
        return f"Tipo de gráfica desconocido: {tipo}."
    if modo not in definicion.modos:
        return f"«{definicion.nombre}» no admite el modo {modo}; admite: {', '.join(sorted(definicion.modos))}."
    cantidad = len(variables)
    if cantidad < definicion.min_variables or cantidad > definicion.max_variables:
        rango = (
            str(definicion.min_variables)
            if definicion.min_variables == definicion.max_variables
            else f"{definicion.min_variables} a {definicion.max_variables}"
        )
        return f"«{definicion.nombre}» necesita {rango} variable(s), recibió {cantidad}."
    if definicion.permitidas is not None:
        invalidas = [v for v in variables if v not in definicion.permitidas]
        if invalidas:
            return f"«{definicion.nombre}» no admite la variable {invalidas[0]}."
    if definicion.misma_unidad and len({GRUPO_UNIDAD[v] for v in variables}) > 1:
        return f"«{definicion.nombre}» no mezcla unidades distintas en un mismo eje."
    if tipo == "barras_apiladas" and cantidad == 2 and set(variables) != set(DEFICITS):
        return "Las barras apiladas de dos variables son para los dos déficits (cuantitativo + cualitativo)."
    if tipo == "donut" and set(variables) != set(DEFICITS):
        return "El donut muestra la proporción entre el déficit cuantitativo y el cualitativo."
    if modo == "individual" and zonas_del_modo != 1:
        return "El modo individual usa exactamente una zona."
    if modo == "comparar" and not 2 <= zonas_del_modo <= MAX_ZONAS_COMPARAR:
        return f"Comparar requiere entre 2 y {MAX_ZONAS_COMPARAR} zonas."
    if modo == "general" and zonas_del_modo < definicion.min_zonas_general:
        return f"«{definicion.nombre}» en modo general necesita al menos {definicion.min_zonas_general} zonas seleccionadas."
    return None
