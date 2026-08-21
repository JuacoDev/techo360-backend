"""
Adaptador de ingesta de geometria de zonas administrativas (IGAC) --
carga los limites de departamentos y municipios de Colombia en
zonas_departamento / zonas_municipio, para que el endpoint GET /api/zonas
(a cargo de Daniel, fuera del alcance de este archivo) tenga de donde leer.

Fuente confirmada real y viva (verificado en vivo, con peticiones HTTP
reales, no solo lectura de documentacion):
    https://mapas2.igac.gov.co/server/rest/services/limites/limites/MapServer
    Capa 1 = municipios (1105 reales, confirmado con returnCountOnly),
             campos: MpCodigo, MpNombre, Depto
    Capa 2 = departamentos (33), campos: DeCodigo, DeNombre

>>> CORRECCION IMPORTANTE sobre MpCodigo: el campo reporta
>>> type=esriFieldTypeString, length=6 en el metadata de ArcGIS, pero
>>> ese "length" es solo el ancho MAXIMO asignado a la columna en el
>>> servidor -- los VALORES reales tienen 5 caracteres (confirmado con
>>> datos reales: "68001"=Bucaramanga, "05001"=Medellin,
>>> "11001"=Bogota D.C., y una muestra adicional de 5 municipios en
>>> Nariño/Putumayo, todos de 5 caracteres). Es el DIVIPOLA estandar de
>>> siempre en este proyecto (2 digitos depto + 3 digitos municipio),
>>> NO 6 digitos. Se derivo con zfill(5), no zfill(6) -- con 6 habria
>>> asignado codigo_departamento incorrecto a TODOS los municipios de
>>> forma silenciosa (ej. "68001" -> zfill(6) -> "068001" -> "06", que
>>> es un codigo de departamento real pero EQUIVOCADO, no fallaria el
>>> FK porque "06" tambien existe como codigo valido -- exactamente el
>>> tipo de bug de datos silencioso que ya paso una vez en este
>>> proyecto con entidad_territorial vacio en IGACCatastroAdapter).

>>> CORRECCION IMPORTANTE sobre exceededTransferLimit: NO significa lo
>>> que se penso originalmente. Probado en vivo variando resultRecordCount
>>> (50, 10, 5, 1) en el mismo offset: el campo da True en los 4 casos
>>> por igual, MIENTRAS el servidor SI devuelve exactamente la cantidad
>>> pedida cada vez (sin truncar nada). Y en la pagina verdaderamente
>>> final (offset con menos registros restantes que el lote pedido) el
>>> campo pasa a None/ausente. Conclusion verificada: este campo
>>> significa "hay mas datos despues de esta pagina" (un "hasMore"
>>> generico de ArcGIS), NO "esta pagina en particular se trunco por
>>> geometria demasiado compleja". Usarlo como disparador de la
>>> reduccion de lote (como se penso originalmente) habria reducido el
>>> lote hasta 1 desde la primerisima peticion y fallado toda la
>>> corrida de inmediato -- se descarto ese diseño.
>>>
>>> La señal real y confiable de "esta pagina no trajo lo que se pidio"
>>> es comparar cuantas features llegaron contra cuantas se pidieron
>>> (`len(features) < resultRecordCount`). Si eso pasa, puede ser (a) la
>>> pagina final legitima (no hay mas datos) o (b) un truncamiento real.
>>> Para distinguirlas sin gastar de mas, se hace una sola peticion extra
>>> y liviana (resultRecordCount=1) justo despues del ultimo offset
>>> devuelto: si no hay nada mas, (a); si SI hay algo mas, fue (b) y ahi
>>> si se reduce el lote a la mitad y se reintenta el offset original.
>>> maxAllowableOffset sigue siendo la mitigacion real para la
>>> precision catastral (confirmado que SI evita el problema: con
>>> maxAllowableOffset=0.0005 y lote de 50, todas las paginas probadas
>>> devolvieron exactamente lo pedido).

codigo_departamento de un municipio: la capa de municipios expone un
campo "Depto", pero no queda claro desde afuera si es nombre o codigo
-- para no arriesgar un FK que no calce con lo insertado desde la capa
de departamentos, se deriva del propio MpCodigo (los primeros 2
digitos de un codigo DIVIPOLA SON el codigo de departamento por
convencion, ej. "68001" -> "68" = Santander -- verificado contra los
33 departamentos reales y contra Bucaramanga/Medellin/Bogota por
nombre, todos coinciden). "Depto" se guarda solo para log/depuracion,
no se usa para el FK.

>>> CORRECCION IMPORTANTE, encontrada en la primera corrida real
>>> contra produccion (no en pruebas): Bogota D.C. (MpCodigo "11001")
>>> SI aparece en la capa de municipios, pero el departamento "11" NO
>>> aparece en la capa de departamentos del IGAC (administrativamente
>>> es un Distrito Capital, sin poligono propio en esa capa especifica)
>>> -- esto violaba el FK de zonas_municipio y dejaba a los 1105
>>> municipios sin guardar de un solo saque (0 guardados, error de FK).
>>> Se resolvio derivando sinteticamente cualquier codigo_departamento
>>> que los municipios usen pero que la capa de departamentos no traiga
>>> (ver agrupar_municipios_sin_departamento/guardar_departamentos_derivados
>>> mas abajo) -- el poligono derivado es la union de los municipios de
>>> ese grupo, y el nombre queda marcado explicitamente como "derivado"
>>> para no confundirlo con un poligono oficial de la capa de
>>> departamentos.

Simplificacion geometrica: se hace en SQL (ST_SimplifyPreserveTopology,
no ST_Simplify a secas -- evita huecos entre municipios vecinos al
simplificar cada poligono por separado), no en Python -- por eso este
adaptador no necesita ninguna libreria de geometria, solo httpx +
psycopg2 (ya en requirements.txt).
"""
import json
import logging
from datetime import date
from typing import Callable

