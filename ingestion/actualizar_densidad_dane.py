"""
Integracion de una vez (no job recurrente, mismo patron que
ingestion/reetiquetar_ultimo_valor_observado.py): reemplaza el origen
de densidad_poblacional para TODOS los municipios reales (no solo los
3 gaps) de "TerriData" a una fuente unica DANE, decision explicita del
usuario (Fase 2, Paso 1 -> implementacion) para no dejar la variable
dividida entre 2 origenes distintos segun que municipio TerriData
cubriera.

Formula: densidad_poblacional = poblacion_total_DANE_{anio} /
area_km2(zonas_municipio.geom), area calculada via PostGIS
(ST_Area(geography(geom))/1.000.000) sobre la capa canonica del mapa --
la misma geometria contra la que ya se filtra "que municipio existe"
en toda la Fase 2 (ver ingestion/auditoria_cobertura_variables.py). El
area NO depende del año (la geometria municipal no cambia), asi que se
calcula una sola vez y se reutiliza para todos los años del rango.

Por que DANE-poblacion y no seguir con TerriData: TerriData tiene un
hueco real y verificado para San Andres (codigo 88001, ver diagnostico
de la Fase 2) -- publica el archipielago SOLO como agregado
departamental (88000), nunca desagregado a nivel municipio para
densidad. DANE si trae San Andres y Providencia como municipios
propios, con serie 2018-2042 publicada 08-ago-2025 (mas reciente que
lo que el equipo pudo confirmar sobre el origen/vigencia real de
TerriData para esta variable).

>>> INC-001 (16/10/2026) -- CAMBIO DE ORIGEN, leer antes de ejecutar <<<
Este script escribia originVar = "DANE-proyeccion-poblacion-{anio}" y
intervaloConfianza = NULL, y esas filas se tomaban por dato REAL. Es
PROYECCION (poblacion proyectada DANE / area). Ahora escribe, para TODOS
los años (2024-2042),

    originVar = "proyeccion-poblacion-sobre-area"   (prefijo "proyeccion-" + metodo,
                                                      sin el nombre de la fuente ni el año)
    intervaloConfianza = 30                          (fiabilidad 0-100, constante)

que es lo que ya quedo en mapcalordb tras el UPDATE autorizado del 16/10/2026
(19.874 filas; ver claude/fase3-inc001-ejecucion.md). El año ya esta en yearM,
asi que el nombre no lo repite. El ancla del sanity check (año previo) se busca
con ese mismo origen. Este script NO se ejecuto al cambiarlo.

Texto original (historico, ya NO vigente): originVar = "DANE-proyeccion-poblacion-
{anio}" (nombre explicito con el año real del dato usado, por analogia con
"DANE-CNPV2018": el censo se publico en 2020 pero el origen se llama por el año del
levantamiento, 2018).

===========================================================================
EXTENSION 2025-2042 (24-sep-2026) -- de un solo año (2024) a un rango
===========================================================================
La primera version de este script solo cargaba ANIO_DATO=2024 (single
año, hardcodeado). Se generaliza a un RANGO de años via
--anio-inicio/--anio-fin (default 2025-2042, el resto del periodo que
publica el anexo DANE y que 2024 no cubria) en vez de crear 18 scripts
casi identicos -- la logica de calculo (poblacion/area) y de escritura
(guardar_indicador) es identica para cualquier año, lo unico que cambia
es el año consultado en DanePoblacionAdapter.poblacion_por_municipio().
2024 sigue siendo cargable con --anio-inicio 2024 --anio-fin 2024 si
hiciera falta re-ejecutarlo, sin caso especial en el codigo.

>>> AVISO IMPORTANTE, no ocultar -- hallazgo del diagnostico previo a
>>> esta extension (24-sep-2026) <<<
Antes de extender, se diagnostico el estado real de originVar para
densidad_poblacional en produccion:

    originVar                         yearM min/max   filas   municipios
    TerriData                         2000-2026        28624    1103
    DANE-proyeccion-poblacion-2024    2024-2024         1104    1104
    TerriData-proyeccion              2027-2042        17648    1103

"TerriData-proyeccion" es un origen que YA EXISTE en produccion para
2027-2042, pero es HUERFANO: no hay ningun codigo, en ningun commit, de
NINGUNO de los 5 repos del monorepo (backend, frontend, chatbot,
proyeccion, infra, historial completo con `git log --all -S`), ni en
techo360-infra, ni en el repo huerfano techo360-ai-service (ya no
existe ni localmente ni en GitHub, confirmado), que lo haya generado.
Ademas tiene intervaloConfianza=NULL en el 100% de sus filas, lo cual
contradice la convencion documentada mas abajo en este modulo (las
filas de relleno/proyeccion deberian llevar un valor de fiabilidad, no
NULL) -- es tratado como si fuera un dato real, sin serlo.

guardar_indicador() (ver ingestion/db.py) hace el UPDATE por clave
codigoDivipola + nameVar + yearM -- SIN incluir originVar en el WHERE.
Esto significa que escribir "DANE-proyeccion-poblacion-{anio}" para
2027-2042 no coexistiria con "TerriData-proyeccion": LA SOBRESCRIBIRIA
en el sitio (mismo codigo+variable+año, cambia origen y valor). Esa
decision (reemplazar el origen huerfano) todavia no esta confirmada por
el usuario -- por eso este script NO escribe para 2027-2042 salvo que
se pase explicitamente --confirmar-sobrescribir-terridata-proyeccion.
Sin ese flag, el rango de escritura real se recorta automaticamente a
los años que no chocan con filas huerfanas (2025-2026, que ya
reemplazan a "TerriData" real -- mismo patron ya aprobado que 2024).
--dry-run SI reporta el rango completo pedido, con el numero exacto de
filas que se sobrescribirian en cada año, para decidir con el dato
delante.

Pasos, en orden, cada uno verificado antes de pasar al siguiente:
1. Cargar poblacion por municipio para cada año del rango (DanePoblacionAdapter;
   el adaptador cachea la descarga del xlsx, un solo archivo sirve para
   todos los años).
2. Cargar area_km2 por municipio via PostGIS (zonas_municipio.geom) -- una
   sola vez, no depende del año.
3. Calcular densidad = poblacion / area para los municipios donde
   ambos existen, para cada año del rango.
4. SANITY CHECK por año:
   - 2024, 2025, 2026 (los años donde TerriData tiene dato real, ver
     tabla del diagnostico arriba): ratio nueva/TerriData, mismo umbral
     [0.5, 2.0] que la version original.
   - 2027-2042 (sin ancla TerriData real): ratio contra la densidad
     DANE del año inmediatamente anterior YA CALCULADA en esta misma
     corrida (encadenado desde 2026) -- la poblacion cambia de forma
     gradual año a año, asi que un salto brusco de un año al siguiente
     indica un problema de parseo del xlsx, no una tendencia real.
     Mismo umbral [0.5, 2.0] por consistencia, aunque en la practica un
     año real nunca deberia acercarse a ese rango.
   Si CUALQUIER año falla su sanity check, se ABORTA antes de escribir
   nada de ningun año (no se escribe parcialmente un rango que fallo
   validacion a la mitad).
5. Checksum sha256 (antes/despues) de las filas de densidad_poblacional
   con origen 'TerriData' o 'TerriData-proyeccion' -- deben quedar
   BIT-IDENTICAS tras la corrida salvo en los años donde
   --confirmar-sobrescribir-terridata-proyeccion autorizo explicitamente
   el cambio; se reporta la comparacion siempre, se aborta si cambio
   algo no autorizado.
6. Solo si todo lo anterior pasa: UPDATE via guardar_indicador (misma
   funcion ya usada en Fase 1, UPDATE por llave unica
   codigoDivipola+nameVar+yearM, nunca INSERT duplicado).

Uso:
    # Reporta el rango completo 2025-2042 sin escribir nada
    docker exec techo360-backend python -m ingestion.actualizar_densidad_dane --dry-run

    # Escribe solo 2025-2026 (no toca TerriData-proyeccion, seguro sin flag)
    docker exec techo360-backend python -m ingestion.actualizar_densidad_dane

    # Escribe 2025-2042 completo, sobrescribiendo TerriData-proyeccion en 2027-2042
    docker exec techo360-backend python -m ingestion.actualizar_densidad_dane --confirmar-sobrescribir-terridata-proyeccion

    # Re-cargar un año especifico ya cargado (ej. 2024)
    docker exec techo360-backend python -m ingestion.actualizar_densidad_dane --anio-inicio 2024 --anio-fin 2024
"""
import argparse
import hashlib
import logging
import statistics
import sys

