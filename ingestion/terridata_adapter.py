"""
Adaptador de TerriData (DNP) v2 -- via el portal REAL y funcional
(terridata.dnp.gov.co), no via datos.gov.co (retirado, confirmado 404)
ni via el portal "nuevo" que resulto estar roto (ddtspr.dnp.gov.co/TDPR/,
confirmado en 2 sesiones distintas, dias aparte: jQuery y Google
Analytics fallan al cargar ahi, bloqueando los botones de descarga).

Confirmado en vivo con un navegador real (Chrome, sesion interactiva):
- terridata.dnp.gov.co es un portal DISTINTO, con su propia API REST
  (FITService.svc) y archivos ZIP descargables directos por dimension,
  sin autenticacion. Confirmado: GET a un .zip real devuelve 200,
  content-type application/x-zip-compressed, ~4MB.

PATRON DE ACCESO -- distinto al de antes: en vez de consultar
variable por variable (como hacia el viejo mirror de Socrata), este
portal entrega un ZIP completo por DIMENSION (ej. toda la dimension
"Demografia y poblacion" en un solo archivo TXT), no por variable
individual. El adaptador descarga el ZIP de la dimension relevante,
lo descomprime en memoria, y filtra las filas a las variables que
necesita. Cachea por dimension dentro de una misma corrida para no
descargar el mismo ZIP de varios MB mas de una vez.

Dimensiones relevantes (confirmado via
GET /tdtservice/FITService.svc/rest/busqueda/dimensiones):
  - Dimension 2: "Demografía y población" -> densidad_poblacional
  - Dimension 3: "Vivienda y acceso a servicios públicos" -> deficit
    habitacional, coberturas, tenencia informal (a confirmar)

>>> IMPORTANTE, LEER ANTES DE CONFIAR EN LOS RESULTADOS <<<
Se confirmo que el ZIP es descargable de verdad, pero NO se pudo abrir
su contenido desde este entorno (no hay libreria de unzip en el
navegador para inspeccionarlo ahi, y este entorno de desarrollo no
tiene red hacia terridata.dnp.gov.co). Los nombres de columna
("Variable", "Año", "Dato", "Entidad", "Código Entidad") y el
delimitador (";") asumidos en el codigo de abajo son la mejor
suposicion basada en el patron tipico de exports TXT del DNP, NO estan
confirmados. Usar `obtener_muestra()` en la VPS antes de confiar en
los resultados, y ajustar los nombres de columna en `fetch()` si no
coinciden.
"""
import io
import logging
import zipfile

import httpx

from ingestion.base import DataSourceAdapter, HTTP_HEADERS, IndicadorRecord
from ingestion.validacion_esquema import verificar_esquema

logger = logging.getLogger(__name__)

DIMENSIONES_URL = "https://terridata.dnp.gov.co/tdtservice/FITService.svc/rest//busqueda/dimensiones"

COLUMNAS_ESPERADAS = {"Entidad", "Variable", "Año", "Dato"}

# Mapeo: nombre interno -> codigo de dimension (CONFIRMADO, via la API
# de dimensiones real) + nombre de variable dentro del TXT (SIN
# confirmar -- ver aviso arriba).
MAPEO_DIMENSIONES = {
    "densidad_poblacional": {"dimension": "2", "variable_txt": "Densidad poblacional"},
    "deficit_habitacional_cuantitativo": {"dimension": "3", "variable_txt": "Déficit cuantitativo de vivienda"},
    "deficit_habitacional_cualitativo": {"dimension": "3", "variable_txt": "Déficit cualitativo de vivienda"},
    "cobertura_acueducto": {"dimension": "3", "variable_txt": "Cobertura de acueducto"},
    "cobertura_alcantarillado": {"dimension": "3", "variable_txt": "Cobertura de alcantarillado"},
    # Sin confirmar si esta dimension SI tiene esta variable -- ver la
    # investigacion anterior sobre "posesion sin titulo" en el CNPV.
    "tenencia_informal_terrenos": {"dimension": "3", "variable_txt": "Tenencia informal de terrenos"},
}


class TerriDataAdapter(DataSourceAdapter):
    nombre = "TerriData"
    disponible = True

    def __init__(self, timeout: float = 60.0):
        # Timeout mas alto que las otras fuentes -- los ZIP pesan varios MB.
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
        """Descarga y descomprime el TXT completo de una dimension,
        devuelve una lista de dicts (una por fila). Cachea por dimension
        dentro de esta instancia."""
        if codigo_dimension in self._cache_dimensiones:
            return self._cache_dimensiones[codigo_dimension]

        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            enlace_zip = self._obtener_enlace_dimension(client, codigo_dimension)
            zip_resp = client.get(enlace_zip)
            zip_resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            nombre_archivo = zf.namelist()[0]  # se asume 1 solo archivo TXT dentro del ZIP
            contenido_bytes = zf.read(nombre_archivo)

        # Encoding sin confirmar en la fuente real -- se prueba utf-8
        # primero (mas comun en exports modernos), con latin-1 como
        # respaldo (tipico en TXT viejos del gobierno colombiano). Si
        # ninguno decodifica limpio, usar utf-8 con reemplazo de
        # caracteres invalidos en vez de fallar toda la descarga.
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

        # Delimitador sin confirmar -- ";" es lo tipico en TXT gov CO
        # (para no chocar con comas decimales), verificar en la VPS.
        encabezados = lineas[0].split(";")
        for linea in lineas[1:]:
            valores = linea.split(";")
            if len(valores) != len(encabezados):
                continue
            filas.append(dict(zip(encabezados, valores)))

        if filas:
            verificar_esquema(self.nombre, filas[:5], COLUMNAS_ESPERADAS)

        self._cache_dimensiones[codigo_dimension] = filas
        return filas

    def obtener_muestra(self, codigo_dimension: str = "2", limite: int = 5) -> list[dict]:
        """Descarga una dimension completa y devuelve las primeras filas
        -- para confirmar en la VPS los nombres reales de columna y el
        delimitador/encoding real (ver el aviso al inicio del modulo).
        Por defecto usa la dimension 2 (Demografia y poblacion)."""
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
                # Nombres de columna sin confirmar -- ajustar tras ver
                # obtener_muestra() en la VPS real.
                if fila.get("Variable") != info["variable_txt"]:
                    continue
                anio_fila = fila.get("Año", fila.get("Ano", ""))
                if anio is not None and str(anio_fila) != str(anio):
                    continue
                try:
                    records.append(IndicadorRecord(
                        codigo_divipola=fila.get("Código Entidad", fila.get("Codigo Entidad", "")).strip(),
                        entidad_territorial=fila.get("Entidad", "").strip(),
                        variable=var_interna,
                        valor=float(str(fila.get("Dato", "nan")).replace(",", ".")),
                        anio=int(anio_fila) if anio_fila else 0,
                        fuente=self.nombre,
                    ))
                except (TypeError, ValueError):
                    continue

        return records
