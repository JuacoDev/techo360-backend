"""
Acceso a la base de datos principal (postgres-principal) para el paso 6
del pipeline: guardar los IndicadorRecord y GeometryRecord que ya
pasaron validacion.

Esquema real definido por Jeremy (DBA) -- dos tablas, con dos
comportamientos de guardado DISTINTOS por una razon tecnica real:

1. MapCalorDB (indicadores numericos)
   Tiene UNIQUE(municipios, nameVar, yearM) + un TRIGGER BEFORE INSERT
   que LANZA UNA EXCEPCION si ya existe esa combinacion (SQLSTATE
   23505, no es un fallo silencioso).

   >>> IMPORTANTE: este trigger hace que un INSERT ... ON CONFLICT ...
   >>> DO UPDATE nunca llegue a la parte de UPDATE. En Postgres, los
   >>> triggers BEFORE INSERT se ejecutan ANTES de que se evalue el
   >>> ON CONFLICT -- el trigger de Jeremy revienta primero, sin
   >>> importar si la sentencia tiene ON CONFLICT o no.
   >>>
   >>> Por eso aqui NO se usa ON CONFLICT. Se hace UPDATE primero (por
   >>> la llave unica); si no actualizo ninguna fila (no existia), se
   >>> hace el INSERT. Asi el trigger nunca ve un duplicado en el
   >>> camino normal -- solo dispararia ante una condicion de carrera
   >>> real (dos corridas del pipeline al mismo tiempo), que no aplica
   >>> a este caso de uso (corre programado, una vez por ciclo).

2. ViaDB (geometria de vias)
   NO tiene restriccion de unicidad -- si se re-ingesta con INSERT
   normal, cada corrida duplicaria todos los segmentos. Como la red
   vial casi no cambia, se trata como una "foto" completa: se borra
   todo y se vuelve a insertar en cada corrida (ver `reemplazar_vias`),
   en vez de actualizar fila por fila.

Nota sobre mayusculas/minusculas: Jeremy escribio los nombres de tabla
y columna SIN comillas dobles (MapCalorDB, nameVar, etc.) -- Postgres
pliega automaticamente los identificadores sin comillas a minusculas,
asi que en la base real quedaron como "mapcalordb", "namevar", etc.
Esto NO requiere ningun ajuste especial aqui: mientras este codigo
tampoco use comillas dobles al referenciar esos nombres, Postgres
resuelve la coincidencia igual sin importar como se escriban.

>>> codigoDivipola (agregado despues, ver migrar_columna_codigo_divipola
>>> mas abajo): MapCalorDB nunca tuvo columna de codigo DIVIPOLA -- solo
>>> guardaba el nombre de la entidad (record.entidad_territorial),
>>> descartando record.codigo_divipola por completo. Eso obligaba a
>>> extraer_panel.py (en techo360-proyeccion) a cruzar contra
>>> zonas_municipio por NOMBRE normalizado, un cruce fragil confirmado en
>>> vivo: nombres de TerriData/DANE (ej. "Cali", "Cartagena") no siempre
>>> coinciden con los nombres oficiales de IGAC (ej. "Santiago De Cali",
>>> "Cartagena De Indias"), y encima el mismo nombre oficial IGAC puede
>>> repetirse en dos municipios reales distintos (ej. "El Carmen" existe
>>> en Choco Y en Santander, con codigos distintos) -- un cruce por
>>> nombre nunca puede distinguir ese caso, solo el codigo puede.
>>>
>>> MapCalorDB NO la crea este codigo (la crea Jeremy/DBA en produccion,
>>> con el trigger BEFORE INSERT ya explicado arriba) -- por eso esto es
>>> un ALTER TABLE idempotente y explicito
>>> (migrar_columna_codigo_divipola), no una creacion de tabla, y no se
>>> corre automaticamente en cada guardado (correr un ALTER TABLE en
>>> cada llamada a guardar_indicador seria un costo y un riesgo de lock
>>> innecesario contra una tabla de produccion que este codigo no es
>>> dueño de crear). Se corre una sola vez, a mano, antes de desplegar
>>> este cambio -- ver el bloque `if __name__ == "__main__"` al final
>>> del archivo.
"""
import logging

import psycopg2

from ingestion.base import GeometryRecord, IndicadorRecord

logger = logging.getLogger(__name__)


def _conectar(database_url: str):
    return psycopg2.connect(database_url)


