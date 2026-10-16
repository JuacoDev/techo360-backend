"""
Acceso a la base de datos principal (postgres-principal) para el paso 6
del pipeline: guardar los IndicadorRecord y GeometryRecord que ya
pasaron validacion.

Esquema real definido por Jeremy (DBA) -- dos tablas, con dos
comportamientos de guardado DISTINTOS por una razon tecnica real:

1. MapCalorDB (indicadores numericos)
   Tiene UNIQUE(codigoDivipola, nameVar, yearM) + un TRIGGER BEFORE
   INSERT que LANZA UNA EXCEPCION si ya existe esa combinacion
   (SQLSTATE 23505, no es un fallo silencioso).

   >>> CAMBIO 2026-08-30: la clave de unicidad paso de
   >>> (municipios, nameVar, yearM) a (codigoDivipola, nameVar, yearM),
   >>> tanto en el CONSTRAINT de la tabla como en el cuerpo de la
   >>> funcion trigger (validar_unicidad_mapcalordb() -- ver mas abajo,
   >>> antes comparaba por NOMBRE, ahora por CODIGO). Motivo: al
   >>> insertar las 3,366 filas municipales de deficit habitacional
   >>> (DaneDeficitAdapter), 66 nombres de municipio del anexo del DANE
   >>> resultaron estar duplicados entre departamentos distintos (151
   >>> municipios reales, ej. "Sabanalarga" existe en Antioquia,
   >>> Atlantico Y Casanare con codigos 05628/08638/85300) -- exactamente
   >>> el mismo problema de fondo ya documentado mas abajo con "El
   >>> Carmen" (Choco vs. Santander), pero confirmado aqui con alcance
   >>> mucho mayor. Un UNIQUE por nombre de texto no puede distinguir
   >>> estos casos; el codigo DIVIPOLA si. Decision tomada con Juan
   >>> David: corregir el esquema en la raiz en vez de seguir
   >>> parchando nombres de texto caso por caso.
   >>>
   >>> Consecuencia aceptada: para las filas historicas viejas que
   >>> tienen codigoDivipola NULL (ver aviso de mas abajo sobre
   >>> migrar_columna_codigo_divipola), Postgres trata cada NULL como
   >>> distinto de cualquier otro NULL en un UNIQUE -- esas filas
   >>> quedan sin proteccion de duplicados a nivel de base de datos
   >>> (antes la tenian, via el nombre). No se reconstruyo el codigo
   >>> para esas filas viejas (mismo motivo ya explicado abajo: no se
   >>> puede derivar de forma confiable a partir del nombre ya
   >>> guardado). guardar_indicador() abajo compensa esto a nivel de
   >>> aplicacion cuando el registro entrante SI trae codigo.

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

>>> originVar / intervaloConfianza (agregado 2026-08-30) -- esquema de
>>> procedencia de datos, para las 3 piezas de relleno de MapCalorDB
>>> (cobertura de acueducto/alcantarillado, tenencia informal, deficit
>>> habitacional) que necesitan datos 2025-2026 sin serie historica real
>>> completa. Principio rector: ningun dato estimado puede confundirse
>>> con un dato real -- por eso originVar deja de ser solo "de que fuente
>>> vino" y pasa a ser tambien "es un dato observado o un relleno, y con
>>> que metodo". intervaloConfianza queda NULL siempre que originVar sea
>>> una fuente real (un dato observado no tiene intervalo de confianza,
>>> por definicion); solo se llena en filas de relleno, como proxy del
>>> error historico del metodo usado (no es un intervalo de prediccion
>>> estadisticamente riguroso -- se documenta como tal en cada caso).
>>>
>>> Valores de originVar en uso (reales, ya existentes, sin tocar):
>>>   "TerriData"      -- dato real observado, via TerriDataAdapter.
>>>       Para densidad_poblacional especificamente, ver
>>>       "DANE-proyeccion-poblacion-2024" mas abajo -- reemplazo desde
>>>       22-sep-2026, TerriData queda solo como fuente historica
>>>       (años 2008-2023) para esta variable.
>>>   "proyeccion-poblacion-sobre-area" (INC-001, 16/10/2026) -- PROYECCION de
>>>       densidad_poblacional 2024-2042 (poblacion proyectada DANE / area_km2),
>>>       intervaloConfianza = 30. Reemplaza al nombre "DANE-proyeccion-poblacion-
>>>       {anio}" descrito justo debajo: esas filas se renombraron en la base y
>>>       actualizar_densidad_dane.py ya escribe este origen. services/origen.py
>>>       la clasifica como proyeccion (prefijo "proyeccion-").
>>>   "DANE-proyeccion-poblacion-{anio}" -- NOMBRE LEGADO, ya no existe en la base
>>>       (agregado 22-sep-2026, ver
>>>       ingestion/dane_poblacion_adapter.py y
>>>       ingestion/actualizar_densidad_dane.py); entonces se tomaba por dato real
>>>       observado, error corregido por INC-001
>>>       para densidad_poblacional, calculado como poblacion municipal
>>>       DANE (anexo oficial PPED 2018-2042, publicado 08-ago-2025) /
>>>       area_km2 (PostGIS sobre zonas_municipio.geom, capa canonica
>>>       IGAC). Reemplaza a "TerriData" para esta variable en TODOS los
>>>       municipios (no solo los gaps) para no dejarla dividida entre 2
>>>       origenes distintos -- decision explicita del usuario tras
>>>       confirmar con un sanity check en vivo que ambas fuentes
>>>       coinciden casi exactamente (ratio mediana nueva/vieja = 1.00,
>>>       p10=0.998, p90=1.002, sobre 1.102 municipios comparados).
>>>       Cierra 2 de los 3 gaps de densidad_poblacional que TerriData
>>>       nunca cubrio: San Andres (88001, TerriData solo publica el
>>>       archipielago como agregado departamental 88000, nunca
>>>       desagregado) y Nuevo Belen de Bajira (27493, municipio
>>>       reciente). El tercer gap (00000, "Area En Litigio
>>>       Cauca-Huila") sigue sin dato -- no tiene codigo de municipio
>>>       en NINGUNA fuente ya investigada, territorio en disputa sin
>>>       administracion municipal propia.
>>>   "IGAC Catastro"  -- dato real observado, via IGACCatastroAdapter,
>>>       PERO ver aviso especial sobre tenencia_informal_terrenos mas
>>>       abajo -- esta fuente es un snapshot EN VIVO (capa ArcGIS del
>>>       IGAC, sin serie historica real), asi que "IGAC Catastro" solo
>>>       es un dato real observado para el año en que la fuente
>>>       realmente se consulto -- pedirle un año distinto via --anio no
>>>       produce un dato real de ese año, produce el estado actual
>>>       etiquetado con un año falso (ver caso documentado abajo).
>>>       LIMITACION ADICIONAL (22-sep-2026): el 73% de los municipios
>>>       (816/1.118) tienen valuevar=0.0 exacto para
>>>       tenencia_informal_terrenos -- probablemente subestima la
>>>       informalidad real (contraste con UPRA: solo 79/1.104
>>>       municipios en banda 0%-25%), no una lectura genuina de "sin
>>>       informalidad". Ver el docstring completo de
>>>       ingestion/igac_catastro_adapter.py para la causa tecnica
>>>       exacta y por que UPRA no se adopta todavia como reemplazo
>>>       (solo PDF, vigencia fija en 2020). Sin corregir aun.
>>>
>>> >>> CASO REAL: tenencia_informal_terrenos quedo con UN SOLO año
>>> >>> real (30/31-ago-2026) <<<
>>> Entre el 17-ago-2026 (creacion de IGACCatastroAdapter, commit
>>> 00fa4cf) y el 23-ago-2026 02:51 (activacion del guardado de
>>> codigoDivipola, commit 9151ba9) se cargaron 1,033 filas con
>>> yearm=2023 para esta variable, via `--anio 2023` pasado a mano al
>>> runner -- pero IGACCatastroAdapter no tiene forma de dar un dato
>>> real de 2023 (es un snapshot en vivo), asi que esas filas eran en
>>> realidad el estado ACTUAL del catastro (de esos dias de agosto)
>>> mal etiquetado. Se confirmo con evidencia concluyente (30-ago-2026):
>>> las 1,033 filas de "2023" y las 1,033 de "2026" (la corrida real,
>>> posterior) daban el MISMO valor exacto en el 100% de los casos
>>> (media/desviacion de diferencia = 0.000000) -- imposible si fueran
>>> mediciones reales independientes. Se borraron: primero 2,062 filas
>>> de interpolacion lineal 2024-2025 que se habian calculado sobre esa
>>> base falsa, y despues las 1,033 filas fantasma de "2023" mismas.
>>> tenencia_informal_terrenos quedo con UNA sola observacion real:
>>> 2026 (1,033 filas). No hay nada que ofrecer para otros años hasta
>>> que exista una segunda medicion real futura que permita alguna
>>> forma de proyeccion o interpolacion honesta -- GET /api/zonas debe
>>> reflejar esto (una sola año disponible), no inventar mas relleno.
>>>
>>> Valores de originVar para relleno (esquema de procedencia, Sprint 3):
>>>   "proyeccion-ultimo-valor-observado-{anio}" (renombrado 22-sep-2026,
>>>       ver ingestion/reetiquetar_ultimo_valor_observado.py -- ANTES
>>>       "ultimo-valor-observado" sin prefijo, inconsistente con el
>>>       resto del esquema y sin el prefijo "proyeccion-" que el
>>>       frontend usa para clasificar la tarjeta como proyeccion en vez
>>>       de fuente confirmada) -- baseline ingenuo (repetir el ultimo
>>>       valor real conocido, sin ajuste). Usado para cobertura_acueducto/
>>>       cobertura_alcantarillado 2025-2026, ambos anclados a {anio}=2024
>>>       (decision del 30-ago-2026: el LightGBM entrenado para estas dos
>>>       variables especificas queda PEOR que este mismo baseline en el
>>>       set de test real ya documentado -- no es defendible usar a
>>>       proposito el metodo que ya se sabe que es peor solo porque
>>>       tambien es "un modelo"). Fiabilidad DINAMICA segun antiguedad
>>>       (no un numero fijo como las otras dos proyecciones de abajo):
>>>       fiabilidad = max(30, 60 - 10*(antiguedad-1)), antiguedad =
>>>       yearm - anio_del_dato_real. Hoy: 60% para yearm=2025, 50% para
>>>       yearm=2026 -- ver el docstring completo de
>>>       ingestion/reetiquetar_ultimo_valor_observado.py para la
>>>       justificacion de cada parametro de la formula (incluye RMSE
>>>       real medido a distintas antiguedades, no un numero inventado).
>>>   "interpolacion-lineal" -- interpolar entre dos puntos reales
>>>       conocidos. Se intento para tenencia_informal_terrenos
>>>       2024-2025 (30-ago-2026) pero se revirtio por completo (ver
>>>       caso real documentado arriba) -- los "2 puntos reales" no lo
>>>       eran. Sigue reservado para el dia en que una variable tenga de
>>>       verdad 2+ años reales distintos que interpolar.
>>>   "SAE-censo2018" -- Small Area Estimation con covariables del censo
>>>       2018. NOTA 2026-09-09: este parrafo describia un estado viejo
>>>       (34 filas departamentales de TerriData) que ya no es cierto --
>>>       DaneDeficitAdapter (ver ingestion/dane_deficit_adapter.py)
>>>       reemplazo esas 34 filas por 1,122 filas MUNICIPALES reales
>>>       (yearm=2018, codigoDivipola real, confirmado en vivo). Por eso
>>>       SAE real ya NO hacia falta para bajar de departamento a
>>>       municipio -- ver "proyeccion-tendencia-nacional-2018-2024"
>>>       abajo, que extrapola esa base municipal real hacia 2024 con una
>>>       tasa nacional simple, sin necesitar SAE. Esta entrada queda
>>>       reservada por si en el futuro hiciera falta bajar un dato que
>>>       SOLO exista a nivel departamental o nacional.
>>>
>>>   "proyeccion-tendencia-nacional-2018-2024" (agregado 2026-09-09,
>>>       ver ingestion/proyeccion_2024.py) -- deficit_habitacional_
>>>       cuantitativo/cualitativo/total no tienen ninguna fuente
>>>       municipal real para 2024 (DANE solo actualiza el deficit via la
>>>       ECV, que no baja de nivel departamental/regional -- confirmado,
>>>       no asumido). Se extrapola el valor municipal REAL de 2018 (el
>>>       de DaneDeficitAdapter) aplicando la tasa de cambio NACIONAL
>>>       2018->2024 de cada componente (CNPV 2018 vs ECV 2024, ambos
>>>       DANE) de forma proporcional. Fiabilidad media-alta (parte de un
>>>       dato municipal real, solo extrapola la tendencia) -- ver el
>>>       modulo de proyeccion para la formula exacta y el aviso sobre
>>>       por que el componente cuantitativo sube en vez de bajar
>>>       (CNPV censo vs ECV encuesta no son perfectamente comparables).
>>>
>>>   "proyeccion-snapshot-2026" (agregado 2026-09-09) -- para
>>>       tenencia_informal_terrenos: no existe NINGUN punto de
>>>       referencia municipal anterior a 2026 para esta variable
>>>       especifica (investigado: el CNPV 2018 no publica un anexo
>>>       estatico de tenencia de vivienda a nivel municipal, es un
>>>       sistema de consulta interactivo REDATAM sin archivo descargable
>>>       -- no se pudo confirmar con el nivel de certeza que este
>>>       proyecto exige). Sin un segundo punto real, no hay tendencia
>>>       honesta que calcular -- se reutiliza el snapshot 2026 tal cual
>>>       para 2024, con fiabilidad BAJA (es el mismo dato de hoy, no una
>>>       medicion de 2024).
>>>   "proyeccion-regresion-lineal-4puntos" (agregado 23-sep-2026)
>>>       -- densidad_poblacional hacia el futuro, calculado por
>>>       techo360-proyeccion (GET /api/proyecciones). Regresion lineal
>>>       por minimos cuadrados sobre los 4 ultimos puntos REALES del
>>>       municipio (ventana elegida con un backtesting de 4 cortes, ver
>>>       ese modulo), ajustada contra el año real de cada punto y con
>>>       recorte en 0. La primera version uso solo 2 puntos, pero el
>>>       ultimo par podia no representar la tendencia (Medellin,
>>>       Barranquilla y Cali proyectaban caida teniendo serie al alza).
>>>       Reemplazo a
>>>       LightGBM para esta variable tras un backtesting contra los años
>>>       reales 2019-2024 (RMSE 28.07 vs 223.89 del LightGBM recursivo,
>>>       que ademas correlacionaba NEGATIVAMENTE, -0.212, con la
>>>       tendencia real). Ver el docstring de app/services/proyeccion.py
>>>       en techo360-proyeccion para la tabla completa y la limitacion
>>>       conocida del backtesting (los "reales" son a su vez
>>>       proyecciones DANE/TerriData, no mediciones independientes).
>>>   "LightGBM-proyeccion" -- reservado para cuando un modelo entrenado
>>>       SI demuestre superar su baseline ingenuo correspondiente. Sigue
>>>       SIN USARSE: la ultima variable que lo iba a usar
>>>       (densidad_poblacional) quedo descartada por el backtesting de
>>>       arriba. No usar este valor solo porque el metodo "suena mas
>>>       sofisticado" si la evidencia real dice que un metodo mas simple
>>>       generaliza mejor.
>>>
>>> AVISO IMPORTANTE, no ocultar -- guardar_indicador() NO ESCALA a
>>> cargas masivas (24-sep-2026, ver ingestion/actualizar_densidad_dane.py):
>>> cada llamada abre y cierra su PROPIA conexion TCP via _conectar()
>>> (psycopg2.connect(database_url) sin timeout de socket/statement).
>>> Contra la VPS de produccion, alcanzable solo por Tailscale y sin
>>> conexion directa (ver seccion "Acceso a la base de datos" en
>>> techo360/CLAUDE.md), cada conexion nueva mide ~15-17 segundos
>>> (handshake TCP+TLS+auth via el relay de Tailscale, no solo el ping
>>> ~180ms) -- medido en vivo al intentar cargar ~2.200 filas de un
>>> tiron, que a ese ritmo habria tomado ~9-10 horas. Ademas, sin
>>> timeout, un corte transitorio del relay puede dejar una conexion
>>> colgada indefinidamente en estado "idle in transaction" (visto en
>>> vivo esa misma corrida) en vez de fallar limpio y reintentar.
>>> Para menos de ~100-200 filas por corrida esto es aceptable (asi se
>>> uso en T-92 a T-95 y en las correcciones puntuales de esta seccion).
>>> Para cargas de miles de filas (ej. un rango de años, no un año
>>> suelto), NO llamar a guardar_indicador() en un loop -- abrir UNA
>>> conexion reutilizada para todo el lote/año y comitear al final. Ver
>>> ingestion/actualizar_densidad_dane.py (funcion
>>> guardar_indicadores_lote, agregada 24-sep-2026 especificamente para
>>> esto) como referencia del patron alternativo: misma logica SQL de
>>> UPDATE-antes-que-INSERT que este modulo, pero con la conexion como
>>> parametro en vez de abrirla adentro. Se implemento LOCAL a ese
>>> script, sin modificar guardar_indicador() aqui, para no tocar el
>>> comportamiento que ya usan otros scripts de ingestion/ bajo presion
>>> de una fecha de Sprint -- generalizar esto en db.py mismo (ej. que
>>> guardar_indicador() acepte una conexion opcional) queda como mejora
>>> pendiente, no urgente.
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
    nombre.

    Busqueda del UPDATE (cambio 2026-08-30, ver aviso del modulo): si el
    registro entrante trae codigo_divipola, el UPDATE busca por CODIGO
    (coherente con la clave real de unicidad ahora); solo cuando no hay
    codigo cae al viejo criterio por nombre, para las fuentes que aun no
    lo dan. Buscar siempre por nombre aqui habria reintroducido el mismo
    bug que motivo el cambio de esquema: con nombre duplicado entre
    municipios reales distintos (ej. "Sabanalarga"), un UPDATE por nombre
    actualizaria las dos filas a la vez con el valor de una sola."""
    codigo_divipola = record.codigo_divipola.strip() if record.codigo_divipola else None
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
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
            if cur.rowcount > 0:
                logger.info(f"Actualizado: {record.entidad_territorial} / {record.variable} / {record.anio}")
                return "actualizado"

            cur.execute(
                """
                INSERT INTO MapCalorDB (municipios, nameVar, valueVar, yearM, originVar, codigoDivipola, intervaloConfianza)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (record.entidad_territorial, record.variable, record.valor, record.anio, record.fuente, codigo_divipola, record.intervalo_confianza),
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
