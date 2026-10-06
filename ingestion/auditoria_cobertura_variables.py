"""
Auditoria de cobertura (solo lectura, no modifica nada): cruza los 1,105
municipios reales de zonas_municipio (capa canonica IGAC, la misma que
usa el mapa del frontend) contra las 7 variables del panel de
indicadores, para yearm=2024 -- el UNICO anio que hoy pide el frontend
(ANIO_INDICADORES fijo en techo360-frontend/src/services/api.js).

Paso 0 de la Fase 2 (reduccion de proyecciones via scraping): antes de
escribir un solo scraper hace falta saber CUALES municipios/variables
realmente necesitan el esfuerzo -- no tiene sentido scrapear parejo los
1,105 si una parte relevante ya tiene dato real.

Por municipio, para cada una de las 7 variables, clasifica la celda en:
  - "real": existe fila en mapcalordb con origen que NO empieza con
    "proyeccion-".
  - "proyectado": existe fila con origen que SI empieza con
    "proyeccion-" -- se extrae el anio del dato real ancla desde el
    propio string de origen (ver ORIGENES_CONOCIDOS abajo).
  - "sin_dato": no existe ninguna fila para esa combinacion
    municipio+variable+2024 -- ni siquiera relleno/proyeccion.

>>> HALLAZGO DE CALIDAD DE DATOS, no ocultar <<<
Al cruzar contra zonas_municipio aparecen ~18-19 codigos DANE (todos en
Amazonas/Guainia/Vaupes, ej. "91263 El Encanto", "94883 San Felipe") que
SI tienen filas reales en mapcalordb (deficit_habitacional/
tenencia_informal) pero NO existen en zonas_municipio -- son
corregimientos departamentales reales que DANE reporta como unidad
propia pero que la capa IGAC del mapa no traza como poligono separado.
No es basura ni un codigo espurio (a diferencia del "01001" agregado
nacional de TerriData, ya excluido en un commit anterior) -- es un
desalineamiento real entre el universo de unidades de DANE y el de
IGAC. Esta auditoria los IGNORA (cuenta solo contra los 1,105 de
zonas_municipio, que es lo que el mapa puede mostrar), pero se listan
aparte en el reporte para que quede visible, no se pierden en silencio.

Salida (2 archivos CSV en el directorio donde se corre):
  - auditoria_cobertura_detalle.csv: 1,105 x 7 filas, una por
    municipio x variable.
  - auditoria_cobertura_ranking.csv: 1,105 filas, una por municipio,
    con el ranking de prioridad para el scraping de la Fase 2.

Uso:
    python -m ingestion.auditoria_cobertura_variables
"""
import csv
import re
from pathlib import Path

import psycopg2
import psycopg2.extras

from core.config import settings

ANIO_FRONTEND = 2024  # el unico anio que hoy expone GET /api/zonas?nivel=municipio

VARIABLES = [
    "densidad_poblacional",
    "cobertura_acueducto",
    "cobertura_alcantarillado",
    "tenencia_informal_terrenos",
    "deficit_habitacional_cuantitativo",
    "deficit_habitacional_cualitativo",
    "deficit_habitacional_total",
]

PREFIJO_PROYECCION = "proyeccion-"

# Mapeo explicito origen -> anio del dato real ancla (ver catalogo
# completo y justificado en ingestion/db.py). Si aparece un origen
# proyeccion-* NO listado aqui, se intenta parsear un sufijo "-AAAA"
# como fallback (ver _anio_desde_origen) y si tampoco funciona se
# reporta como anio_dato_real=None -- nunca se asume un numero.
ORIGENES_CONOCIDOS = {
    "proyeccion-tendencia-nacional-2018-2024": 2018,
    "proyeccion-snapshot-2026": 2026,
    "proyeccion-ultimo-valor-observado-2024": 2024,
}

DIR_SALIDA = Path(__file__).resolve().parent.parent


def _anio_desde_origen(origen: str) -> int | None:
    if origen in ORIGENES_CONOCIDOS:
        return ORIGENES_CONOCIDOS[origen]
    m = re.search(r"-(\d{4})$", origen)
    return int(m.group(1)) if m else None


def cargar_municipios(cur) -> list[dict]:
    cur.execute(
        "SELECT codigo_municipio, codigo_departamento, nombre FROM zonas_municipio ORDER BY codigo_municipio"
    )
    return cur.fetchall()


def cargar_indicadores_2024(cur) -> dict[tuple[str, str], dict]:
    """Todo mapcalordb en yearm=2024, indexado por (codigo, variable) --
    UNA sola consulta, para no hacer 1,105 x 7 round-trips."""
    cur.execute(
        """
        SELECT codigodivipola, namevar, originvar, valuevar
        FROM mapcalordb
        WHERE yearm = %s AND codigodivipola IS NOT NULL AND codigodivipola <> ''
        """,
        (ANIO_FRONTEND,),
    )
    indice = {}
    for fila in cur.fetchall():
        indice[(fila["codigodivipola"], fila["namevar"])] = fila
    return indice


def cargar_codigos_huerfanos(cur) -> list[dict]:
    """Filas de mapcalordb (2024) cuyo codigo NO esta en zonas_municipio
    -- ver aviso del modulo. Solo para reportar aparte, no cuentan en
    el cruce principal."""
    cur.execute(
        """
        SELECT DISTINCT m.codigodivipola, m.municipios
        FROM mapcalordb m
        LEFT JOIN zonas_municipio z ON m.codigodivipola = z.codigo_municipio
        WHERE m.yearm = %s AND z.codigo_municipio IS NULL
          AND m.codigodivipola IS NOT NULL AND m.codigodivipola <> ''
        ORDER BY m.codigodivipola
        """,
        (ANIO_FRONTEND,),
    )
    return cur.fetchall()