import httpx
import psycopg2

from ingestion.base import DataSourceAdapter, DataSourceUnavailableError, HTTP_HEADERS
from ingestion.validacion_esquema import EsquemaCambiadoError, verificar_esquema

logger = logging.getLogger(__name__)

MAP_SERVER_URL = "https://mapas2.igac.gov.co/server/rest/services/limites/limites/MapServer"
DEPARTAMENTOS_URL = f"{MAP_SERVER_URL}/2/query"
MUNICIPIOS_URL = f"{MAP_SERVER_URL}/1/query"

COLUMNAS_ESPERADAS_DEPARTAMENTOS = {"DeCodigo", "DeNombre"}
COLUMNAS_ESPERADAS_MUNICIPIOS = {"MpCodigo", "MpNombre", "Depto"}

# Tolerancias de ST_SimplifyPreserveTopology, en grados (dataset en 4326):
# 0.01 ~ 1.1 km (departamentos, se ven bien en zoom alejado)
# 0.001 ~ 110 m (municipios, se ven bien en zoom cercano)
TOLERANCIA_DEPARTAMENTOS = 0.01
TOLERANCIA_MUNICIPIOS = 0.001

MAX_OFFSET_DEPARTAMENTOS = 0.005
MAX_OFFSET_MUNICIPIOS = 0.0005
LOTE_INICIAL_MUNICIPIOS = 50

