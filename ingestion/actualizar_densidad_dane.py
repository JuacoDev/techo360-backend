"""
Integracion de una vez (no job recurrente, mismo patron que
ingestion/reetiquetar_ultimo_valor_observado.py): reemplaza el origen
de densidad_poblacional para TODOS los municipios reales (no solo los
3 gaps) de "TerriData" a una fuente unica DANE, decision explicita del
usuario (Fase 2, Paso 1 -> implementacion) para no dejar la variable
dividida entre 2 origenes distintos segun que municipio TerriData
cubriera.

Formula: densidad_poblacional = poblacion_total_DANE_2024 /
area_km2(zonas_municipio.geom), area calculada via PostGIS
(ST_Area(geography(geom))/1.000.000) sobre la capa canonica del mapa --
la misma geometria contra la que ya se filtra "que municipio existe"
en toda la Fase 2 (ver ingestion/auditoria_cobertura_variables.py).

Por que DANE-poblacion y no seguir con TerriData: TerriData tiene un
hueco real y verificado para San Andres (codigo 88001, ver diagnostico
de la Fase 2) -- publica el archipielago SOLO como agregado
departamental (88000), nunca desagregado a nivel municipio para
densidad. DANE si trae San Andres y Providencia como municipios
propios, con serie 2018-2042 publicada 08-ago-2025 (mas reciente que
lo que el equipo pudo confirmar sobre el origen/vigencia real de
TerriData para esta variable).

originVar = "DANE-proyeccion-poblacion-2024" (nombre explicito con el
año real del dato usado -- la fuente en si se publico en ago-2025, pero
el AÑO DEL DATO que se usa aqui es 2024, para ser consistente con la
convencion ya establecida en el proyecto de nombrar el origen segun el
año real del dato, no el año de publicacion; ver "DANE-CNPV2018" como
precedente: el censo se publico oficialmente en 2020 pero el origen se
llama por el año del levantamiento, 2018).

Pasos, en orden, cada uno verificado antes de pasar al siguiente:
1. Cargar poblacion 2024 por municipio (DanePoblacionAdapter).
2. Cargar area_km2 por municipio via PostGIS (zonas_municipio.geom).
3. Calcular densidad = poblacion / area para los municipios donde
   ambos existen.
4. SANITY CHECK contra los valores TerriData ya existentes (yearm=2024,
   origen='TerriData') -- si la diferencia mediana es descabellada
   (multiples ordenes de magnitud), se ABORTA antes de escribir nada,
   no se asume que "el area debe estar mal calculada, seguimos igual".
5. Solo si el sanity check pasa: UPDATE via guardar_indicador (misma
   funcion ya usada en Fase 1, UPDATE por llave unica
   codigoDivipola+nameVar+yearM, nunca INSERT duplicado).

Uso:
    docker exec techo360-backend python -m ingestion.actualizar_densidad_dane --dry-run
    docker exec techo360-backend python -m ingestion.actualizar_densidad_dane
"""
import argparse
import logging
import statistics
import sys

import psycopg2
import psycopg2.extras

from core.config import settings
from ingestion.base import IndicadorRecord
from ingestion.dane_poblacion_adapter import DanePoblacionAdapter
from ingestion.db import guardar_indicador

logger = logging.getLogger(__name__)

ANIO_DATO = 2024
ORIGEN_NUEVO = "DANE-proyeccion-poblacion-2024"
VARIABLE = "densidad_poblacional"

# Umbral de sanity check: si la RATIO mediana (densidad_nueva /
# densidad_TerriData_vieja) para los municipios que YA tenian dato cae
# fuera de este rango, algo esta mal en el calculo de area y se aborta
# antes de escribir -- un area correcta deberia dar una densidad del
# mismo orden de magnitud que TerriData, no necesariamente identica
# (metodologias de area distintas), pero nunca 10x+ distinta en la
# mediana.
RATIO_MINIMA_ACEPTABLE = 0.5
RATIO_MAXIMA_ACEPTABLE = 2.0


def cargar_areas_km2(cur) -> dict[str, float]:
    cur.execute(
        "SELECT codigo_municipio, ST_Area(geography(geom)) / 1000000.0 AS area_km2 FROM zonas_municipio"
    )
    return {r["codigo_municipio"]: float(r["area_km2"]) for r in cur.fetchall() if r["area_km2"]}


def cargar_densidad_terridata(cur) -> dict[str, float]:
    cur.execute(
        """
        SELECT codigodivipola, valuevar FROM mapcalordb
        WHERE namevar = %s AND yearm = %s AND originvar = 'TerriData' AND codigodivipola IS NOT NULL
        """,
        (VARIABLE, ANIO_DATO),
    )
    return {r["codigodivipola"]: float(r["valuevar"]) for r in cur.fetchall()}


def calcular_densidades(poblacion: dict, areas: dict) -> dict[str, tuple[str, float]]:
    """Devuelve {codigo: (nombre, densidad)} solo donde hay poblacion Y area."""
    resultado = {}
    for codigo, (nombre, hab) in poblacion.items():
        area = areas.get(codigo)
        if area and area > 0:
            resultado[codigo] = (nombre, round(hab / area, 2))
    return resultado