import psycopg2
import psycopg2.extras

from core.config import settings
from ingestion.base import IndicadorRecord
from ingestion.dane_poblacion_adapter import DanePoblacionAdapter

logger = logging.getLogger(__name__)

ANIO_INICIO_DEFAULT = 2025
ANIO_FIN_DEFAULT = 2042
VARIABLE = "densidad_poblacional"

# Años donde TerriData tiene dato real para densidad_poblacional --
# fuera de este conjunto no hay ancla real contra que comparar, se
# encadena contra el año DANE anterior (ver AVISO del modulo).
ANIOS_CON_TERRIDATA_REAL = set(range(2000, 2027))  # confirmado en vivo: TerriData va 2000-2026

# Origen huerfano que ocupa 2027-2042 sin escribir confirmacion
# explicita -- ver AVISO del modulo.
ORIGEN_HUERFANO = "TerriData-proyeccion"

RATIO_MINIMA_ACEPTABLE = 0.5
RATIO_MAXIMA_ACEPTABLE = 2.0


# INC-001: origen unico para toda la serie 2024-2042 (el año ya esta en yearM) e
# intervaloConfianza constante. Debe coincidir con lo que ya quedo en mapcalordb.
ORIGEN_PROYECCION = "proyeccion-poblacion-sobre-area"
INTERVALO_CONFIANZA = 30.0