DDL_ZONAS = """
CREATE TABLE IF NOT EXISTS zonas_departamento (
    codigo_departamento VARCHAR(2) PRIMARY KEY,
    nombre              VARCHAR(100) NOT NULL,
    geom                GEOMETRY(MultiPolygon, 4326) NOT NULL,
    actualizado_en       TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_zonas_departamento_geom ON zonas_departamento USING GIST (geom);

CREATE TABLE IF NOT EXISTS zonas_municipio (
    codigo_municipio     VARCHAR(6) PRIMARY KEY,
    codigo_departamento  VARCHAR(2) NOT NULL REFERENCES zonas_departamento(codigo_departamento),
    nombre               VARCHAR(250) NOT NULL,
    geom                 GEOMETRY(MultiPolygon, 4326) NOT NULL,
    actualizado_en        TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_zonas_municipio_geom ON zonas_municipio USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_zonas_municipio_depto ON zonas_municipio (codigo_departamento);
"""


class ZonasIGACAdapter(DataSourceAdapter):
    """No sigue el patron fetch(variables, anio) de los demas adaptadores
    a proposito -- esto no es un indicador ni una serie anual, es una
    sincronizacion completa de un dataset de limites administrativos
    que casi no cambia. Ver `sincronizar()` como punto de entrada real."""

    nombre = "IGAC Limites Administrativos"
    disponible = True

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout

    def fetch(self, variables: list[str], anio: int | None = None):
        raise DataSourceUnavailableError(
            "ZonasIGACAdapter no implementa fetch(variables, anio) -- usar sincronizar() directamente."
        )

    # ------------------------------------------------------------
    # Descarga
    # ------------------------------------------------------------

    def _query_capa(
        self,
        client: httpx.Client,
        url: str,
        out_fields: str,
        max_allowable_offset: float,
        result_record_count: int,
        result_offset: int = 0,
    ) -> dict:
        params = {
            "where": "1=1",
            "outFields": out_fields,
            "f": "geojson",
            "outSR": 4326,
            "maxAllowableOffset": max_allowable_offset,
            "resultRecordCount": result_record_count,
            "resultOffset": result_offset,
        }
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise RuntimeError(f"Error del servidor IGAC ({url}): {data['error']}")
        return data

    def descargar_departamentos(self, client: httpx.Client) -> list[dict]:
        """Caben en una sola consulta (33 registros reales, resultRecordCount=100
        de sobra). La señal de "se trajo todo" es que vinieron MENOS de
        los 100 pedidos (no exceededTransferLimit -- ver el aviso del
        modulo, ese campo es "hasMore", no "esta pagina se trunco")."""
        data = self._query_capa(
            client, DEPARTAMENTOS_URL, "DeCodigo,DeNombre",
            MAX_OFFSET_DEPARTAMENTOS, result_record_count=100,
        )
        features = data.get("features", [])
        if len(features) >= 100:
            raise RuntimeError(
                f"Se recibieron {len(features)} departamentos (>= los 100 pedidos) -- "
                f"puede haber mas de los que caben en una sola pagina, algo cambio "
                f"respecto a los 33 confirmados originalmente. Revisar antes de confiar "
                f"en el resultado (habria que paginar esta capa tambien)."
            )
        if features:
            verificar_esquema(
                self.nombre,
                [f["properties"] for f in features[:5]],
                COLUMNAS_ESPERADAS_DEPARTAMENTOS,
            )
        return features

    def _hay_mas_datos(self, client: httpx.Client, offset: int) -> bool:
        """Peticion liviana (resultRecordCount=1, sin geometria) para
        distinguir 'esta fue la ultima pagina de verdad' de 'esta pagina
        se trunco a medio dataset' -- ver el aviso del modulo sobre por
        que exceededTransferLimit no sirve para esto."""
        params = {
            "where": "1=1", "outFields": "MpCodigo", "f": "json",
            "returnGeometry": "false", "resultRecordCount": 1, "resultOffset": offset,
        }
        response = client.get(MUNICIPIOS_URL, params=params)
        response.raise_for_status()
        data = response.json()
        return len(data.get("features", [])) > 0

    def descargar_municipios(self, client: httpx.Client) -> list[dict]:
        """Pagina con resultOffset, en lotes de LOTE_INICIAL_MUNICIPIOS.
        La señal de "esta pagina no trajo lo pedido" es comparar
        features recibidas contra las pedidas (NO exceededTransferLimit,
        ver el aviso del modulo) -- cuando eso pasa, se verifica con
        `_hay_mas_datos()` si es la pagina final legitima o un
        truncamiento real. Si es truncamiento real, se reduce el lote a
        la mitad y se reintenta el MISMO offset; una vez reducido, se
        queda en ese tamaño para el resto de la descarga."""
        features: list[dict] = []
        offset = 0
        lote = LOTE_INICIAL_MUNICIPIOS
        esquema_verificado = False

        while True:
            data = self._query_capa(
                client, MUNICIPIOS_URL, "MpCodigo,MpNombre,Depto",
                MAX_OFFSET_MUNICIPIOS, result_record_count=lote, result_offset=offset,
            )
            lote_features = data.get("features", [])

            if not esquema_verificado and lote_features:
                verificar_esquema(
                    self.nombre,
                    [f["properties"] for f in lote_features[:5]],
                    COLUMNAS_ESPERADAS_MUNICIPIOS,
                )
                esquema_verificado = True

            if len(lote_features) < lote:
                # Pudo ser la pagina final legitima, o un truncamiento real
                # a medio dataset -- solo un chequeo barato lo distingue.
                if not self._hay_mas_datos(client, offset + len(lote_features)):
                    features.extend(lote_features)
                    logger.info(f"Ultima pagina de municipios: {len(lote_features)} filas en offset {offset}.")
                    break

                if lote <= 1:
                    raise RuntimeError(
                        f"Truncamiento real confirmado en offset {offset} incluso con lote de 1 -- "
                        f"no se puede reducir mas."
                    )
                lote = max(1, lote // 2)
                logger.warning(
                    f"Truncamiento real confirmado en offset {offset} (llegaron {len(lote_features)} de "
                    f"{lote * 2} pedidos, y SI hay mas datos despues) -- reduciendo lote a {lote} y reintentando"
                )
                continue

            features.extend(lote_features)
            logger.info(f"Municipios descargados hasta ahora: {len(features)} (offset {offset}, lote {lote})")
            offset += len(lote_features)

        return features


# ------------------------------------------------------------
# Guardado en Postgres
# ------------------------------------------------------------

def _conectar(database_url: str):
    return psycopg2.connect(database_url)


def crear_tablas_zonas(database_url: str) -> None:
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL_ZONAS)
        logger.info("Tablas zonas_departamento / zonas_municipio verificadas/creadas.")
    finally:
        conn.close()


