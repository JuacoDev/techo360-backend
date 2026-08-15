"""
Adaptador de vias -- fuente primaria INVIAS (Red Vial Nacional, ArcGIS
Open Data), con OpenStreetMap/Overpass API como fuente de respaldo
(la idea original del equipo: usar el mapa actualizado para calcular
conectividad vial).

Este adaptador devuelve GeometryRecord, no IndicadorRecord -- son
lineas (geometria), no numeros. El calculo de "km de via por km2 de
municipio" (densidad vial) NO pasa aqui -- eso lo hace el Backend
Principal despues, con PostGIS, una vez la geometria ya este en la
base (ver la explicacion que se dio sobre el punto 6.1).

Fuente primaria: INVIAS Open Data (ArcGIS)
  https://inviasopendata-invias.opendata.arcgis.com

Fuente de respaldo: OpenStreetMap, via Overpass API
  https://overpass-api.de/api/interpreter

IMPORTANTE: ninguna de las dos URLs se ha podido probar en vivo desde
este entorno (sin red hacia esos dominios). La estructura de
`fetch_invias()` y `fetch_osm()` sigue el patron tipico de cada API,
pero los nombres de campo EXACTOS de la capa de INVIAS no estan
confirmados -- usar `obtener_muestra_invias()` en la primera corrida
real antes de confiar en los resultados.
"""
import logging

import httpx

from ingestion.base import DataSourceAdapter, GeometryRecord

logger = logging.getLogger(__name__)

INVIAS_FEATURE_SERVER_URL = (
    "https://inviasopendata-invias.opendata.arcgis.com/datasets/red-vial-nacional/FeatureServer/0/query"
)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


class ViasAdapter(DataSourceAdapter):
    """Intenta INVIAS primero. Si falla (fuente caida, esquema cambiado,
    o simplemente no tiene cobertura para el municipio pedido), cae a
    OpenStreetMap como respaldo -- nunca falla en silencio, siempre deja
    en el log cual de las dos fuentes se termino usando."""

    nombre = "Vias (INVIAS/OSM)"
    disponible = True

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    def obtener_muestra_invias(self, limite: int = 5) -> list[dict]:
        with httpx.Client(timeout=self._timeout) as client:
            params = {
                "where": "1=1",
                "outFields": "*",
                "resultRecordCount": limite,
                "f": "geojson",
            }
            response = client.get(INVIAS_FEATURE_SERVER_URL, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("features", [])

    def _fetch_invias(self, codigo_divipola: str | None) -> list[GeometryRecord]:
        with httpx.Client(timeout=self._timeout) as client:
            params = {"where": "1=1", "outFields": "*", "f": "geojson", "resultRecordCount": 2000}
            response = client.get(INVIAS_FEATURE_SERVER_URL, params=params)
            response.raise_for_status()
            data = response.json()

        records = []
        for feature in data.get("features", []):
            geom = feature.get("geometry")
            if geom is None:
                continue
            # Conversion GeoJSON -> WKT simplificada (LineString unicamente,
            # que es lo que se espera de una capa de vias)
            if geom.get("type") != "LineString":
                continue
            coords = ", ".join(f"{lon} {lat}" for lon, lat in geom["coordinates"])
            wkt = f"LINESTRING({coords})"

            props = feature.get("properties", {})
            records.append(GeometryRecord(
                codigo_divipola=codigo_divipola or "",
                tipo_via=props.get("tipo", "sin-clasificar"),
                geometria_wkt=wkt,
                fuente="INVIAS",
            ))
        return records

    def _fetch_osm(self, codigo_divipola: str, bbox: tuple[float, float, float, float]) -> list[GeometryRecord]:
        """Respaldo via Overpass API. `bbox` = (sur, oeste, norte, este)."""
        sur, oeste, norte, este = bbox
        query = f"""
        [out:json][timeout:25];
        way["highway"]({sur},{oeste},{norte},{este});
        out geom;
        """
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(OVERPASS_URL, data={"data": query})
            response.raise_for_status()
            data = response.json()

        records = []
        for element in data.get("elements", []):
            if element.get("type") != "way" or "geometry" not in element:
                continue
            coords = ", ".join(f"{p['lon']} {p['lat']}" for p in element["geometry"])
            wkt = f"LINESTRING({coords})"
            records.append(GeometryRecord(
                codigo_divipola=codigo_divipola,
                tipo_via=element.get("tags", {}).get("highway", "sin-clasificar"),
                geometria_wkt=wkt,
                fuente="OpenStreetMap",
            ))
        return records

    def fetch(self, variables: list[str], anio: int | None = None,
              codigo_divipola: str | None = None,
              bbox_respaldo: tuple[float, float, float, float] | None = None) -> list[GeometryRecord]:
        if "conectividad_vial" not in variables:
            return []

        try:
            records = self._fetch_invias(codigo_divipola)
            if records:
                logger.info(f"Vias obtenidas de INVIAS: {len(records)} segmentos")
                return records
            logger.warning("INVIAS respondio pero sin geometrias -- intentando respaldo OSM")
        except Exception as e:
            logger.warning(f"INVIAS fallo ({e}) -- intentando respaldo OSM")

        if bbox_respaldo is None:
            raise RuntimeError(
                "INVIAS no disponible/vacio y no se dio bbox_respaldo para "
                "consultar OpenStreetMap. No se puede obtener conectividad vial."
            )

        records = self._fetch_osm(codigo_divipola or "", bbox_respaldo)
        logger.info(f"Vias obtenidas de OpenStreetMap (respaldo): {len(records)} segmentos")
        return records
