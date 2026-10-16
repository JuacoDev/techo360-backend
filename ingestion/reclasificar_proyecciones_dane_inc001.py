"""
Correccion de una vez (no job recurrente, NO va al scheduler): INC-001.

Problema (COMPROBADO en el informe 1.1 de Fase 1, anomalias A1/A2): las filas
de densidad_poblacional de los anios 2027-2042 tienen originVar =
"DANE-proyeccion-poblacion-{anio}" (1 104 filas por anio). Esos anios son
PROYECCIONES de poblacion del DANE, pero services/origen.py las clasifica
como dato real porque el nombre NO empieza por "proyeccion-" (ver
es_proyeccion()). El frontend las muestra entonces en "Fuentes confirmadas"
con fiabilidad 100.

Que hace este script: cambia SOLO la columna originVar de esas filas, de
"DANE-proyeccion-poblacion-{anio}" a "proyeccion-DANE-proyeccion-poblacion-
{anio}", usando el UPDATE por llave unica codigoDivipola+nameVar+yearM (la
misma llave que usa guardar_indicador() en ingestion/db.py).

>>> POR QUE NO SE USA guardar_indicador() AQUI <<<
guardar_indicador() reescribe valueVar y intervaloConfianza y, si el UPDATE
no toca ninguna fila, hace INSERT. Para un re-etiquetado eso es riesgo sin
beneficio: se pasaria el valor por un float de Python (el ruido de precision
de INC-002 ya demostro que los valores de origen deben conservarse tal cual)
y un codigo mal formado podria insertar una fila nueva. Este script hace un
UPDATE que solo asigna originVar, exige rowcount == 1 por fila y NUNCA hace
INSERT.

>>> DECISIONES PENDIENTES DE JUAN (no resueltas por este script) <<<
1. Nombre nuevo del origen (PREFIJO_NUEVO + origen viejo). Es una propuesta.
2. intervaloConfianza: este script NO lo toca. Esas filas lo tienen NULL
   (dato "real"); como proyeccion, fiabilidad_de_origen() devolveria el minimo
   por defecto (30). Si se quiere otra fiabilidad, es otra correccion.
3. Alcance de anios: solo 2027-2042. 2024, 2025 y 2026 quedan como estan.
4. Hay tests y comentarios que fijan que "DANE-proyeccion-poblacion-*" es dato
   real por diseno (services/tests_*, techo360-proyeccion/tests, frontend
   variablesProyeccion.test.js). Esos tests cambian si se ejecuta esto.
5. ingestion/actualizar_densidad_dane.py::origen_para() volveria a escribir el
   nombre viejo si se re-ejecutara: hay que actualizarlo en el mismo cambio.
6. Alternativa sin escritura en la base: cambiar la regla de es_proyeccion()
   en services/origen.py (y su espejo en techo360-proyeccion/app/origen.py).

Salvaguardas (patron de actualizar_densidad_dane.py):
- --dry-run es el modo POR DEFECTO. Se ejecuta con sesion de solo lectura
  (default_transaction_read_only = on, verificada con SHOW antes y despues).
- Escribir exige --ejecutar, --respaldo RUTA (archivo existente, no vacio, y si
  es .gz se lee completo para validarlo) y --autorizo-paso TEXTO (se registra).
- Umbral de aborto: por anio se esperan 1 104 filas con tolerancia del 1 %;
  cualquier anio fuera de rango, un codigoDivipola NULL, llaves duplicadas, o
  filas que YA tengan el origen nuevo abortan antes de escribir.
- Checksum sha256 ANTES y DESPUES de (a) todas las filas que NO son candidatas
  (deben quedar identicas) y (b) las filas candidatas SIN contar originVar
  (valueVar e intervaloConfianza deben quedar identicos). Si algo difiere, se
  hace ROLLBACK; el COMMIT es lo ultimo.
- --revertir invierte el mapeo (mismas salvaguardas) para la reversa.
- --preimagen RUTA guarda un CSV con el estado previo de las filas candidatas.

Uso (dentro del contenedor, igual que los demas scripts de ingestion/):
    python -m ingestion.reclasificar_proyecciones_dane_inc001            # dry-run
    python -m ingestion.reclasificar_proyecciones_dane_inc001 --preimagen pre.csv
    python -m ingestion.reclasificar_proyecciones_dane_inc001 --ejecutar \\
        --respaldo /ruta/respaldo.sql.gz --autorizo-paso "paso X"
"""
import argparse
import csv
import gzip
import hashlib
import logging
import os
import sys

import psycopg2
import psycopg2.extras

from core.config import settings

logger = logging.getLogger(__name__)

VARIABLE = "densidad_poblacional"
ANIO_DESDE = 2027
ANIO_HASTA = 2042
PREFIJO_VIEJO = "DANE-proyeccion-poblacion-"
PREFIJO_NUEVO = "proyeccion-"  # PENDIENTE DE DECISION (ver docstring, punto 1)

FILAS_POR_ANIO_ESPERADAS = 1104  # REPORTADO en el informe 1.1 (no medido por este script antes de correr)
UMBRAL_DESVIO = 0.01


