"""
Adaptador de TerriData (DNP) v4 -- corrige un problema real de memoria
encontrado en produccion (VPS): la v3 hacia OOM-kill (SIGKILL,
codigo de salida 137) incluso solo pidiendo una muestra de 5 filas,
porque construia la lista de TODAS las filas de la dimension completa
en memoria (todos los indicadores, todos los municipios, todos los
años) antes de recortar a las primeras N -- en un servidor con RAM
muy ajustada, eso es fatal.

CAMBIO CLAVE respecto a v3: en vez de devolver una lista completa de
dicts, `_iterar_filas_dimension()` es un GENERADOR -- procesa el
archivo linea por linea y solo mantiene en memoria la fila actual,
nunca el archivo completo parseado. `fetch()` filtra fila por fila
sobre la marcha (descarta lo que no coincide con el "Indicador" que
se busca, sin acumularlo). `obtener_muestra()` se detiene apenas junta
`limite` filas, sin seguir leyendo el resto del archivo.

El contenido decodificado del ZIP (un solo string largo) SI se cachea
por dimension para no descargar el mismo ZIP de nuevo si varias
variables de la misma corrida usan la misma dimension -- eso es
liviano comparado con guardar cientos de miles de dicts.

Esquema real confirmado en produccion (ver version anterior del
adaptador para el detalle completo de esa investigacion):
    Delimitador: "|"
    Columnas: Código Departamento | Departamento | Código Entidad |
    Entidad | Dimensión | Subcategoría | Indicador | Dato Numérico |
    Dato Cualitativo | Año | Mes | Fuente | Unidad de Medida
"""
import io
import logging
import zipfile
from typing import Iterator

import httpx

from ingestion.base import DataSourceAdapter, HTTP_HEADERS, IndicadorRecord
from ingestion.validacion_esquema import verificar_esquema

logger = logging.getLogger(__name__)

DIMENSIONES_URL = "https://terridata.dnp.gov.co/tdtservice/FITService.svc/rest//busqueda/dimensiones"

COLUMNAS_ESPERADAS = {"Entidad", "Código Entidad", "Indicador", "Año", "Dato Numérico"}

MAPEO_DIMENSIONES = {
    "densidad_poblacional": {"dimension": "2", "indicador": "Densidad poblacional"},
    "deficit_habitacional_cuantitativo": {"dimension": "3", "indicador": "Déficit cuantitativo de vivienda"},
    "deficit_habitacional_cualitativo": {"dimension": "3", "indicador": "Déficit cualitativo de vivienda"},
    "cobertura_acueducto": {"dimension": "3", "indicador": "Cobertura de acueducto"},
    "cobertura_alcantarillado": {"dimension": "3", "indicador": "Cobertura de alcantarillado"},
    "tenencia_informal_terrenos": {"dimension": "3", "indicador": "Tenencia informal de terrenos"},
}


class TerriDataAdapter(DataSourceAdapter):
    nombre = "TerriData"
    disponible = True

    def __init__(self, timeout: float = 60.0):
        self._timeout = timeout
        # Se cachea el TEXTO crudo (un string), no las filas ya parseadas
        # en dicts -- mucho mas liviano para un servidor con RAM ajustada.
        self._cache_texto_dimension: dict[str, str] = {}

    def _obtener_enlace_dimension(self, client: httpx.Client, codigo_dimension: str) -> str:
        resp = client.get(DIMENSIONES_URL)
        resp.raise_for_status()
        dimensiones = resp.json()
        info = next((d for d in dimensiones if d.get("CodigoCategoria") == codigo_dimension), None)
        if info is None:
            raise ValueError(f"No se encontro la dimension con codigo '{codigo_dimension}'")
        return info["EnlacePlano"]

    def _obtener_texto_dimension(self, codigo_dimension: str) -> str:
        """Descarga y descomprime el TXT de una dimension, cachea el
        texto crudo. NO parsea filas aqui -- eso lo hace
        `_iterar_filas_dimension()` de forma perezosa."""
        if codigo_dimension in self._cache_texto_dimension:
            return self._cache_texto_dimension[codigo_dimension]

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

        self._cache_texto_dimension[codigo_dimension] = contenido
        return contenido

    def _iterar_filas_dimension(self, codigo_dimension: str) -> Iterator[dict]:
        """Generador: produce una fila (dict) a la vez, sin construir
        nunca la lista completa en memoria. Verifica el esquema contra
        las primeras filas antes de seguir."""
        contenido = self._obtener_texto_dimension(codigo_dimension)
        lineas = contenido.splitlines()
        if not lineas:
            return

        encabezados = lineas[0].split("|")

        # Verificar esquema con solo las primeras filas, sin acumular
        # todo el archivo -- se detiene apenas junta 5.
        muestra_para_validar = []
        for linea in lineas[1:6]:
            valores = linea.split("|")
            if len(valores) == len(encabezados):
                muestra_para_validar.append(dict(zip(encabezados, valores)))
        if muestra_para_validar:
            verificar_esquema(self.nombre, muestra_para_validar, COLUMNAS_ESPERADAS)

        for linea in lineas[1:]:
            valores = linea.split("|")
            if len(valores) != len(encabezados):
                continue
            yield dict(zip(encabezados, valores))

    def obtener_muestra(self, codigo_dimension: str = "2", limite: int = 5) -> list[dict]:
        """Devuelve solo las primeras `limite` filas -- se detiene ahi,
        NO sigue leyendo/parseando el resto del archivo (esto es lo que
        causaba el OOM-kill en la version anterior: se construia la
        lista completa antes de recortar)."""
        muestra = []
        for fila in self._iterar_filas_dimension(codigo_dimension):
            muestra.append(fila)
            if len(muestra) >= limite:
                break
        return muestra

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        records: list[IndicadorRecord] = []

        for var_interna in variables:
            info = MAPEO_DIMENSIONES.get(var_interna)
            if info is None:
                raise ValueError(f"'{var_interna}' no esta mapeada en MAPEO_DIMENSIONES.")

            # Filtra fila por fila sobre la marcha -- las filas que no
            # coinciden con el Indicador buscado nunca se acumulan en
            # memoria, se descartan de inmediato.
            for fila in self._iterar_filas_dimension(info["dimension"]):
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
