"""
Correccion de una vez (no job recurrente): re-etiqueta las filas de
relleno "ultimo-valor-observado" (cobertura_acueducto/alcantarillado,
yearm=2025 y 2026, ver techo360-proyeccion/scripts/
rellenar_baseline_2025_2026.py, rama Testing, ejecutado 30-ago-2026)
para que el prefijo del origen empiece con "proyeccion-" -- igual que
"proyeccion-tendencia-nacional-2018-2024" y "proyeccion-snapshot-2026"
(ver ingestion/proyeccion_2024.py) -- y clasifiquen correctamente en el
frontend (PanelIndicadores.jsx usa esProyeccion() sobre ese prefijo).

Por que hacia falta: sin el prefijo, estas filas se mostraban en
"Fuentes confirmadas" en vez de "Proyecciones" -- inconsistente con el
resto del sistema, que SI marca cualquier dato de relleno/proyeccion
como tal. Root cause, no solo el sintoma: el origen se eligio antes de
que existiera la convencion del prefijo "proyeccion-" (Sprint 3 vs.
2026-09-09), nunca se retro-aplico.

Alcance real verificado en vivo contra produccion (22-sep-2026, no
asumido): las UNICAS filas con originvar='ultimo-valor-observado' estan
en yearm=2025 (980 acueducto + 979 alcantarillado) y yearm=2026 (980 +
979), ambas ancladas al mismo ANIO_BASE=2024 real de TerriData (fijo,
no varia por municipio -- ver script generador). No existen filas de
este origen en yearm=2024 (el unico anio que pide hoy el frontend via
ANIO_INDICADORES en api.js), asi que esta correccion es preventiva --
no corrige un dato mal mostrado a usuarios reales HOY, corrige el
momento en que ANIO_INDICADORES deje de estar fijo en 2024 (comentario
explicito en api.js: "pendiente de decision").

Formula de fiabilidad dinamica (decision metodologica 22-sep-2026):

    fiabilidad(antiguedad) = max(30, 60 - 10 * (antiguedad - 1))
    antiguedad = yearm_de_la_fila - anio_del_dato_real (2024, fijo hoy)

    antiguedad=1 (filas yearm=2025): 60%
    antiguedad=2 (filas yearm=2026): 50%
    antiguedad=3: 40% -- antiguedad>=4: piso 30%

Por que 60% de arranque (no 70%, no 30%): se ubica deliberadamente
entre los dos precedentes ya documentados en ingestion/proyeccion_2024.py.
Por debajo del 70% de deficit_habitacional (que extrapola una TENDENCIA
real entre 2 anclas de 6 anios de separacion) porque este metodo es mas
simple -- repetir el ultimo valor real sin ningun ajuste. Por encima del
30% de tenencia_informal_terrenos (que reusa un snapshot sin NINGUN
ancla temporal) porque este metodo si tiene un ancla real reciente (el
propio valor de 2024, no un dato de otro periodo).

Por que -10/anio y piso en 30%: RMSE ya documentado en
techo360-proyeccion (test set 2024, prediccion=lag_1) es 15.56
(acueducto) / 13.89 (alcantarillado) a 1 anio de antiguedad -- error
real, no cero. Verificado ademas en vivo contra produccion (22-sep-2026,
RMSE recalculado con pares reales de anios distintos, metodologia
propia, no identica a la del repo de proyeccion): el error crece con la
antiguedad pero de forma RUIDOSA, no perfectamente lineal (gap=1:~19,
gap=2:~23, gap=3:~32, gap=4:~28 -- no monotono). La caida de 10 puntos
por anio es del mismo orden de magnitud que ese crecimiento real
observado (~20-25% de RMSE adicional por anio de gap), pero es una
simplificacion lineal razonada, NO un ajuste estadistico riguroso sobre
una curva que el propio dato real demuestra que no es lineal. El piso
de 30% reusa literalmente FIABILIDAD_TENENCIA (mismo suelo semantico
"baja confianza" en todo el sistema).

>>> LIMITACION CONOCIDA, no ocultar <<<
No existe en MapCalorDB ninguna columna que registre "de que anio real
proviene este valor de relleno" -- la antiguedad de cada fila se deriva
aqui de ANIO_DATO_REAL (constante fija = 2024, confirmada en vivo como
el unico anio base real usado por TODAS las filas de este origen hoy),
no de un campo propio por fila. Si en el futuro se generan mas filas
"ultimo-valor-observado" con un anio base distinto (ej. corriendo de
nuevo rellenar_baseline_2025_2026.py con otro ANIO_BASE), este script
NO las cubre automaticamente sin ajustar ANIO_DATO_REAL a mano -- no es
un mecanismo generico por-fila, es una correccion puntual sobre el
estado real ya verificado de la base al 22-sep-2026.

Solo toca originVar e intervaloConfianza (via guardar_indicador(), que
actualiza por la llave unica codigoDivipola+nameVar+yearM) -- valueVar
se reenvia SIN CAMBIOS (mismo valor leido, no recalculado), por lo que
el calculo del Score de Privacion (routers/zonas.py, que solo lee
valueVar/score_normalizado, nunca origen/intervaloConfianza) queda
intacto. Verificado en vivo antes y despues de correr esta correccion
(ver README/decisions o el reporte de consola de esta misma corrida).

Uso:
    docker exec techo360-backend python -m ingestion.reetiquetar_ultimo_valor_observado
    docker exec techo360-backend python -m ingestion.reetiquetar_ultimo_valor_observado --dry-run
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

ORIGEN_VIEJO = "ultimo-valor-observado"
VARIABLES = ("cobertura_acueducto", "cobertura_alcantarillado")

ANIO_DATO_REAL = 2024  # ver aviso de limitacion conocida en el docstring del modulo

FIABILIDAD_INICIAL = 60.0
FIABILIDAD_DECAIMIENTO_POR_ANIO = 10.0
FIABILIDAD_PISO = 30.0


def calcular_fiabilidad(antiguedad: int) -> float:
    return max(FIABILIDAD_PISO, FIABILIDAD_INICIAL - FIABILIDAD_DECAIMIENTO_POR_ANIO * (antiguedad - 1))


def nuevo_origen(anio_dato_real: int) -> str:
    return f"proyeccion-ultimo-valor-observado-{anio_dato_real}"


def _filas_a_corregir(cur, variable: str):
    cur.execute(
        """
        SELECT codigodivipola, municipios, yearm, valuevar
        FROM mapcalordb
        WHERE namevar = %s AND originvar = %s
        ORDER BY yearm, municipios
        """,
        (variable, ORIGEN_VIEJO),
    )
    return cur.fetchall()


def reetiquetar(database_url: str, dry_run: bool) -> tuple[int, int]:
    """Devuelve (corregidas, errores)."""
    conn = psycopg2.connect(database_url)
    corregidas = 0
    errores = 0
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            for variable in VARIABLES:
                filas = _filas_a_corregir(cur, variable)
                logger.info(f"{variable}: {len(filas)} filas con origen='{ORIGEN_VIEJO}' encontradas")

                for fila in filas:
                    antiguedad = fila["yearm"] - ANIO_DATO_REAL
                    fiabilidad = calcular_fiabilidad(antiguedad)
                    origen = nuevo_origen(ANIO_DATO_REAL)

                    if dry_run:
                        corregidas += 1
                        continue

                    try:
                        record = IndicadorRecord(
                            codigo_divipola=fila["codigodivipola"],
                            entidad_territorial=fila["municipios"],
                            variable=variable,
                            valor=float(fila["valuevar"]),  # SIN CAMBIOS -- mismo valor leido
                            anio=fila["yearm"],
                            fuente=origen,
                            intervalo_confianza=fiabilidad,
                        )
                        resultado = guardar_indicador(database_url, record)
                        if resultado != "actualizado":
                            # No deberia insertar nunca -- estas filas ya existen.
                            # Si esto pasa, algo cambio respecto al estado verificado
                            # (ej. un codigo_divipola distinto) y hay que investigar,
                            # no seguir de largo silenciosamente.
                            logger.warning(
                                f"Esperaba UPDATE pero guardar_indicador devolvio "
                                f"'{resultado}' para {fila['municipios']} / {variable} / {fila['yearm']}"
                            )
                        corregidas += 1
                    except Exception as exc:
                        errores += 1
                        logger.warning(
                            f"Error re-etiquetando {variable} / {fila['municipios']} / {fila['yearm']}: {exc!r}"
                        )
    finally:
        conn.close()
    return corregidas, errores


def main(dry_run: bool) -> int:
    database_url = settings.database_url

    print(f"=== Re-etiquetado '{ORIGEN_VIEJO}' -> 'proyeccion-{ORIGEN_VIEJO}-{{anio}}' ===")
    print(f"Formula: fiabilidad = max({FIABILIDAD_PISO}, {FIABILIDAD_INICIAL} - {FIABILIDAD_DECAIMIENTO_POR_ANIO} * (antiguedad-1))")
    print(f"anio 2025 (antiguedad=1) -> fiabilidad={calcular_fiabilidad(1)}%")
    print(f"anio 2026 (antiguedad=2) -> fiabilidad={calcular_fiabilidad(2)}%\n")

    corregidas, errores = reetiquetar(database_url, dry_run)

    if dry_run:
        print(f"\n--dry-run: {corregidas} filas SE HUBIERAN corregido, {errores} errores. No se escribio nada.")
    else:
        print(f"\nCorregidas: {corregidas}. Errores: {errores}.")

    return 1 if errores else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta, sin escribir en la DB.")
    args = parser.parse_args()
    sys.exit(main(args.dry_run))