def origen_viejo(anio: int) -> str:
    return f"{PREFIJO_VIEJO}{anio}"


def origen_nuevo(anio: int) -> str:
    return f"{PREFIJO_NUEVO}{origen_viejo(anio)}"


def _sha256_filas(filas) -> str:
    h = hashlib.sha256()
    for fila in filas:
        h.update(("|".join("" if v is None else str(v) for v in fila) + "\n").encode("utf-8"))
    return h.hexdigest()


def verificar_solo_lectura(cur, etiqueta: str) -> None:
    cur.execute("SHOW default_transaction_read_only")
    default_ro = cur.fetchone()[0]
    cur.execute("SHOW transaction_read_only")
    tx_ro = cur.fetchone()[0]
    print(f"  [{etiqueta}] default_transaction_read_only={default_ro} transaction_read_only={tx_ro}")
    if default_ro != "on":
        raise RuntimeError("La sesion de dry-run NO esta en solo lectura; se aborta.")


def cargar_candidatas(cur, origen_de):
    anios = list(range(ANIO_DESDE, ANIO_HASTA + 1))
    origenes = [origen_de(a) for a in anios]
    cur.execute(
        """
        SELECT id, codigodivipola, namevar, yearm, originvar, valuevar::text AS valuevar,
               intervaloconfianza::text AS intervaloconfianza
        FROM mapcalordb
        WHERE namevar = %s AND yearm BETWEEN %s AND %s AND originvar = ANY(%s)
        ORDER BY yearm, codigodivipola, id
        """,
        (VARIABLE, ANIO_DESDE, ANIO_HASTA, origenes),
    )
    return cur.fetchall()


def checksum_resto(cur, ids_candidatas) -> str:
    """sha256 de TODAS las filas que no son candidatas (deben quedar identicas)."""
    with cur.connection.cursor(name="chk_resto") as ncur:
        ncur.itersize = 20000
        ncur.execute(
            """
            SELECT id, codigodivipola, namevar, yearm, originvar, valuevar::text, intervaloconfianza::text
            FROM mapcalordb
            WHERE NOT (id = ANY(%s))
            ORDER BY id
            """,
            (list(ids_candidatas),),
        )
        h = hashlib.sha256()
        for fila in ncur:
            h.update(("|".join("" if v is None else str(v) for v in fila) + "\n").encode("utf-8"))
    return h.hexdigest()


def checksum_candidatas_sin_origen(filas) -> str:
    return _sha256_filas(
        (f["id"], f["codigodivipola"], f["namevar"], f["yearm"], f["valuevar"], f["intervaloconfianza"])
        for f in sorted(filas, key=lambda f: f["id"])
    )


def validar(filas, cur, origen_a) -> dict:
    """Aborta (RuntimeError) ante cualquier senal de que el estado no es el verificado."""
    por_anio = {}
    for f in filas:
        por_anio[f["yearm"]] = por_anio.get(f["yearm"], 0) + 1

    minimo = int(FILAS_POR_ANIO_ESPERADAS * (1 - UMBRAL_DESVIO))
    maximo = int(FILAS_POR_ANIO_ESPERADAS * (1 + UMBRAL_DESVIO)) + 1
    for anio in range(ANIO_DESDE, ANIO_HASTA + 1):
        n = por_anio.get(anio, 0)
        if not (minimo <= n <= maximo):
            raise RuntimeError(
                f"ABORTADO: anio {anio} tiene {n} filas candidatas; se esperaban {FILAS_POR_ANIO_ESPERADAS} "
                f"(rango aceptado {minimo}-{maximo})."
            )

    sin_codigo = [f["id"] for f in filas if not (f["codigodivipola"] and f["codigodivipola"].strip())]
    if sin_codigo:
        raise RuntimeError(f"ABORTADO: {len(sin_codigo)} filas candidatas sin codigoDivipola (llave unica no usable).")

    llaves = [(f["codigodivipola"], f["namevar"], f["yearm"]) for f in filas]
    if len(llaves) != len(set(llaves)):
        raise RuntimeError("ABORTADO: hay llaves (codigoDivipola, nameVar, yearM) duplicadas entre las candidatas.")

    destinos = [origen_a(a) for a in range(ANIO_DESDE, ANIO_HASTA + 1)]
    cur.execute("SELECT count(*) FROM mapcalordb WHERE originvar = ANY(%s)", (destinos,))
    ya_destino = cur.fetchone()[0]
    if ya_destino:
        raise RuntimeError(f"ABORTADO: ya existen {ya_destino} filas con el origen destino; el estado no es el esperado.")
    return por_anio


def validar_respaldo(ruta: str) -> None:
    if not ruta or not os.path.isfile(ruta) or os.path.getsize(ruta) == 0:
        raise RuntimeError("--respaldo debe apuntar a un archivo existente y no vacio.")
    if ruta.endswith(".gz"):
        with gzip.open(ruta, "rb") as fh:
            while fh.read(1024 * 1024):
                pass
    sha = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            sha.update(bloque)
    print(f"  respaldo OK: {ruta} ({os.path.getsize(ruta)} bytes) sha256={sha.hexdigest()}")


