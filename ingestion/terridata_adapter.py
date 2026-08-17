"""
Adaptador de TerriData (DNP) v3 -- esquema real CONFIRMADO desde
produccion (no es una suposicion, esta vez de verdad).

La primera corrida real del scheduler (VPS, 2026-08-17) trajo el
esquema exacto en el mensaje de EsquemaCambiadoError que el propio
`verificar_esquema()` genero al detectar el desajuste con la v2
(que asumia mal tanto el delimitador como los nombres de columna):

    Código Departamento | Departamento | Código Entidad | Entidad |
    Dimensión | Subcategoría | Indicador | Dato Numérico |
    Dato Cualitativo | Año | Mes | Fuente | Unidad de Medida

Correcciones sobre la v2:
  - Delimitador real: "|" (pipe), NO ";" -- por eso la v2 nunca
    lograba separar columnas, tomaba el encabezado completo como una
    sola columna gigante.
  - La columna de nombre de indicador se llama "Indicador", no
    "Variable".
  - El valor numerico esta en "Dato Numérico", no en "Dato" (existe
    tambien "Dato Cualitativo" para indicadores no numericos, que no
    nos interesa aqui).
  - "Entidad", "Código Entidad" y "Año" SI estaban bien nombrados en
    la v2 -- esos tres se mantienen igual.

Mismo patron de acceso que v2 (sin cambios): un ZIP completo por
dimension, via la API REST real de terridata.dnp.gov.co (confirmada
en vivo con navegador real, ver terridata_adapter.py version anterior
para el detalle de esa investigacion).
"""
import io
import logging
import zipfile

import httpx

from ingestion.base import DataSourceAdapter, HTTP_HEADERS, IndicadorRecord
from ingestion.validacion_esquema import verificar_esquema

logger = logging.getLogger(__name__)

DIMENSIONES_URL = "https://terridata.dnp.gov.co/tdtservice/FITService.svc/rest//busqueda/dimensiones"

# Confirmado real -- no incluye TODAS las columnas del archivo (hay mas:
# Código Departamento, Departamento, Dimensión, Subcategoría, Mes,
# Fuente, Unidad de Medida), solo las que este adaptador usa.
COLUMNAS_ESPERADAS = {"Entidad", "Código Entidad", "Indicador", "Año", "Dato Numérico"}

MAPEO_DIMENSIONES = {
    "densidad_poblacional": {"dimension": "2", "indicador": "Densidad poblacional"},
    "deficit_habitacional_cuantitativo": {"dimension": "3", "indicador": "Déficit cuantitativo de vivienda"},
    "deficit_habitacional_cualitativo": {"dimension": "3", "indicador": "Déficit cualitativo de vivienda"},
    "cobertura_acueducto": {"dimension": "3", "indicador": "Cobertura de acueducto"},
    "cobertura_alcantarillado": {"dimension": "3", "indicador": "Cobertura de alcantarillado"},
    # Nombre exacto del "Indicador" dentro de la dimension 3 sin
    # confirmar todavia -- el esquema (columnas) ya esta confirmado,
    # pero el VALOR exacto de este texto en la columna "Indicador"
    # sigue siendo la mejor suposicion. Si `fetch()` devuelve 0
    # registros para esta variable pero las demas de la dimension 3 si
    # traen datos, es este texto el que hay que ajustar.
    "tenencia_informal_terrenos": {"dimension": "3", "indicador": "Tenencia informal de terrenos"},
}


class TerriDataAdapter(DataSourceAdapter):
    nombre = "TerriData"
    disponible = True

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout
        self._cache_dimensiones: dict[str, list[dict]] = {}

    def _obtener_enlace_dimension(self, client: httpx.Client, codigo_dimension: str) -> str:
        resp = client.get(DIMENSIONES_URL)
        resp.raise_for_status()
        dimensiones = resp.json()
        info = next((d for d in dimensiones if d.get("CodigoCategoria") == codigo_dimension), None)
        if info is None:
            raise ValueError(f"No se encontro la dimension con codigo '{codigo_dimension}'")
        return info["EnlacePlano"]

    def _descargar_dimension(self, codigo_dimension: str) -> list[dict]:
        if codigo_dimension in self._cache_dimensiones:
            return self._cache_dimensiones[codigo_dimension]

        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            enlace_zip = self._obtener_enlace_dimension(client, codigo_dimension)
            zip_resp = client.get(enlace_zip)
            zip_resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            nombre_archivo = zf.namelist()[0]
            contenido_bytes = zf.read(nombre_archivo)

        try:
            contenido = contenido_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                contenido = contenido_bytes.decode("latin-1")
            except UnicodeDecodeError:
                contenido = contenido_bytes.decode("utf-8", errors="replace")

        filas = []
        lineas = contenido.splitlines()
        if not lineas:
            self._cache_dimensiones[codigo_dimension] = []
            return []

        # Delimitador CONFIRMADO real: "|" (no ";", ver aviso del modulo).
        encabezados = lineas[0].split("|")
        for linea in lineas[1:]:
            valores = linea.split("|")
            if len(valores) != len(encabezados):
                continue
            filas.append(dict(zip(encabezados, valores)))

        if filas:
            verificar_esquema(self.nombre, filas[:5], COLUMNAS_ESPERADAS)

        self._cache_dimensiones[codigo_dimension] = filas
        return filas

    def obtener_muestra(self, codigo_dimension: str = "2", limite: int = 5) -> list[dict]:
        filas = self._descargar_dimension(codigo_dimension)
        return filas[:limite]

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        records: list[IndicadorRecord] = []

        for var_interna in variables:
            info = MAPEO_DIMENSIONES.get(var_interna)
            if info is None:
                raise ValueError(f"'{var_interna}' no esta mapeada en MAPEO_DIMENSIONES.")

            filas = self._descargar_dimension(info["dimension"])

            for fila in filas:
                if fila.get("Indicador") != info["indicador"]:
                    continue
                anio_fila = fila.get("Año", "")
                if anio is not None and str(anio_fila) != str(anio):
                    continue
                try:
                    records.append(IndicadorRecord(
                        codigo_divipola=fila.get("Código Entidad", "").strip(),
                        entidad_territorial=fila.get("Entidad", "").strip(),
                        variable=var_interna,
                        valor=float(str(fila.get("Dato Numérico", "nan")).replace(",", ".")),
                        anio=int(anio_fila) if anio_fila else 0,
                        fuente=self.nombre,
                    ))
                except (TypeError, ValueError):
                    continue

        return records