def _upsert_departamento(cur, codigo: str, nombre: str, geometria_geojson: dict) -> None:
    cur.execute(
        """
        INSERT INTO zonas_departamento (codigo_departamento, nombre, geom, actualizado_en)
        VALUES (%s, %s, ST_SimplifyPreserveTopology(ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s), now())
        ON CONFLICT (codigo_departamento) DO UPDATE
        SET nombre = EXCLUDED.nombre, geom = EXCLUDED.geom, actualizado_en = now()
        """,
        (codigo, nombre, json.dumps(geometria_geojson), TOLERANCIA_DEPARTAMENTOS),
    )


def _upsert_municipio(cur, codigo: str, codigo_departamento: str, nombre: str, geometria_geojson: dict) -> None:
    cur.execute(
        """
        INSERT INTO zonas_municipio (codigo_municipio, codigo_departamento, nombre, geom, actualizado_en)
        VALUES (%s, %s, %s, ST_SimplifyPreserveTopology(ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s), now())
        ON CONFLICT (codigo_municipio) DO UPDATE
        SET codigo_departamento = EXCLUDED.codigo_departamento, nombre = EXCLUDED.nombre,
            geom = EXCLUDED.geom, actualizado_en = now()
        """,
        (codigo, codigo_departamento, nombre, json.dumps(geometria_geojson), TOLERANCIA_MUNICIPIOS),
    )


