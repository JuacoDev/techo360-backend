"""
Proyeccion de las 4 variables de indicadores que no tienen ninguna fuente
municipal real para anio=2024 (investigado a fondo, ver reporte de la
sesion 2026-09-09 -- ni GEIH ni ECV bajan de nivel departamental/regional,
y el CNPV 2018 no publica anexo estatico de tenencia de vivienda por
municipio). Regla de producto: rellenar con una proyeccion SIMPLE por
variable, marcada explicitamente como tal via originVar + intervaloConfianza
(reusado como "% de fiabilidad", no como intervalo estadistico riguroso),
NUNCA mezclada con dato real.

No es SAE ni ningun modelo estadistico formal -- decision consciente por
tiempo, ver aviso en ingestion/db.py.

============================================================
1) Deficit habitacional (cuantitativo, cualitativo, total)
============================================================
Base real: 1,122 filas municipales de yearm=2018 (DaneDeficitAdapter,
CNPV 2018, codigoDivipola real). Se extrapola cada valor municipal
multiplicandolo por la tasa de cambio NACIONAL 2018->2024 del mismo
componente:

    valor_2024_proyectado = valor_2018_real * (tasa_nacional_2024 / tasa_nacional_2018)

Tasas nacionales usadas (ambas DANE, "metodologia 2020" del deficit
habitacional):

    Fuente 2018 (censo): Boletin Tecnico Deficit Habitacional CNPV 2018
    https://www.dane.gov.co/files/investigaciones/deficit-habitacional/deficit-hab-2020-boletin.pdf
        Deficit cuantitativo: 5.0%
        Deficit cualitativo:  26.9%
        Deficit total:        31.9%

    Fuente 2024 (encuesta): Boletin Tecnico ECV 2024, seccion 1.2.2
    https://www.dane.gov.co/files/operaciones/ECV/bol-ECV-2024.pdf
        Deficit cuantitativo: 6.8%
        Deficit cualitativo:  20.0%
        Deficit total:        26.8%

>>> AVISO IMPORTANTE, no ocultar <<<
CNPV (censo, universo completo) y ECV (encuesta por muestreo) NO son
perfectamente comparables metodologicamente pese a compartir el nombre
"metodologia 2020" -- son operaciones estadisticas distintas (censo vs.
encuesta), con muestras/definiciones que no coinciden exactamente. Prueba
de esto: el componente CUANTITATIVO nacional SUBE de 5.0% (2018) a 6.8%
(2024) -- un resultado contraintuitivo si se interpretara como "el
deficit estructural empeoro en todo el pais", cuando el efecto mas
probable es la diferencia censo-vs-encuesta, no un deterioro real
generalizado. Se aplica la tasa tal cual (asi lo decidio el equipo,
sabiendo esto) porque es la unica fuente nacional comparable disponible
para 2024 -- pero por eso este metodo NO se etiqueta como dato real, y
la fiabilidad asignada (media-alta, no alta) refleja este limite
conocido, no solo el hecho de ser una extrapolacion.

Fiabilidad asignada: 70% -- parte de un dato municipal REAL (no
estimado) de 2018, y solo extrapola una tendencia agregada nacional
sobre esa base real; no es un dato observado, pero tampoco es una
estimacion sin ningun anclaje real.

Los valores proyectados se recortan a [0, 100] (son porcentajes) -- la
tasa de cuantitativo (x1.36) podria empujar a municipios que ya
partian de un valor 2018 alto por encima de 100 sin este recorte.

============================================================
2) Tenencia informal de terrenos
============================================================
Base real: 1,118 filas municipales de yearm=2026 (IGACCatastroAdapter,
snapshot en vivo del catastro, sin serie historica -- ver aviso extenso
en ingestion/db.py). Se investigo a fondo si existe algun punto de
referencia municipal para 2018 (censo CNPV, alguna encuesta, algun
registro administrativo) -- NO se encontro ninguno con el nivel de
certeza que este proyecto exige (el CNPV 2018 solo expone esto via un
sistema de consulta interactivo REDATAM, sin archivo estatico
descargable que se pudiera confirmar en este tiempo).

Sin un segundo punto real, no hay tendencia honesta que calcular --
inventar una tasa de cambio sin ningun ancla historica seria
exactamente el tipo de dato sintetico que este proyecto decidio evitar
desde el principio. Se reutiliza el valor 2026 tal cual para 2024,
etiquetado con fiabilidad BAJA (30%): es el mismo dato de HOY, no una
medicion de 2024, y se comunica asi explicitamente en el frontend.

Uso:
    docker exec techo360-backend python -m ingestion.proyeccion_2024
    docker exec techo360-backend python -m ingestion.proyeccion_2024 --dry-run
"""
import argparse
import logging
import sys

import psycopg2
import psycopg2.extras

from core.config import settings
from ingestion.base import IndicadorRecord
from ingestion.db import guardar_indicador

logger = logging.getLogger(__name__)

ANIO_BASE_DEFICIT = 2018
ANIO_BASE_TENENCIA = 2026
ANIO_PROYECTADO = 2024

