"""
Adaptador del DANE -- Deficit Habitacional municipal, Nueva Metodologia
CNPV 2018.

Confirmado en vivo (descarga real + inspeccion con pandas/openpyxl,
sesion previa a este adapter) que el anexo oficial del DANE SI trae el
dato desagregado a nivel MUNICIPAL, con codigo DIVIPOLA directo -- no
hace falta ninguna estimacion (SAE) para esto, es ingesta de dato real,
igual que TerriData/IGAC:

    https://www.dane.gov.co/files/investigaciones/deficit-habitacional/
    deficit-hab-2020-anexo-nueva-metodologia.xlsx

Hoja "Total Municipios" (encabezado real en la fila 8 del Excel, indice
7 con pandas `header=7`), columnas confirmadas por POSICION (no por
nombre -- los nombres tienen tildes que se corrompen facil dependiendo
de la codificacion de quien lea el archivo despues; la posicion es mas
robusta):
    0: Departamento (codigo)         1: Departamento nombre
    2: Municipio (codigo DIVIPOLA)   3: Municipio nombre
    4: Total de hogares
    5: Hogares en deficit cuantitativo   6: Hogares en deficit cualitativo
    7: Hogares en deficit habitacional
    8: Deficit cuantitativo (%)      9: Deficit cualitativo (%)
    10: Deficit habitacional (%)
    11+: componentes (tipo de vivienda, paredes, hacinamiento, etc.) --
         no se usan en este adapter.

1,123 filas totales = 1,122 municipios reales + 1 fila "Total Nacional"
al final (codigo de municipio nulo, columna 2 vacia) -- se excluye.
Codigo de municipio llega como float (ej. 5001.0) porque Excel lo guarda
como numero, no texto -- se normaliza con
`str(int(codigo)).zfill(5)`, mismo patron zfill ya usado en
zonas_igac_adapter.py para DIVIPOLA.

AÑO: el censo (CNPV) es de 2018 -- ese es el año real del dato, aunque
la metodologia se PUBLICO en 2020. Se usa 2018 siempre, sin importar
que `anio` pida quien llame a fetch() (igual que IGACCatastroAdapter
con su fuente de snapshot actual: la fuente en si no tiene mas años que
ofrecer). Si se pide un año distinto de 2018, se ignora y se avisa por
log -- no se inventa un año falso.

>>> POR QUE NO SE REGISTRA EN PLAN_DE_VARIABLES_INDICADOR (run.py) <<<
A proposito NO se agrega al plan semanal del scheduler. El censo CNPV
2018 es un dato historico fijo -- no cambia semana a semana. Si se
registrara ahi, el job semanal (que llama con
`anio=date.today().year`, ver ingestion/scheduler.py) intentaria
"actualizar" el mismo dato de 2018 cada semana para siempre, sin ningun
beneficio, solo gastando CPU/ancho de banda contra el servidor del DANE.
Esta ingesta es de una sola vez (o manual, si el DANE alguna vez publica
una revision), no recurrente -- se corre a mano cuando haga falta, igual
que `zonas_igac_adapter.py::sincronizar_zonas()` no esta en el plan
semanal tampoco por una razon parecida (los limites administrativos casi
no cambian).

3 variables que produce este adapter (nombres ya usados en MapCalorDB,
mas una nueva):
    deficit_habitacional_cuantitativo  -- ya existia (departamental, TerriData)
    deficit_habitacional_cualitativo   -- ya existia (departamental, TerriData)
    deficit_habitacional_total         -- NUEVA, no existia en MapCalorDB

originVar = "DANE-CNPV2018" (ver convencion documentada en
ingestion/db.py) -- distingue estas filas municipales reales de las 34
filas departamentales viejas de TerriData (que se reemplazan, no
coexisten, para no dejarle al mapa de calor la ambiguedad de "cual uso").
"""
import io
import logging
import time
import urllib.error
import urllib.request

import openpyxl

from ingestion.base import DataSourceAdapter, HTTP_HEADERS, IndicadorRecord

logger = logging.getLogger(__name__)

XLSX_URL = "https://www.dane.gov.co/files/investigaciones/deficit-habitacional/deficit-hab-2020-anexo-nueva-metodologia.xlsx"
HOJA = "Total Municipios"
FILA_ENCABEZADO = 8  # 1-indexado, tal como lo reporta Excel/openpyxl
ANIO_CENSO = 2018

