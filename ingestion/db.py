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
    Devuelve 'actualizado' o 'insertado'."""
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE MapCalorDB
                SET valueVar = %s, originVar = %s
                WHERE municipios = %s AND nameVar = %s AND yearM = %s
                """,
                (record.valor, record.fuente, record.entidad_territorial, record.variable, record.anio),
            )
            if cur.rowcount > 0:
                logger.info(f"Actualizado: {record.entidad_territorial} / {record.variable} / {record.anio}")
                return "actualizado"

            cur.execute(
                """
                INSERT INTO MapCalorDB (municipios, nameVar, valueVar, yearM, originVar)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (record.entidad_territorial, record.variable, record.valor, record.anio, record.fuente),
            )
            logger.info(f"Insertado: {record.entidad_territorial} / {record.variable} / {record.anio}")
            return "insertado"
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