def guardar_indicador(database_url: str, record: IndicadorRecord) -> str:
    """UPDATE primero, INSERT solo si no existia -- ver el aviso del
    modulo sobre por que NO se usa ON CONFLICT aqui.
    Devuelve 'actualizado' o 'insertado'.

    Guarda record.codigo_divipola en la columna codigoDivipola (ver aviso
    del modulo) -- se normaliza "" a NULL (en vez de guardar string
    vacio) porque no todas las fuentes lo dan (ej. FNA probablemente solo
    da el nombre, igual que ya contempla validacion.py); NULL es lo que
    extraer_panel.py espera para decidir cuando usar su fallback por
    nombre."""
    codigo_divipola = record.codigo_divipola.strip() if record.codigo_divipola else None
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE MapCalorDB
                SET valueVar = %s, originVar = %s, codigoDivipola = %s
                WHERE municipios = %s AND nameVar = %s AND yearM = %s
                """,
                (record.valor, record.fuente, codigo_divipola, record.entidad_territorial, record.variable, record.anio),
            )
            if cur.rowcount > 0:
                logger.info(f"Actualizado: {record.entidad_territorial} / {record.variable} / {record.anio}")
                return "actualizado"

            cur.execute(
                """
                INSERT INTO MapCalorDB (municipios, nameVar, valueVar, yearM, originVar, codigoDivipola)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (record.entidad_territorial, record.variable, record.valor, record.anio, record.fuente, codigo_divipola),
            )
            logger.info(f"Insertado: {record.entidad_territorial} / {record.variable} / {record.anio}")
            return "insertado"
    finally:
        conn.close()


def migrar_columna_codigo_divipola(database_url: str) -> None:
    """ALTER TABLE idempotente para agregar codigoDivipola a MapCalorDB --
    ver el aviso del modulo sobre por que esto NO se corre automaticamente
    en cada guardar_indicador() y por que las filas ya cargadas antes de
    este cambio se quedan con codigoDivipola en NULL (no se puede
    reconstruir el codigo real de forma confiable a partir del nombre ya
    guardado -- es justamente el cruce fragil por nombre que este cambio
    busca dejar de necesitar como metodo principal; reconstruirlo bien
    requeriria volver a correr el backfill historico contra TerriData,
    fuera del alcance de esta migracion).

    Correr UNA VEZ, a mano, antes de desplegar el resto de este cambio:
        DATABASE_URL=... python -m ingestion.db
    """
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE MapCalorDB ADD COLUMN IF NOT EXISTS codigoDivipola VARCHAR(5)")
        logger.info("Columna codigoDivipola verificada/agregada en MapCalorDB.")
    finally:
        conn.close()


def contar_distintos_por_variable_anio(database_url: str, variable: str, anio: int) -> tuple[int, int]:
    """Devuelve (total_filas, municipios_distintos) para una variable+año
    ya guardados en MapCalorDB. Existe especificamente para poder
    verificar backfills historicos desde la propia terminal sin entrar a
    psql -- si total_filas y municipios_distintos difieren mucho (ej. 17
    años cargados pero "distintos" clavado en 1), es la misma clase de
    bug real que ya paso una vez (un campo vacio/mal derivado colapsando
    cientos de municipios en la misma llave unica de MapCalorDB)."""
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT municipios) FROM MapCalorDB WHERE nameVar = %s AND yearM = %s",
                (variable, anio),
            )
            total, distintos = cur.fetchone()
            return total, distintos
    finally:
        conn.close()


def reemplazar_vias(database_url: str, registros: list[GeometryRecord]) -> int:
    """Snapshot completo: borra todas las vias existentes y vuelve a
    insertar todas las nuevas, dentro de una sola transaccion (si algo
    falla a mitad de camino, no se queda la tabla vacia a medias)."""
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE ViaDB RESTART IDENTITY")
            for r in registros:
                cur.execute(
                    """
                    INSERT INTO ViaDB (geometry, originVar)
                    VALUES (ST_GeomFromText(%s, 4326), %s)
                    """,
                    (r.geometria_wkt, r.fuente),
                )
        logger.info(f"Vias reemplazadas: {len(registros)} segmentos insertados")
        return len(registros)
    finally:
        conn.close()


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "Falta la variable de entorno DATABASE_URL -- correr esto dentro "
            "del contenedor del backend (ya la tiene seteada), ej.:\n"
            "  docker exec techo360-backend python3 -m ingestion.db"
        )

    migrar_columna_codigo_divipola(database_url=database_url)