# Posicion (0-indexada) de cada columna dentro de la hoja "Total
# Municipios" -- confirmado en vivo, ver aviso del modulo.
COL_CODIGO_MUNICIPIO = 2
COL_NOMBRE_MUNICIPIO = 3
COL_DEFICIT_CUANTITATIVO_PCT = 8
COL_DEFICIT_CUALITATIVO_PCT = 9
COL_DEFICIT_TOTAL_PCT = 10

MAPEO_VARIABLES = {
    "deficit_habitacional_cuantitativo": COL_DEFICIT_CUANTITATIVO_PCT,
    "deficit_habitacional_cualitativo": COL_DEFICIT_CUALITATIVO_PCT,
    "deficit_habitacional_total": COL_DEFICIT_TOTAL_PCT,
}


class DaneDeficitAdapter(DataSourceAdapter):
    nombre = "DANE CNPV 2018 - Deficit Habitacional"
    disponible = True

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout
        self._cache_bytes: bytes | None = None

    def _descargar_xlsx(self, intentos: int = 4) -> bytes:
        if self._cache_bytes is not None:
            return self._cache_bytes

        ultimo_error = None
        for intento in range(intentos):
            try:
                req = urllib.request.Request(XLSX_URL, headers=HTTP_HEADERS)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    contenido = resp.read()
                self._cache_bytes = contenido
                return contenido
            except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as e:
                ultimo_error = e
                logger.warning(f"Descarga del anexo DANE fallo (intento {intento + 1}/{intentos}): {e}")
                time.sleep(4)
        raise RuntimeError(f"No se pudo descargar el anexo del DANE tras {intentos} intentos") from ultimo_error

    def _iterar_filas_municipio(self):
        """Generador de filas crudas (tuplas de celdas) de la hoja
        'Total Municipios', ya sin la fila de 'Total Nacional' al
        final (codigo de municipio vacio)."""
        contenido = self._descargar_xlsx()
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb[HOJA]

        for numero_fila, fila in enumerate(ws.iter_rows(min_row=FILA_ENCABEZADO + 1, values_only=True), start=1):
            codigo_crudo = fila[COL_CODIGO_MUNICIPIO]
            if codigo_crudo is None:
                # La fila de "Total Nacional" (y cualquier fila vacia
                # residual) no tiene codigo de municipio -- se excluye,
                # nunca se cuenta como un municipio real.
                continue
            yield fila

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        variables_pedidas = [v for v in variables if v in MAPEO_VARIABLES]
        if not variables_pedidas:
            return []

        if anio is not None and anio != ANIO_CENSO:
            logger.warning(
                f"DaneDeficitAdapter: se pidio anio={anio}, pero esta fuente es un censo fijo de "
                f"{ANIO_CENSO} (metodologia publicada en 2020, sin mas años disponibles) -- se ignora "
                f"el año pedido y se usa {ANIO_CENSO}, que es el año real del levantamiento del dato."
            )

        records: list[IndicadorRecord] = []
        for fila in self._iterar_filas_municipio():
            codigo = str(int(fila[COL_CODIGO_MUNICIPIO])).zfill(5)
            nombre = str(fila[COL_NOMBRE_MUNICIPIO]).strip()

            for variable in variables_pedidas:
                col = MAPEO_VARIABLES[variable]
                valor_crudo = fila[col]
                if valor_crudo is None:
                    continue
                try:
                    records.append(IndicadorRecord(
                        codigo_divipola=codigo,
                        entidad_territorial=nombre,
                        variable=variable,
                        valor=float(valor_crudo),
                        anio=ANIO_CENSO,
                        fuente="DANE-CNPV2018",
                    ))
                except (TypeError, ValueError):
                    logger.warning(f"Valor invalido para {variable} en municipio {codigo} ({nombre}): {valor_crudo!r}")

        return records


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    adapter = DaneDeficitAdapter()
    registros = adapter.fetch(list(MAPEO_VARIABLES.keys()))

    municipios_distintos = {r.codigo_divipola for r in registros}
    print(f"Registros totales: {len(registros)}")
    print(f"Municipios distintos: {len(municipios_distintos)}")
    for variable in MAPEO_VARIABLES:
        n = sum(1 for r in registros if r.variable == variable)
        print(f"  {variable}: {n} registros")

    if len(sys.argv) > 1 and sys.argv[1] == "--muestra":
        for codigo_ejemplo in ("68001", "05001"):
            for r in registros:
                if r.codigo_divipola == codigo_ejemplo:
                    print(f"  {r}")