def clasificar_celda(fila) -> tuple[str, str | None, int | None]:
    """Devuelve (estado, origen, anio_dato_real)."""
    if fila is None:
        return "sin_dato", None, None
    origen = fila["originvar"]
    if origen.startswith(PREFIJO_PROYECCION):
        return "proyectado", origen, _anio_desde_origen(origen)
    return "real", origen, ANIO_FRONTEND


def main():
    conn = psycopg2.connect(settings.database_url)
    detalle = []
    ranking = []

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            municipios = cargar_municipios(cur)
            indice = cargar_indicadores_2024(cur)
            huerfanos = cargar_codigos_huerfanos(cur)
    finally:
        conn.close()

    print(f"{len(municipios)} municipios (zonas_municipio, capa canonica IGAC).")
    print(f"{len(indice)} celdas municipio+variable con alguna fila en mapcalordb (yearm={ANIO_FRONTEND}).")
    print(f"{len(huerfanos)} codigos huerfanos en mapcalordb sin match en zonas_municipio (ver CSV aparte).\n")

    for m in municipios:
        codigo = m["codigo_municipio"]
        nombre = m["nombre"]
        n_real = n_proyectado = n_sin_dato = 0
        antiguedad_total = 0
        variables_sin_dato = []
        variables_proyectadas = []

        for variable in VARIABLES:
            fila = indice.get((codigo, variable))
            estado, origen, anio_dato_real = clasificar_celda(fila)

            if estado == "real":
                n_real += 1
            elif estado == "proyectado":
                n_proyectado += 1
                variables_proyectadas.append(variable)
                if anio_dato_real is not None and anio_dato_real <= ANIO_FRONTEND:
                    antiguedad_total += ANIO_FRONTEND - anio_dato_real
            else:
                n_sin_dato += 1
                variables_sin_dato.append(variable)

            detalle.append({
                "codigo_municipio": codigo,
                "codigo_departamento": m["codigo_departamento"],
                "nombre_municipio": nombre,
                "variable": variable,
                "estado": estado,
                "origen": origen or "",
                "anio_dato_real": anio_dato_real if anio_dato_real is not None else "",
            })

        n_no_confirmado = n_proyectado + n_sin_dato
        ranking.append({
            "codigo_municipio": codigo,
            "codigo_departamento": m["codigo_departamento"],
            "nombre_municipio": nombre,
            "n_variables_reales": n_real,
            "n_variables_proyectadas": n_proyectado,
            "n_variables_sin_dato": n_sin_dato,
            "n_variables_no_confirmadas": n_no_confirmado,
            "antiguedad_total_anios": antiguedad_total,
            "variables_sin_dato": ";".join(variables_sin_dato),
            "variables_proyectadas": ";".join(variables_proyectadas),
        })

    # Ranking de prioridad: primero mas variables sin confirmar (7 = el
    # peor caso posible), luego mayor antiguedad acumulada como
    # desempate -- ver docstring del modulo para por que se separan
    # n_sin_dato/n_proyectado en columnas propias (el usuario puede
    # re-ordenar con su propio criterio de peso si no coincide con este).
    ranking.sort(key=lambda r: (-r["n_variables_no_confirmadas"], -r["antiguedad_total_anios"]))
    for i, r in enumerate(ranking, start=1):
        r["prioridad"] = i

    ruta_detalle = DIR_SALIDA / "auditoria_cobertura_detalle.csv"
    with ruta_detalle.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(detalle[0].keys()))
        writer.writeheader()
        writer.writerows(detalle)
    print(f"Guardado: {ruta_detalle} ({len(detalle)} filas)")

    ruta_ranking = DIR_SALIDA / "auditoria_cobertura_ranking.csv"
    campos_ranking = ["prioridad"] + [k for k in ranking[0].keys() if k != "prioridad"]
    with ruta_ranking.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos_ranking)
        writer.writeheader()
        writer.writerows(ranking)
    print(f"Guardado: {ruta_ranking} ({len(ranking)} filas)")

    if huerfanos:
        ruta_huerfanos = DIR_SALIDA / "auditoria_codigos_huerfanos.csv"
        with ruta_huerfanos.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["codigodivipola", "municipios"])
            writer.writeheader()
            writer.writerows({"codigodivipola": h["codigodivipola"], "municipios": h["municipios"]} for h in huerfanos)
        print(f"Guardado: {ruta_huerfanos} ({len(huerfanos)} filas)")

    # Resumen en consola, por variable
    print(f"\n=== Resumen por variable (de {len(municipios)} municipios) ===")
    for variable in VARIABLES:
        reales = sum(1 for d in detalle if d["variable"] == variable and d["estado"] == "real")
        proyectados = sum(1 for d in detalle if d["variable"] == variable and d["estado"] == "proyectado")
        sin_dato = sum(1 for d in detalle if d["variable"] == variable and d["estado"] == "sin_dato")
        print(f"  {variable:38} real={reales:4}  proyectado={proyectados:4}  sin_dato={sin_dato:4}")

    peores = ranking[:10]
    print(f"\n=== Top 10 municipios mas urgentes ===")
    for r in peores:
        print(f"  #{r['prioridad']:4} {r['nombre_municipio']:30} ({r['codigo_municipio']}) "
              f"no_confirmadas={r['n_variables_no_confirmadas']}/7  antiguedad_total={r['antiguedad_total_anios']}")


if __name__ == "__main__":
    main()