def sanity_check(densidades_nuevas: dict, densidades_viejas: dict) -> tuple[bool, dict]:
    ratios = []
    for codigo, densidad_vieja in densidades_viejas.items():
        entrada_nueva = densidades_nuevas.get(codigo)
        if entrada_nueva and densidad_vieja > 0:
            ratios.append(entrada_nueva[1] / densidad_vieja)

    if not ratios:
        return False, {"error": "no hay municipios en comun para comparar"}

    mediana = statistics.median(ratios)
    resumen = {
        "n_comparados": len(ratios),
        "ratio_mediana": round(mediana, 3),
        "ratio_p10": round(sorted(ratios)[len(ratios) // 10], 3),
        "ratio_p90": round(sorted(ratios)[len(ratios) * 9 // 10], 3),
    }
    ok = RATIO_MINIMA_ACEPTABLE <= mediana <= RATIO_MAXIMA_ACEPTABLE
    return ok, resumen


def main(dry_run: bool) -> int:
    database_url = settings.database_url

    print("Descargando/parseando poblacion DANE 2024...")
    adapter = DanePoblacionAdapter()
    poblacion = adapter.poblacion_por_municipio(ANIO_DATO)
    print(f"  {len(poblacion)} municipios con poblacion DANE 2024.")

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            print("Calculando areas via PostGIS...")
            areas = cargar_areas_km2(cur)
            print(f"  {len(areas)} municipios con area calculada.")

            print("Cargando densidades TerriData existentes para sanity check...")
            densidades_viejas = cargar_densidad_terridata(cur)
            print(f"  {len(densidades_viejas)} municipios con densidad TerriData (yearm={ANIO_DATO}).")

        densidades_nuevas = calcular_densidades(poblacion, areas)
        # Solo los municipios reales de zonas_municipio (via areas, que
        # ya viene de esa tabla) -- los ~18 codigos huerfanos de DANE
        # (corregimientos sin match en la capa IGAC) quedan excluidos
        # naturalmente porque no tienen entrada en `areas`.
        print(f"\nDensidades calculadas (poblacion DANE / area PostGIS): {len(densidades_nuevas)} municipios.")

        ok, resumen_sanity = sanity_check(densidades_nuevas, densidades_viejas)
        print(f"\n=== Sanity check (ratio nueva/vieja para municipios en comun) ===")
        print(f"  {resumen_sanity}")
        if not ok:
            print(f"\nABORTADO: la mediana de ratios ({resumen_sanity.get('ratio_mediana')}) esta fuera del "
                  f"rango aceptable [{RATIO_MINIMA_ACEPTABLE}, {RATIO_MAXIMA_ACEPTABLE}]. "
                  f"No se escribio nada -- revisar el calculo de area antes de reintentar.")
            return 1
        print("  OK -- dentro del rango aceptable, se procede.")

        # Los 3 gaps de la auditoria: confirmar cuales quedan cerrados.
        gaps_conocidos = {"00000": "Area En Litigio Cauca - Huila", "27493": "Nuevo Belen De Bajira", "88001": "San Andres"}
        print(f"\n=== Estado de los 3 gaps conocidos ===")
        cerrados = 0
        for codigo, nombre in gaps_conocidos.items():
            entrada = densidades_nuevas.get(codigo)
            if entrada:
                print(f"  {codigo} ({nombre}): CERRADO -- densidad={entrada[1]} hab/km2 (poblacion={poblacion.get(codigo, (None, None))[1]})")
                cerrados += 1
            else:
                razon = "sin fila en DANE" if codigo not in poblacion else "sin area en zonas_municipio"
                print(f"  {codigo} ({nombre}): SIGUE SIN DATO ({razon})")
        print(f"  Total cerrados: {cerrados}/3")

        if dry_run:
            print(f"\n--dry-run: se hubieran escrito {len(densidades_nuevas)} filas. No se escribio nada.")
            return 0

        print(f"\nEscribiendo {len(densidades_nuevas)} filas (origen='{ORIGEN_NUEVO}')...")
        escritas = 0
        errores = 0
        for codigo, (nombre, densidad) in densidades_nuevas.items():
            try:
                record = IndicadorRecord(
                    codigo_divipola=codigo,
                    entidad_territorial=nombre,
                    variable=VARIABLE,
                    valor=densidad,
                    anio=ANIO_DATO,
                    fuente=ORIGEN_NUEVO,
                )
                guardar_indicador(database_url, record)
                escritas += 1
            except Exception as exc:
                errores += 1
                logger.warning(f"Error escribiendo {codigo} ({nombre}): {exc!r}")

        print(f"\nEscritas: {escritas}. Errores: {errores}.")
        return 1 if errores else 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta, sin escribir en la DB.")
    args = parser.parse_args()
    sys.exit(main(args.dry_run))