def origen_para(anio: int) -> str:
    """Origen que se escribe (y se busca como ancla) para `anio`. Desde INC-001 es el
    mismo para todos los años; el parametro se conserva por compatibilidad con los
    llamadores (cargar_densidad_dane_anio_previo, main)."""
    return ORIGEN_PROYECCION


def construir_records(anio: int, densidades: dict[str, tuple[str, float]]) -> list[IndicadorRecord]:
    """IndicadorRecord de `anio` con el origen y el intervaloConfianza de INC-001."""
    origen = origen_para(anio)
    return [
        IndicadorRecord(
            codigo_divipola=codigo,
            entidad_territorial=nombre,
            variable=VARIABLE,
            valor=densidad,
            anio=anio,
            fuente=origen,
            intervalo_confianza=INTERVALO_CONFIANZA,
        )
        for codigo, (nombre, densidad) in densidades.items()
    ]


def _conectar_con_timeout(database_url: str, statement_timeout_ms: int = 30_000):
    """Igual que ingestion.db._conectar(), pero CON timeouts -- ver AVISO
    en ingestion/db.py. connect_timeout cubre el establecimiento inicial
    de la conexion; statement_timeout (via `options`, del lado del
    servidor) cubre una query lenta pero VIVA.

    Ninguno de los dos cubre el caso real que se dio dos veces en vivo
    (24-sep-2026, un corte transitorio del relay de Tailscale a mitad
    de una escritura): el UPDATE ya se ejecuto en el servidor y quedo
    "idle in transaction" esperando el siguiente comando del cliente,
    pero el socket TCP murio a medio camino sin que ninguna de las dos
    partes lo notara -- statement_timeout no aplica (no hay query
    corriendo) y el SO por defecto tarda hasta 2 HORAS en notar un
    socket muerto (keepalive por defecto). Por eso los `keepalives_*`:
    fuerzan una sonda TCP cada 5s de inactividad, y si 3 sondas seguidas
    (~11s) no responden, psycopg2 lanza OperationalError en vez de
    quedarse colgado para siempre -- el error se captura por fila en
    guardar_indicadores_lote() y el resto del lote puede seguir/reportar
    en vez de bloquear todo el proceso indefinidamente."""
    return psycopg2.connect(
        database_url,
        connect_timeout=15,
        options=f"-c statement_timeout={statement_timeout_ms}",
        keepalives=1,
        keepalives_idle=5,
        keepalives_interval=2,
        keepalives_count=3,
    )