def codigos_departamento_reales(departamentos: list[dict]) -> set[str]:
    return {str(f["properties"]["DeCodigo"]).strip().zfill(2) for f in departamentos}


def agrupar_municipios_sin_departamento(municipios: list[dict], codigos_reales: set[str]) -> dict[str, list[dict]]:
    """Municipios cuyo codigo_departamento (derivado de MpCodigo, ver
    guardar_municipios) no aparece en la capa de departamentos del IGAC.

    Caso real confirmado en la primera corrida contra produccion: Bogota
    D.C. (MpCodigo '11001' -> depto '11') SI existe en la capa de
    municipios, pero NO tiene poligono propio en la capa de
    departamentos (administrativamente es un Distrito Capital, no un
    departamento con limite propio en esa capa) -- eso violaba el FK de
    zonas_municipio y dejaba 0 municipios guardados de los 1105. Se
    agrupan aqui (no hardcodeado a "11" especificamente, por si hay otro
    caso similar) para poder derivar un poligono sintetico en vez de
    fallar el FK o perder el municipio."""
    grupos: dict[str, list[dict]] = {}
    for f in municipios:
        codigo = str(f["properties"]["MpCodigo"]).strip().zfill(5)[:2]
        if codigo not in codigos_reales:
            grupos.setdefault(codigo, []).append(f)
    return grupos


def guardar_departamentos_derivados(database_url: str, grupos: dict[str, list[dict]]) -> int:
    """Para codigos de departamento que los municipios SI usan pero que
    la capa de departamentos del IGAC no trae (ver
    agrupar_municipios_sin_departamento) -- inserta un poligono
    sintetico, union de los municipios de ese grupo (ST_Union
    incremental, no necesita cargar nada de geometria en Python). El
    nombre queda marcado explicitamente como derivado, para no
    confundirlo con datos oficiales de la capa de departamentos."""
    if not grupos:
        return 0
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            for codigo, features in grupos.items():
                primero, *resto = features
                nombre_base = str(primero["properties"].get("MpNombre", codigo)).strip().title()
                nombre = f"{nombre_base} (derivado de municipios -- sin poligono propio en la capa de departamentos del IGAC)"
                _upsert_departamento(cur, codigo, nombre, primero["geometry"])
                for f in resto:
                    cur.execute(
                        """
                        UPDATE zonas_departamento
                        SET geom = ST_Multi(ST_Union(geom, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))),
                            actualizado_en = now()
                        WHERE codigo_departamento = %s
                        """,
                        (json.dumps(f["geometry"]), codigo),
                    )
                logger.warning(
                    f"Departamento '{codigo}' derivado de {len(features)} municipio(s) "
                    f"(ausente en la capa de departamentos del IGAC): {nombre}"
                )
        logger.info(f"Departamentos derivados guardados: {len(grupos)}")
        return len(grupos)
    finally:
        conn.close()


def guardar_departamentos(database_url: str, features: list[dict]) -> int:
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            for f in features:
                props = f["properties"]
                codigo = str(props["DeCodigo"]).strip().zfill(2)
                nombre = str(props["DeNombre"]).strip().title()
                _upsert_departamento(cur, codigo, nombre, f["geometry"])
        logger.info(f"Departamentos guardados: {len(features)}")
        return len(features)
    finally:
        conn.close()


def guardar_municipios(database_url: str, features: list[dict]) -> int:
    conn = _conectar(database_url)
    try:
        with conn, conn.cursor() as cur:
            for f in features:
                props = f["properties"]
                # zfill(5), NO 6 -- ver aviso del modulo (DIVIPOLA real de
                # 5 digitos, confirmado en vivo, el "length: 6" de ArcGIS
                # es solo el ancho maximo de columna, no el largo real).
                codigo = str(props["MpCodigo"]).strip().zfill(5)
                codigo_departamento = codigo[:2]  # ver aviso del modulo: no se usa "Depto" para el FK
                nombre = str(props["MpNombre"]).strip().title()
                _upsert_municipio(cur, codigo, codigo_departamento, nombre, f["geometry"])
        logger.info(f"Municipios guardados: {len(features)}")
        return len(features)
    finally:
        conn.close()


