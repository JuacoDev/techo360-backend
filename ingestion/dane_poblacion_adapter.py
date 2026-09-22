"""
Adaptador del DANE -- Proyecciones y Retroproyecciones de Poblacion
Municipal (PPED), base Censo Nacional de Poblacion y Vivienda 2018.

Confirmado en vivo (descarga real + inspeccion con openpyxl, Fase 2 del
plan de scraping, Paso 1 de validacion de fuentes, 22-sep-2026): DANE
publica un anexo oficial con poblacion proyectada por municipio, area
geografica (cabecera/rural/total) y sexo, para el periodo 2018-2042,
publicado 08-ago-2025:

    https://www.dane.gov.co/files/censo2018/proyecciones-de-poblacion/
    Municipal/PPED-AreaSexoEdadMun-2018-2042_VP.xlsx

Hoja "PobMunicipalxAreaSexoEdad" (nombre real, con tildes que se
corrompen segun la codificacion de quien lea el archivo -- se referencia
por POSICION del indice de hoja, nunca por nombre literal, mismo patron
que dane_deficit_adapter.py). Encabezado real en la fila 8 (1-indexado);
la fila 9 es un sub-encabezado de agrupacion (Total/Hombres/Mujeres) que
tambien se descarta.

Columnas confirmadas por POSICION (0-indexada):
    0: DP (codigo departamento)      1: DPNOM (nombre departamento)
    2: MPIO (codigo DIVIPOLA municipio, 5 digitos)
    3: DPMP (NOMBRE del municipio -- pese al nombre de columna, NO es
       un codigo; confirmado en vivo tras un primer intento fallido que
       asumio lo contrario)
    4: ANO (entero, 2018-2042)
    5: AREA GEOGRAFICA ("Cabecera Municipal", "Centros Poblados y Rural
       Disperso", o "Total" -- este adapter SOLO usa "Total")
    6: TOTAL (poblacion total, ambos sexos, ese año/area)
    7+: desglose HOMBRES/MUJERES por edad simple -- no se usa aqui.

1,123 codigos de municipio distintos encontrados (vs. 1,105 en
zonas_municipio, la capa canonica IGAC) -- mismo desalineamiento ya
documentado en ingestion/db.py (corregimientos departamentales de
Amazonas/Guainia/Vaupes que DANE reporta pero IGAC no traza como
poligono propio); no hace falta excluir nada especial aqui, el join
posterior contra zonas_municipio filtra esto naturalmente. NO se
encontraron filas de agregado nacional/departamental espurias (a
diferencia del "01001" de TerriData) -- todos los codigos de municipio
tienen 5 digitos y ninguno termina en "000", confirmado escaneando el
archivo completo.

>>> CASO REAL VERIFICADO: Nuevo Belen de Bajira (27493) <<<
Poblacion=0 en TODOS los años 2018-2023, salta a 29.812 en 2024 y
30.214 en 2025. No es un error de datos -- Bajira fue territorio en
disputa entre Choco y Antioquia, formalizado como municipio propio
(segregado de Riosucio) en años recientes; DANE lo retroproyecta con
poblacion cero antes de su reconocimiento administrativo pleno. Se
respeta el valor tal cual lo publica DANE, sin "corregir" el cero
historico.

NO incluye "00000" (Area en Litigio Cauca-Huila) en ningun año -- DANE
no le asigna codigo de municipio propio (es territorio en disputa sin
administracion municipal), consistente con que esta zona tampoco tiene
dato en ninguna otra fuente ya usada por el proyecto.

Este adapter SOLO expone poblacion (variable intermedia, NO uno de los
7 nombres canonicos del panel) -- "densidad_poblacional" real requiere
dividir por el area del municipio, que no es un dato de esta fuente
sino que se calcula via PostGIS sobre zonas_municipio.geom (ver
ingestion/actualizar_densidad_dane.py, que consume este adapter).
"""
import io
import logging
import time
import urllib.error
import urllib.request

import openpyxl

from ingestion.base import HTTP_HEADERS

logger = logging.getLogger(__name__)

XLSX_URL = "https://www.dane.gov.co/files/censo2018/proyecciones-de-poblacion/Municipal/PPED-AreaSexoEdadMun-2018-2042_VP.xlsx"
INDICE_HOJA = 2  # "PobMunicipalxAreaSexoEdad", ver aviso del modulo
FILA_DATOS_DESDE = 10  # 1-indexado: fila 8 encabezado, fila 9 sub-encabezado, datos desde la 10

COL_CODIGO_MUNICIPIO = 2
COL_NOMBRE_MUNICIPIO = 3
COL_ANIO = 4
COL_AREA_GEOGRAFICA = 5
COL_TOTAL = 6

AREA_GEOGRAFICA_TOTAL = "Total"


class DanePoblacionAdapter:
    nombre = "DANE - Proyecciones de Poblacion Municipal"
    disponible = True

    def __init__(self, timeout: float = 90.0):
        self._timeout = timeout
        self._cache_bytes: bytes | None = None

    def _descargar_xlsx(self, intentos: int = 3) -> bytes:
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
                logger.warning(f"Descarga del anexo DANE-poblacion fallo (intento {intento + 1}/{intentos}): {e}")
                time.sleep(4)
        raise RuntimeError(f"No se pudo descargar el anexo DANE-poblacion tras {intentos} intentos") from ultimo_error

    def poblacion_por_municipio(self, anio: int) -> dict[str, tuple[str, int]]:
        """Devuelve {codigo_divipola: (nombre_municipio, poblacion_total)}
        para el año pedido, SOLO filas con area geografica 'Total' (evita
        contar cabecera+rural duplicado)."""
        contenido = self._descargar_xlsx()
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[INDICE_HOJA]]

        resultado: dict[str, tuple[str, int]] = {}
        for fila in ws.iter_rows(min_row=FILA_DATOS_DESDE, values_only=True):
            codigo_crudo = fila[COL_CODIGO_MUNICIPIO]
            if codigo_crudo is None:
                continue
            if fila[COL_ANIO] != anio:
                continue
            if fila[COL_AREA_GEOGRAFICA] != AREA_GEOGRAFICA_TOTAL:
                continue

            codigo = str(codigo_crudo).strip().zfill(5)
            nombre = str(fila[COL_NOMBRE_MUNICIPIO]).strip()
            poblacion = fila[COL_TOTAL]
            if poblacion is None:
                continue
            resultado[codigo] = (nombre, int(poblacion))

        return resultado


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    adapter = DanePoblacionAdapter()
    datos = adapter.poblacion_por_municipio(2024)
    print(f"Municipios con poblacion 2024: {len(datos)}")
    for codigo in ("88001", "27493", "05001"):
        print(f"  {codigo}: {datos.get(codigo)}")