def guardar_indicadores_lote(conn, cur, records: list[IndicadorRecord], progreso_cada: int = 25) -> tuple[int, int, list[str]]:
    """Variante de ingestion.db.guardar_indicador() para cargas masivas
    (ver AVISO agregado en ingestion/db.py 24-sep-2026): MISMA logica
    SQL exacta (UPDATE por codigoDivipola+nameVar+yearM, INSERT solo si
    no actualizo ninguna fila -- nunca INSERT directo sin intentar el
    UPDATE primero), pero recibe una conexion/cursor YA ABIERTOS en vez
    de abrir uno nuevo por registro -- el cuello de botella medido era
    el establecimiento de conexion TCP (~15-17s cada una via el relay de
    Tailscale), no el tamaño de una transaccion ni el costo de un COMMIT
    sobre una conexion ya abierta.

    >>> AVISO: COMMIT POR FILA, no por lote -- decision corregida
    >>> 24-sep-2026 <<<
    La primera version de esta funcion hacia un solo commit() al FINAL
    de las ~1,104 filas del lote. Eso es INCORRECTO para este caso de
    uso: ante un corte de red a mitad de camino (ya observado en vivo
    dos veces), Postgres hace ROLLBACK de TODA la transaccion abierta,
    no solo de la fila en curso -- se perderia el progreso completo del
    año, no solo la fila colgada. commit() por fila synchronize con la
    idempotencia real de guardar_indicador() (UPDATE por llave unica):
    si el proceso muere a mitad, las filas ya comiteadas quedan
    persistidas de verdad, y un reintento del lote completo simplemente
    vuelve a escribir el mismo valor en esas filas (no-op real) y
    continua donde haga falta en las que faltan.

    Devuelve (escritas, errores, mensajes_error) -- no lanza excepcion
    por fila individual, para que un error puntual no aborte todo el
    lote (mismo criterio que el loop original en main())."""
    escritas = 0
    errores = 0
    mensajes_error = []
    total = len(records)
    for i, record in enumerate(records, 1):
        codigo_divipola = record.codigo_divipola.strip() if record.codigo_divipola else None
        try:
            if codigo_divipola:
                cur.execute(
                    """
                    UPDATE MapCalorDB
                    SET valueVar = %s, originVar = %s, municipios = %s, intervaloConfianza = %s
                    WHERE codigoDivipola = %s AND nameVar = %s AND yearM = %s
                    """,
                    (record.valor, record.fuente, record.entidad_territorial, record.intervalo_confianza, codigo_divipola, record.variable, record.anio),
                )
            else:
                cur.execute(
                    """
                    UPDATE MapCalorDB
                    SET valueVar = %s, originVar = %s, codigoDivipola = %s, intervaloConfianza = %s
                    WHERE municipios = %s AND nameVar = %s AND yearM = %s
                    """,
                    (record.valor, record.fuente, codigo_divipola, record.intervalo_confianza, record.entidad_territorial, record.variable, record.anio),
                )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO MapCalorDB (municipios, nameVar, valueVar, yearM, originVar, codigoDivipola, intervaloConfianza)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (record.entidad_territorial, record.variable, record.valor, record.anio, record.fuente, codigo_divipola, record.intervalo_confianza),
                )
            conn.commit()
            escritas += 1
        except Exception as exc:
            errores += 1
            mensajes_error.append(f"{codigo_divipola} ({record.entidad_territorial}, {record.anio}): {exc!r}")
            conn.rollback()  # solo descarta ESTA fila -- las anteriores ya estan comiteadas

        if i % progreso_cada == 0 or i == total:
            print(f"    progreso {record.anio}: {i}/{total} (escritas={escritas}, errores={errores})", flush=True)

    return escritas, errores, mensajes_error


def cargar_areas_km2(cur) -> dict[str, float]:
    cur.execute(
        "SELECT codigo_municipio, ST_Area(geography(geom)) / 1000000.0 AS area_km2 FROM zonas_municipio"
    )
    return {r["codigo_municipio"]: float(r["area_km2"]) for r in cur.fetchall() if r["area_km2"]}


def cargar_densidad_terridata(cur, anio: int) -> dict[str, float]:
    cur.execute(
        """
        SELECT codigodivipola, valuevar FROM mapcalordb
        WHERE namevar = %s AND yearm = %s AND originvar = 'TerriData' AND codigodivipola IS NOT NULL
        """,
        (VARIABLE, anio),
    )
    return {r["codigodivipola"]: float(r["valuevar"]) for r in cur.fetchall()}


class AnclaNoPersistidaError(RuntimeError):
    """El año de referencia (anio-1, fuera de ANIOS_CON_TERRIDATA_REAL)
    no tiene fila DANE persistida en MapCalorDB todavia -- ver AVISO
    junto a cargar_densidad_dane_anio_previo() mas abajo."""