def guardar_preimagen(filas, ruta: str) -> None:
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "codigodivipola", "namevar", "yearm", "originvar", "valuevar", "intervaloconfianza"])
        for f in filas:
            w.writerow([f["id"], f["codigodivipola"], f["namevar"], f["yearm"], f["originvar"], f["valuevar"], f["intervaloconfianza"]])
    print(f"  pre-imagen guardada: {ruta} ({len(filas)} filas)")


def main(args) -> int:
    revertir = args.revertir
    origen_de = origen_nuevo if revertir else origen_viejo
    origen_a = origen_viejo if revertir else origen_nuevo
    ejecutar = args.ejecutar

    print(f"=== INC-001: {'REVERSA' if revertir else 'reclasificacion'} de {VARIABLE} {ANIO_DESDE}-{ANIO_HASTA} ===")
    print(f"Ejemplo: {origen_de(2027)!r} -> {origen_a(2027)!r}")
    print(f"Modo: {'EJECUTAR' if ejecutar else 'DRY-RUN (solo lectura)'}")

    if ejecutar:
        if not args.autorizo_paso:
            raise RuntimeError("--ejecutar exige --autorizo-paso con el texto de la autorizacion de Juan.")
        validar_respaldo(args.respaldo)
        print(f"  autorizacion registrada: {args.autorizo_paso!r}")

    opciones = None if ejecutar else "-c default_transaction_read_only=on"
    conn = psycopg2.connect(settings.database_url, options=opciones) if opciones else psycopg2.connect(settings.database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            if not ejecutar:
                verificar_solo_lectura(cur, "antes")

            filas = cargar_candidatas(cur, origen_de)
            por_anio = validar(filas, cur, origen_a)
            ids = [f["id"] for f in filas]

            print(f"\nFilas candidatas: {len(filas)} (esperado aprox. {FILAS_POR_ANIO_ESPERADAS * (ANIO_HASTA - ANIO_DESDE + 1)})")
            for anio in sorted(por_anio):
                print(f"  {anio}: {por_anio[anio]} filas  {origen_de(anio)!r} -> {origen_a(anio)!r}")

            antes_resto = checksum_resto(cur, ids)
            antes_cand = checksum_candidatas_sin_origen(filas)
            print("\nChecksum ANTES")
            print(f"  filas NO candidatas        sha256 = {antes_resto}")
            print(f"  candidatas sin originVar   sha256 = {antes_cand}")

            if args.preimagen:
                guardar_preimagen(filas, args.preimagen)

            if not ejecutar:
                verificar_solo_lectura(cur, "despues")
                print(f"\n--dry-run: {len(filas)} filas CAMBIARIAN de originVar. No se escribio nada.")
                conn.rollback()
                return 0

            actualizadas = 0
            for f in filas:
                cur.execute(
                    """
                    UPDATE mapcalordb SET originvar = %s
                    WHERE codigodivipola = %s AND namevar = %s AND yearm = %s AND originvar = %s
                    """,
                    (origen_a(f["yearm"]), f["codigodivipola"], f["namevar"], f["yearm"], f["originvar"]),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"ABORTADO: UPDATE afecto {cur.rowcount} filas (esperaba 1) para id={f['id']}.")
                actualizadas += 1

            despues_filas = cargar_candidatas(cur, origen_a)
            if len(despues_filas) != len(filas):
                raise RuntimeError(f"ABORTADO: tras el UPDATE hay {len(despues_filas)} filas con el origen destino, esperaba {len(filas)}.")
            despues_resto = checksum_resto(cur, ids)
            despues_cand = checksum_candidatas_sin_origen(despues_filas)
            print("\nChecksum DESPUES (dentro de la transaccion, antes del COMMIT)")
            print(f"  filas NO candidatas        sha256 = {despues_resto}")
            print(f"  candidatas sin originVar   sha256 = {despues_cand}")
            if despues_resto != antes_resto or despues_cand != antes_cand:
                raise RuntimeError("ABORTADO: un checksum cambio; se hace ROLLBACK, no se confirma nada.")

            conn.commit()
            print(f"\nCOMMIT: {actualizadas} filas actualizadas. Checksums identicos antes/despues.")
            return 0
    except Exception as exc:
        conn.rollback()
        print(f"\n{exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ejecutar", action="store_true", help="Escribe en la DB. Sin esta bandera siempre es dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Explicito; es el modo por defecto.")
    parser.add_argument("--revertir", action="store_true", help="Invierte el mapeo (reversa).")
    parser.add_argument("--respaldo", help="Ruta del respaldo previo (obligatorio con --ejecutar).")
    parser.add_argument("--autorizo-paso", help="Texto de la autorizacion de Juan (obligatorio con --ejecutar).")
    parser.add_argument("--preimagen", help="Ruta de un CSV con el estado previo de las filas candidatas.")
    args = parser.parse_args()
    if args.ejecutar and args.dry_run:
        parser.error("--ejecutar y --dry-run son excluyentes.")
    try:
        sys.exit(main(args))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(1)