# ------------------------------------------------------------
# Orquestacion (equivalente a run() en ingestion/run.py, pero
# solo para esta fuente -- no se registro en PLAN_DE_VARIABLES_*
# de run.py a proposito, ver el aviso de alcance del pedido original)
# ------------------------------------------------------------

def sincronizar_zonas(
    database_url: str,
    notificar_admin: Callable[[str, str], None] | None = None,
) -> dict:
    """Descarga departamentos + municipios del IGAC y los deja en
    zonas_departamento / zonas_municipio. Pensado para correrse una
    vez al principio (manual) y despues trimestral (via scheduler) --
    los limites administrativos casi no cambian."""

    def _notificar(asunto: str, mensaje: str):
        logger.error(f"ALERTA -> {asunto}: {mensaje}")
        if notificar_admin is not None:
            notificar_admin(asunto, mensaje)

    adapter = ZonasIGACAdapter()
    resultado = {"departamentos": 0, "municipios": 0, "error": None}

    try:
        crear_tablas_zonas(database_url)

        # Se descargan las DOS capas ANTES de guardar cualquiera (no se
        # guardan departamentos primero como en la version anterior) --
        # hace falta tener los municipios en mano para saber si algun
        # codigo_departamento que ellos usan no vino en la capa de
        # departamentos (ver agrupar_municipios_sin_departamento, caso
        # real confirmado: Bogota D.C.), y derivarlo ANTES del insert de
        # municipios -- si no, el FK revienta y no se guarda nada.
        with httpx.Client(timeout=adapter._timeout, headers=HTTP_HEADERS) as client:
            logger.info("Descargando departamentos del IGAC...")
            departamentos = adapter.descargar_departamentos(client)
            logger.info(f"Departamentos obtenidos: {len(departamentos)}")

        with httpx.Client(timeout=adapter._timeout, headers=HTTP_HEADERS) as client:
            logger.info("Descargando municipios del IGAC (esto tarda varios minutos)...")
            municipios = adapter.descargar_municipios(client)
            logger.info(f"Municipios obtenidos: {len(municipios)}")

        codigos_reales = codigos_departamento_reales(departamentos)
        grupos_derivados = agrupar_municipios_sin_departamento(municipios, codigos_reales)
        if grupos_derivados:
            logger.warning(
                f"Codigos de departamento usados por municipios pero ausentes en la capa "
                f"de departamentos del IGAC: {sorted(grupos_derivados)} -- se derivan sinteticamente."
            )

        resultado["departamentos"] = guardar_departamentos(database_url, departamentos)
        resultado["departamentos"] += guardar_departamentos_derivados(database_url, grupos_derivados)
        resultado["municipios"] = guardar_municipios(database_url, municipios)

        logger.info(
            f"Sincronizacion de zonas completa: {resultado['departamentos']} departamentos, "
            f"{resultado['municipios']} municipios."
        )

    except EsquemaCambiadoError as e:
        _notificar(f"Cambio de esquema en {adapter.nombre}", str(e))
        resultado["error"] = str(e)
    except Exception as e:
        _notificar(f"Error sincronizando zonas ({adapter.nombre})", str(e))
        resultado["error"] = str(e)

    return resultado


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "Falta la variable de entorno DATABASE_URL -- correr esto dentro "
            "del contenedor del backend (ya la tiene seteada), ej.:\n"
            "  docker exec techo360-backend python3 -m ingestion.zonas_igac_adapter"
        )

    resultado = sincronizar_zonas(database_url=database_url)
    print(f"\nResultado: {resultado}")
    if resultado["error"]:
        raise SystemExit(1)