def cargar_densidad_dane_anio_previo(cur, anio_previo: int) -> dict[str, float]:
    """>>> AVISO -- fix del sanity check (25-sep-2026) <<<
    Para años fuera de ANIOS_CON_TERRIDATA_REAL, el sanity check
    encadena contra el año anterior. La primera version comparaba
    contra densidades_por_anio.get(anio_previo, {}) -- un diccionario
    EN MEMORIA, calculado solo si anio_previo tambien formaba parte del
    mismo rango --anio-inicio/--anio-fin de ESTA corrida. Eso se rompe
    estructuralmente con ingestion/cargar_dane_con_reintentos.py, que
    por diseño (resiliencia de red, ver AVISO en ese modulo) lanza un
    SUBPROCESO NUEVO por cada año -- asi que anio_previo nunca esta en
    memoria cuando se corre un año a la vez (confirmado en vivo,
    24/25-sep-2026: 2027 aborto con "no hay municipios en comun para
    comparar" al correrlo solo, con 2026 ya real en la BD).

    Fix: consultar MapCalorDB directamente por la fila DANE YA
    PERSISTIDA de anio_previo (origen exacto origen_para(anio_previo) =
    'proyeccion-poblacion-sobre-area' desde INC-001; antes 'DANE-proyeccion-
    poblacion-{anio_previo}'), no memoria de la misma corrida -- funciona sin
    importar si anio_previo se proceso en este mismo subproceso o en
    uno anterior (o en una sesion previa por completo).

    Si anio_previo NO tiene esa fila (por ejemplo, se pide correr 2030
    antes que 2028: 2029 necesitaria 2028, que no existe aun), esto
    lanza AnclaNoPersistidaError explicito -- NUNCA hay que inventar
    una comparacion ni caer silenciosamente a "sin ancla" (que el
    sanity_check ya trata como fallo, pero con un mensaje generico que
    no distingue esta causa de datos genuinamente faltantes)."""
    origen_previo = origen_para(anio_previo)
    cur.execute(
        """
        SELECT codigodivipola, valuevar FROM mapcalordb
        WHERE namevar = %s AND yearm = %s AND originvar = %s AND codigodivipola IS NOT NULL
        """,
        (VARIABLE, anio_previo, origen_previo),
    )
    filas = cur.fetchall()
    if not filas:
        raise AnclaNoPersistidaError(
            f"No hay ancla persistida para el año {anio_previo} (se buscó originVar='{origen_previo}' "
            f"en MapCalorDB y no se encontró ninguna fila). Cargar {anio_previo} antes de continuar "
            f"con años que dependen de él como referencia."
        )
    return {r["codigodivipola"]: float(r["valuevar"]) for r in filas}


def contar_filas_huerfanas(cur, anio: int) -> int:
    cur.execute(
        "SELECT COUNT(*) AS n FROM mapcalordb WHERE namevar = %s AND yearm = %s AND originvar = %s",
        (VARIABLE, anio, ORIGEN_HUERFANO),
    )
    return cur.fetchone()["n"]


def checksum_origenes_sensibles(cur) -> str:
    """sha256 de todas las filas de densidad_poblacional con origen
    'TerriData' o 'TerriData-proyeccion' -- lo que NO deberia cambiar
    salvo confirmacion explicita del usuario (ver AVISO del modulo)."""
    cur.execute(
        """
        SELECT codigodivipola, yearm, originvar, valuevar
        FROM mapcalordb
        WHERE namevar = %s AND originvar IN ('TerriData', %s)
        ORDER BY codigodivipola, yearm, originvar
        """,
        (VARIABLE, ORIGEN_HUERFANO),
    )
    filas = cur.fetchall()
    texto = "|".join(f"{r['codigodivipola']}:{r['yearm']}:{r['originvar']}:{r['valuevar']}" for r in filas)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def calcular_densidades(poblacion: dict, areas: dict) -> dict[str, tuple[str, float]]:
    """Devuelve {codigo: (nombre, densidad)} solo donde hay poblacion Y area."""
    resultado = {}
    for codigo, (nombre, hab) in poblacion.items():
        area = areas.get(codigo)
        if area and area > 0:
            resultado[codigo] = (nombre, round(hab / area, 2))
    return resultado


