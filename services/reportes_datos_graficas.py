"""Datos que consumen las gráficas del reporte (ver reportes_escenas.py): promedio del periodo por
zona, promedio nacional (universo de municipios) y geometrías simplificadas para el coroplético.

Las gráficas usan el MISMO cálculo que las tablas del reporte (obtener_datos_reporte): el valor de un
departamento es el promedio simple de sus municipios con dato y el score de privación usa la
normalización por año de todos los municipios. Aquí solo se agrega lo que ese cálculo no trae:
  * `nacional[variable]`: media, mínimo, cuartiles y máximo de los municipios del país (cada municipio
    con su promedio del periodo; el «promedio nacional» es la media simple de esos promedios);
  * `geometrias[codigo]`: contorno GeoJSON simplificado en la BD (ST_SimplifyPreserveTopology).
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from schemas.reporte import ReporteGenerarRequest
from services.reportes_escenas import DatosGraficas, Resultado, construir_escena
from services.reportes_graficas_catalogo import TIPOS
from services.reportes_service import _finito
from services.score_privacion import (
    CODIGO_AGREGADO_NACIONAL_TERRIDATA,
    VARIABLES_SCORE,
    calcular_score_privacion,
    normalizar_minmax,
)

logger = logging.getLogger(__name__)


def variables_de_graficas(solicitud: ReporteGenerarRequest) -> list[str]:
    """Variables del reporte más las que usan las gráficas (en orden, sin repetir)."""
    todas = list(solicitud.variables)
    for grafica in solicitud.graficas:
        todas.extend(grafica.variables)
    return list(dict.fromkeys(todas))


def _media(valores: list[float | None]) -> float | None:
    presentes = [v for v in valores if v is not None]
    return statistics.fmean(presentes) if presentes else None


def _estadisticos(valores: list[float]) -> dict | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    if len(ordenados) >= 2:
        q1, mediana, q3 = statistics.quantiles(ordenados, n=4, method="inclusive")
    else:
        q1 = mediana = q3 = ordenados[0]
    return {
        "media": statistics.fmean(ordenados),
        "min": ordenados[0],
        "q1": q1,
        "mediana": mediana,
        "q3": q3,
        "max": ordenados[-1],
        "n": len(ordenados),
    }


def calcular_nacional(db: Session, variables: list[str], anio_inicio: int, anio_fin: int) -> dict[str, dict]:
    """Estadísticos del promedio del periodo de cada municipio del país, por variable."""
    universo = [
        fila[0]
        for fila in db.execute(
            text("""
                SELECT codigo_municipio FROM public.zonas_municipio
                WHERE codigo_municipio != '00000' AND codigo_municipio != :nacional
            """),
            {"nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA},
        )
    ]
    if not universo:
        return {}
    pide_score = "score_privacion" in variables
    consultar = sorted((set(variables) - {"score_privacion"}) | (set(VARIABLES_SCORE) if pide_score else set()))
    filas = db.execute(
        text("""
            SELECT codigodivipola, namevar, yearm, valuevar
            FROM public.mapcalordb
            WHERE codigodivipola = ANY(:codigos)
              AND namevar = ANY(:variables)
              AND yearm BETWEEN :anio_inicio AND :anio_fin
        """),
        {"codigos": universo, "variables": consultar, "anio_inicio": anio_inicio, "anio_fin": anio_fin},
    ).mappings().all()
    por_municipio: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for fila in filas:
        valor = _finito(fila["valuevar"])
        if valor is not None:
            por_municipio[(fila["codigodivipola"], fila["namevar"])][fila["yearm"]] = valor

    medias: dict[str, dict[str, float]] = defaultdict(dict)
    for (codigo, variable), anuales in por_municipio.items():
        if variable in variables:
            medias[variable][codigo] = statistics.fmean(anuales.values())

    if pide_score:
        # Mismo score que el reporte: min-max por año sobre todos los municipios y promedio de los tres insumos.
        limites: dict[tuple[int, str], tuple[float, float]] = {}
        for anio in range(anio_inicio, anio_fin + 1):
            for variable in VARIABLES_SCORE:
                valores = [anuales[anio] for (codigo, v), anuales in por_municipio.items() if v == variable and anio in anuales]
                if valores:
                    limites[(anio, variable)] = (min(valores), max(valores))
        for codigo in universo:
            scores = []
            for anio in range(anio_inicio, anio_fin + 1):
                indicadores = {}
                for variable in VARIABLES_SCORE:
                    minimo, maximo = limites.get((anio, variable), (None, None))
                    dato = por_municipio.get((codigo, variable), {}).get(anio)
                    indicadores[variable] = {"score_normalizado": normalizar_minmax(dato, minimo, maximo)}
                score = calcular_score_privacion(indicadores)
                if score is not None:
                    scores.append(score)
            if scores:
                medias["score_privacion"][codigo] = statistics.fmean(scores)

    return {variable: estadisticos for variable in variables if (estadisticos := _estadisticos(list(medias.get(variable, {}).values())))}


def _tolerancia(nivel: str, cantidad: int) -> float:
    if nivel == "departamento":
        return 0.01 if cantidad <= 40 else 0.03
    return 0.002 if cantidad <= 20 else 0.006 if cantidad <= 150 else 0.02


def obtener_geometrias(db: Session, zonas: list[dict]) -> dict[str, dict]:
    """Contorno GeoJSON simplificado de cada zona (lo que necesita el mapa coroplético)."""
    resultado: dict[str, dict] = {}
    for nivel, tabla, columna in (
        ("departamento", "public.zonas_departamento", "codigo_departamento"),
        ("municipio", "public.zonas_municipio", "codigo_municipio"),
    ):
        codigos = [z["codigo"] for z in zonas if z["nivel"] == nivel]
        if not codigos:
            continue
        filas = db.execute(
            text(f"""
                SELECT {columna} AS codigo,
                       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, :tolerancia), 4) AS geometria
                FROM {tabla}
                WHERE {columna} = ANY(:codigos)
            """),
            {"codigos": codigos, "tolerancia": _tolerancia(nivel, len(codigos))},
        ).mappings().all()
        for fila in filas:
            if fila["geometria"]:
                resultado[fila["codigo"]] = json.loads(fila["geometria"])
    return resultado


def construir_datos_graficas(db: Session, solicitud: ReporteGenerarRequest, zonas: list[dict], series: list[dict]) -> DatosGraficas:
    """`series` debe traer las variables de las gráficas (ver variables_de_graficas)."""
    anios = list(range(solicitud.periodo.anio_inicio, solicitud.periodo.anio_fin + 1))
    posicion = {anio: i for i, anio in enumerate(anios)}
    anual: dict[tuple[str, str], list[float | None]] = {}
    for fila in series:
        valores = anual.setdefault((fila["codigo"], fila["variable"]), [None] * len(anios))
        valores[posicion[fila["anio"]]] = _finito(fila["valor"])
    medias = {clave: _media(valores) for clave, valores in anual.items()}

    tipos = [TIPOS[grafica.tipo] for grafica in solicitud.graficas]
    nacional: dict[str, dict] = {}
    variables_nacional = sorted({v for g in solicitud.graficas if TIPOS[g.tipo].necesita_nacional for v in g.variables})
    if variables_nacional:
        nacional = calcular_nacional(db, variables_nacional, anios[0], anios[-1])
    geometrias: dict[str, dict] = {}
    if any(tipo.necesita_geometria for tipo in tipos):
        codigos = {c for g in solicitud.graficas if TIPOS[g.tipo].necesita_geometria for c in (g.zonas if g.modo != "general" else [z["codigo"] for z in zonas])}
        geometrias = obtener_geometrias(db, [z for z in zonas if z["codigo"] in codigos])
    return DatosGraficas(anios=anios, zonas=zonas, medias=medias, anual=anual, nacional=nacional, geometrias=geometrias)


def escenas_de_solicitud(db: Session, solicitud: ReporteGenerarRequest, zonas: list[dict], series: list[dict], ancho: float) -> list[tuple]:
    """[(spec, Resultado)] en el orden elegido por el usuario."""
    if not solicitud.graficas:
        return []
    datos = construir_datos_graficas(db, solicitud, zonas, series)
    return [(spec, construir_escena(spec, datos, ancho)) for spec in solicitud.graficas]


def a_json(spec, resultado: Resultado) -> dict:
    return {
        "id": spec.id,
        "tipo": spec.tipo,
        "modo": spec.modo,
        "titulo": resultado.titulo,
        "escena": resultado.escena,
        "tabla": resultado.tabla,
        "nota": resultado.nota,
    }
