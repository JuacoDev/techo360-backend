"""
Adaptador de TerriData (DNP) v5 -- corrige el OOM-kill (codigo 137) de
raiz. La v4 seguia fallando con el mismo codigo 137 a pesar de que
"se detenia en 5 filas" -- porque el generador de v4 seguia llamando
a `contenido.splitlines()` sobre el TEXTO COMPLETO ya descomprimido
antes de iterar, y `_obtener_texto_dimension()` descomprimia el ZIP
entero de una sola vez con `zf.read(...)` y lo decodificaba completo
a un solo string gigante ANTES de que el generador pudiera empezar a
detenerse en nada. El problema nunca estuvo en "cuantas filas se
procesan", sino en la descompresion/decodificacion completa que
pasaba ANTES de eso.

CAMBIO CLAVE en v5: se usa `zipfile.ZipFile.open()` (no `.read()`)
envuelto en `io.TextIOWrapper`, que descomprime y decodifica el
archivo EN FLUJO, linea por linea, a medida que se itera -- nunca
existe en memoria "el archivo completo como texto" ni "la lista
completa de lineas". Esto es lo que de verdad evita el OOM.

Se cachea el ZIP CRUDO (bytes comprimidos, unos pocos MB) por
dimension, no el texto descomprimido -- volver a descomprimir la
misma dimension varias veces (si varias variables de la corrida
comparten dimension) es costo de CPU, no de memoria, y en este
servidor la memoria es el recurso escaso, no la CPU.

Esquema real confirmado en produccion:
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
        # Se cachea el ZIP CRUDO (bytes comprimidos), no el texto
        # descomprimido -- mucho mas liviano en memoria. Volver a
        # descomprimir es costo de CPU, aceptable en este servidor.
        self._cache_zip_bytes: dict[str, bytes] = {}

    def _obtener_enlace_dimension(self, client: httpx.Client, codigo_dimension: str) -> str:
        resp = client.get(DIMENSIONES_URL)
        resp.raise_for_status()
        dimensiones = resp.json()
        info = next((d for d in dimensiones if d.get("CodigoCategoria") == codigo_dimension), None)
        if info is None:
            raise ValueError(f"No se encontro la dimension con codigo '{codigo_dimension}'")
        return info["EnlacePlano"]

    def _obtener_zip_bytes(self, codigo_dimension: str) -> bytes:
        if codigo_dimension in self._cache_zip_bytes:
            return self._cache_zip_bytes[codigo_dimension]

        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            enlace_zip = self._obtener_enlace_dimension(client, codigo_dimension)
            zip_resp = client.get(enlace_zip)
            zip_resp.raise_for_status()

        self._cache_zip_bytes[codigo_dimension] = zip_resp.content
        return zip_resp.content

    def _iterar_filas_dimension(self, codigo_dimension: str) -> Iterator[dict]:
        """Generador que descomprime y decodifica EN FLUJO, linea por
        linea -- nunca existe en memoria el archivo completo como texto
        ni como lista de lineas. Esto es lo que evita el OOM real.

        Nota sobre encoding: se usa utf-8 con errors="replace" (no un
        reintento con latin-1) a proposito -- intentar dos pasadas
        completas (utf-8 primero, latin-1 si falla) es fragil con un
        generador: si la primera pasada ya produjo filas via `yield`
        antes de fallar a mitad de archivo, un reintento desde cero
        duplicaria esas filas. Con errors="replace", en el peor caso
        algun caracter especial poco comun queda como "?", pero nunca
        se corrompe la estructura de filas ni se duplica nada."""
        zip_bytes = self._obtener_zip_bytes(codigo_dimension)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            nombre_archivo = zf.namelist()[0]
            with zf.open(nombre_archivo) as archivo_binario:
                with io.TextIOWrapper(archivo_binario, encoding="utf-8", errors="replace") as texto:
                    yield from self._procesar_lineas(texto)

    def _procesar_lineas(self, texto_iterable) -> Iterator[dict]:
        """Recibe un iterable de lineas (ej. un TextIOWrapper) y produce
        dicts fila por fila, sin acumular nada mas alla de la fila
        actual y los encabezados."""
        encabezados = None
        primeras_filas_muestra = []

        for numero_linea, linea in enumerate(texto_iterable):
            linea = linea.rstrip("\n").rstrip("\r")
            if not linea:
                continue

            if encabezados is None:
                encabezados = linea.split("|")
                continue

            valores = linea.split("|")
            if len(valores) != len(encabezados):
                continue

            fila = dict(zip(encabezados, valores))

            # Verificar esquema solo una vez, con las primeras filas,
            # sin bloquear el resto del flujo.
            if numero_linea < 6:
                primeras_filas_muestra.append(fila)
                if len(primeras_filas_muestra) == 5:
                    verificar_esquema(self.nombre, primeras_filas_muestra, COLUMNAS_ESPERADAS)

            yield fila

    def obtener_muestra(self, codigo_dimension: str = "2", limite: int = 5) -> list[dict]:
        """Se detiene apenas junta `limite` filas -- gracias al streaming
        real, esto ahora tambien significa que apenas se descomprime lo
        necesario del archivo, no todo."""
        muestra = []
        for fila in self._iterar_filas_dimension(codigo_dimension):
            muestra.append(fila)
            if len(muestra) >= limite:
                break
        return muestra

    def buscar_indicadores(self, codigo_dimension: str, palabra_clave: str) -> list[str]:
        """Recorre una dimension completa y devuelve el conjunto de
        valores UNICOS de la columna 'Indicador' que contengan la
        palabra clave dada (insensible a mayusculas). Sirve para
        encontrar el nombre EXACTO de un indicador sin tener que
        adivinarlo -- el mismo problema que ya tuvimos antes con
        TerriData v1, resuelto aqui de la misma manera: buscar en
        vivo en vez de suponer."""
        palabra_clave_normalizada = palabra_clave.lower()
        encontrados = set()
        for fila in self._iterar_filas_dimension(codigo_dimension):
            indicador = fila.get("Indicador", "")
            if palabra_clave_normalizada in indicador.lower():
                encontrados.add(indicador)
        return sorted(encontrados)

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        records: list[IndicadorRecord] = []

        for var_interna in variables:
            info = MAPEO_DIMENSIONES.get(var_interna)
            if info is None:
                raise ValueError(f"'{var_interna}' no esta mapeada en MAPEO_DIMENSIONES.")

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