def sanity_check(densidades_nuevas: dict, densidades_referencia: dict) -> tuple[bool, dict]:
    ratios = []
    for codigo, densidad_ref in densidades_referencia.items():
        entrada_nueva = densidades_nuevas.get(codigo)
        if entrada_nueva and densidad_ref > 0:
            ratios.append(entrada_nueva[1] / densidad_ref)

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


def main(dry_run: bool, anio_inicio: int, anio_fin: int, confirmar_sobrescribir: bool) -> int:
    database_url = settings.database_url

    print(f"Descargando/parseando poblacion DANE para {anio_inicio}-{anio_fin}...")
    adapter = DanePoblacionAdapter()

    conn = _conectar_con_timeout(database_url)
    conn.autocommit = True  # esta conexion solo hace SELECTs de diagnostico -- sin esto quedaria "idle in transaction" abierta durante todo el loop de 18 años, sin necesidad
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            print("Calculando areas via PostGIS (una sola vez, no depende del año)...")
            areas = cargar_areas_km2(cur)
            print(f"  {len(areas)} municipios con area calculada.")

            print("\nChecksum ANTES de origenes sensibles (TerriData / TerriData-proyeccion)...")
            checksum_antes = checksum_origenes_sensibles(cur)
            print(f"  sha256 = {checksum_antes}")

        # Densidades por año, calculadas en orden para poder encadenar el
        # sanity check de los años sin ancla TerriData real contra el año
        # DANE inmediatamente anterior.
        densidades_por_anio: dict[int, dict[str, tuple[str, float]]] = {}
        resumen_sanity_por_anio: dict[int, dict] = {}
        anios_bloqueados_por_huerfano: set[int] = set()

        for anio in range(anio_inicio, anio_fin + 1):
            print(f"\n=== Año {anio} ===")
            poblacion = adapter.poblacion_por_municipio(anio)
            print(f"  {len(poblacion)} municipios con poblacion DANE {anio}.")
            densidades_nuevas = calcular_densidades(poblacion, areas)
            print(f"  {len(densidades_nuevas)} municipios con densidad calculada (poblacion/area).")
            densidades_por_anio[anio] = densidades_nuevas

            if anio in ANIOS_CON_TERRIDATA_REAL:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    referencia = cargar_densidad_terridata(cur, anio)
                fuente_referencia = f"TerriData real (yearm={anio})"
            else:
                # Ver AVISO en cargar_densidad_dane_anio_previo(): la
                # referencia se lee de MapCalorDB (ancla YA PERSISTIDA),
                # no de densidades_por_anio en memoria -- necesario
                # porque ingestion/cargar_dane_con_reintentos.py corre
                # un año a la vez, en subprocesos separados.
                anio_previo = anio - 1
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    referencia = cargar_densidad_dane_anio_previo(cur, anio_previo)
                fuente_referencia = f"DANE {anio_previo} (persistido en MapCalorDB)"

            ok, resumen = sanity_check(densidades_nuevas, referencia)
            resumen_sanity_por_anio[anio] = resumen
            print(f"  Sanity check contra {fuente_referencia}: {resumen}")
            if not ok:
                print(
                    f"\nABORTADO en año {anio}: ratio mediana ({resumen.get('ratio_mediana')}) fuera de "
                    f"[{RATIO_MINIMA_ACEPTABLE}, {RATIO_MAXIMA_ACEPTABLE}]. No se escribio nada de "
                    f"ningun año del rango -- revisar antes de reintentar."
                )
                return 1

            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                n_huerfanas = contar_filas_huerfanas(cur, anio)
            if n_huerfanas:
                print(f"  AVISO: {n_huerfanas} filas de origen '{ORIGEN_HUERFANO}' existen para {anio} "
                      f"y serian sobrescritas por este UPDATE (guardar_indicador no filtra por originVar).")
                if not confirmar_sobrescribir:
                    anios_bloqueados_por_huerfano.add(anio)

        print(f"\n{'=' * 70}")
        print("Resumen de sanity checks: OK en todos los años del rango.")

        if anios_bloqueados_por_huerfano:
            print(
                f"\nAños BLOQUEADOS para escritura (chocan con '{ORIGEN_HUERFANO}', falta "
                f"--confirmar-sobrescribir-terridata-proyeccion): "
                f"{sorted(anios_bloqueados_por_huerfano)}"
            )

        anios_a_escribir = [a for a in range(anio_inicio, anio_fin + 1) if a not in anios_bloqueados_por_huerfano]
        print(f"Años a escribir en esta corrida: {anios_a_escribir}")

        # Los 3 gaps de la auditoria: confirmar cuales quedan cerrados (con
        # el ultimo año del rango, el mas representativo del estado actual).
        gaps_conocidos = {"00000": "Area En Litigio Cauca - Huila", "27493": "Nuevo Belen De Bajira", "88001": "San Andres"}
        anio_referencia_gaps = anios_a_escribir[-1] if anios_a_escribir else anio_fin
        print(f"\n=== Estado de los 3 gaps conocidos (año {anio_referencia_gaps}) ===")
        for codigo, nombre in gaps_conocidos.items():
            entrada = densidades_por_anio.get(anio_referencia_gaps, {}).get(codigo)
            if entrada:
                print(f"  {codigo} ({nombre}): CERRADO -- densidad={entrada[1]} hab/km2")
            else:
                print(f"  {codigo} ({nombre}): SIGUE SIN DATO")

        if dry_run:
            total_filas = sum(len(densidades_por_anio[a]) for a in range(anio_inicio, anio_fin + 1))
            print(f"\n--dry-run: se hubieran escrito {total_filas} filas en total "
                  f"({len(anios_a_escribir)} de {anio_fin - anio_inicio + 1} años -- ver bloqueados arriba). "
                  f"No se escribio nada.")
            return 0

        escritas_total = 0
        errores_total = 0
        for anio in anios_a_escribir:
            origen = origen_para(anio)
            densidades_nuevas = densidades_por_anio[anio]
            records = construir_records(anio, densidades_nuevas)
            print(f"\nEscribiendo {len(records)} filas para {anio} (origen='{origen}', conexion reutilizada)...")
            conn_escritura = _conectar_con_timeout(database_url)
            try:
                with conn_escritura.cursor() as cur_escritura:
                    escritas, errores, mensajes_error = guardar_indicadores_lote(conn_escritura, cur_escritura, records)
            finally:
                conn_escritura.close()
            print(f"  Escritas: {escritas}. Errores: {errores}.")
            for msg in mensajes_error[:10]:
                logger.warning(f"Error escribiendo {msg}")
            if len(mensajes_error) > 10:
                logger.warning(f"... y {len(mensajes_error) - 10} errores mas (no listados individualmente).")
            escritas_total += escritas
            errores_total += errores

        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            print("\nChecksum DESPUES de origenes sensibles (TerriData / TerriData-proyeccion)...")
            checksum_despues = checksum_origenes_sensibles(cur)
            print(f"  sha256 = {checksum_despues}")

        if anios_bloqueados_por_huerfano:
            # No se toco ningun año que chocara con el huerfano -> el
            # checksum de origenes sensibles debe quedar IDENTICO.
            if checksum_antes != checksum_despues:
                print(
                    "\nALERTA: el checksum de TerriData/TerriData-proyeccion cambio pese a que ningun "
                    "año bloqueado se escribio -- investigar antes de confiar en el resultado."
                )
                errores_total += 1
            else:
                print("  OK -- identico al de antes (ningun año bloqueado se toco).")
        elif confirmar_sobrescribir:
            print(f"  (Cambio esperado: se autorizo sobrescribir '{ORIGEN_HUERFANO}' con "
                  f"--confirmar-sobrescribir-terridata-proyeccion.)")

        print(f"\n{'=' * 70}\nTotal escritas: {escritas_total}. Total errores: {errores_total}.")
        return 1 if errores_total else 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta, sin escribir en la DB.")
    parser.add_argument("--anio-inicio", type=int, default=ANIO_INICIO_DEFAULT)
    parser.add_argument("--anio-fin", type=int, default=ANIO_FIN_DEFAULT)
    parser.add_argument(
        "--confirmar-sobrescribir-terridata-proyeccion",
        action="store_true",
        dest="confirmar_sobrescribir",
        help="Autoriza sobrescribir filas del origen huerfano 'TerriData-proyeccion' "
             "(2027-2042) -- ver AVISO en el docstring del modulo. Sin este flag, esos "
             "años quedan bloqueados aunque pasen el sanity check.",
    )
    args = parser.parse_args()
    sys.exit(main(args.dry_run, args.anio_inicio, args.anio_fin, args.confirmar_sobrescribir))