ORIGEN_PROYECCION_DEFICIT = "proyeccion-tendencia-nacional-2018-2024"
ORIGEN_PROYECCION_TENENCIA = "proyeccion-snapshot-2026"

FIABILIDAD_DEFICIT = 70.0    # media-alta: parte de dato municipal real 2018
FIABILIDAD_TENENCIA = 30.0   # baja: mismo snapshot de hoy, sin ancla historica

# tasa = valor_nacional_2024 / valor_nacional_2018, ambas fuentes DANE
# (ver docstring del modulo para las fuentes exactas y el aviso sobre
# comparabilidad censo-vs-encuesta)
TASAS_NACIONALES_DEFICIT = {
    "deficit_habitacional_cuantitativo": 6.8 / 5.0,
    "deficit_habitacional_cualitativo": 20.0 / 26.9,
    "deficit_habitacional_total": 26.8 / 31.9,
}


def _filas_reales(cur, variable: str, anio: int):
    cur.execute(
        """
        SELECT codigodivipola, municipios, valuevar
        FROM mapcalordb
        WHERE namevar = %s AND yearm = %s AND codigodivipola IS NOT NULL
        """,
        (variable, anio),
    )
    return cur.fetchall()


def proyectar_deficit(database_url: str, dry_run: bool) -> tuple[int, int]:
    """Devuelve (proyectados, errores)."""
    conn = psycopg2.connect(database_url)
    proyectados = 0
    errores = 0
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            for variable, tasa in TASAS_NACIONALES_DEFICIT.items():
                filas = _filas_reales(cur, variable, ANIO_BASE_DEFICIT)
                logger.info(f"{variable}: {len(filas)} filas reales de {ANIO_BASE_DEFICIT} encontradas, tasa={tasa:.4f}")

                for fila in filas:
                    codigo = fila["codigodivipola"]
                    nombre = fila["municipios"]
                    valor_2018 = fila["valuevar"]
                    if valor_2018 is None:
                        continue

                    valor_proyectado = max(0.0, min(100.0, float(valor_2018) * tasa))

                    if dry_run:
                        proyectados += 1
                        continue

                    try:
                        record = IndicadorRecord(
                            codigo_divipola=codigo,
                            entidad_territorial=nombre,
                            variable=variable,
                            valor=round(valor_proyectado, 2),
                            anio=ANIO_PROYECTADO,
                            fuente=ORIGEN_PROYECCION_DEFICIT,
                            intervalo_confianza=FIABILIDAD_DEFICIT,
                        )
                        guardar_indicador(database_url, record)
                        proyectados += 1
                    except Exception as exc:
                        errores += 1
                        logger.warning(f"Error proyectando {variable} para {codigo} ({nombre}): {exc!r}")
    finally:
        conn.close()
    return proyectados, errores


def proyectar_tenencia(database_url: str, dry_run: bool) -> tuple[int, int]:
    conn = psycopg2.connect(database_url)
    proyectados = 0
    errores = 0
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            filas = _filas_reales(cur, "tenencia_informal_terrenos", ANIO_BASE_TENENCIA)
            logger.info(f"tenencia_informal_terrenos: {len(filas)} filas reales de {ANIO_BASE_TENENCIA} encontradas")

            for fila in filas:
                codigo = fila["codigodivipola"]
                nombre = fila["municipios"]
                valor = fila["valuevar"]
                if valor is None:
                    continue

                if dry_run:
                    proyectados += 1
                    continue

                try:
                    record = IndicadorRecord(
                        codigo_divipola=codigo,
                        entidad_territorial=nombre,
                        variable="tenencia_informal_terrenos",
                        valor=float(valor),
                        anio=ANIO_PROYECTADO,
                        fuente=ORIGEN_PROYECCION_TENENCIA,
                        intervalo_confianza=FIABILIDAD_TENENCIA,
                    )
                    guardar_indicador(database_url, record)
                    proyectados += 1
                except Exception as exc:
                    errores += 1
                    logger.warning(f"Error proyectando tenencia para {codigo} ({nombre}): {exc!r}")
    finally:
        conn.close()
    return proyectados, errores


def main(dry_run: bool) -> int:
    database_url = settings.database_url

    print("=== Deficit habitacional (3 variables, tendencia nacional 2018->2024) ===")
    p1, e1 = proyectar_deficit(database_url, dry_run)
    print(f"Proyectados: {p1}. Errores: {e1}.")

    print("\n=== Tenencia informal de terrenos (snapshot 2026 reetiquetado) ===")
    p2, e2 = proyectar_tenencia(database_url, dry_run)
    print(f"Proyectados: {p2}. Errores: {e2}.")

    if dry_run:
        print("\n--dry-run: no se escribio nada en la base de datos.")

    total_errores = e1 + e2
    print(f"\nTotal proyectados: {p1 + p2}. Total errores: {total_errores}.")
    return 1 if total_errores else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta, sin escribir en la DB.")
    args = parser.parse_args()
    sys.exit(main(args.dry_run))
