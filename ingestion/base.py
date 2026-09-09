"""
Clase base para los adaptadores de ingesta de datos del proyecto Techo 360.

Cada fuente de datos (TerriData, FNA, INVIAS, etc.) implementa esta
interfaz. Esto permite que el job de actualizacion automatica (definido en
el modulo de Actualizacion Automatica de Datos) trate a todas las fuentes
de la misma forma, sin importar que tan distinta sea cada una por dentro.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

# User-Agent compartido para todas las peticiones HTTP del pipeline.
# Muchos servidores gubernamentales (detras de WAFs tipo Akamai/Cloudflare)
# bloquean o tratan distinto las peticiones con el User-Agent generico que
# pone httpx por defecto -- esto identifica claramente de donde viene la
# peticion, como piden explicitamente las politicas de uso de APIs como
# Overpass.
HTTP_HEADERS = {
    "User-Agent": "Techo360-UPB-Bucaramanga/1.0 (proyecto academico, contacto: juan.tellez@upb.edu.co)"
}


@dataclass
class IndicadorRecord:
    """Formato comun al que TODAS las fuentes de INDICADORES NUMERICOS
    deben convertir sus datos (TerriData, FNA). Esto es lo que finalmente
    se inserta en la tabla de indicadores (PostGIS)."""
    codigo_divipola: str       # codigo DANE del municipio (ej. "68001" = Bucaramanga)
    entidad_territorial: str   # nombre del municipio
    variable: str              # nombre de la variable (ej. "densidad_poblacional")
    valor: float
    anio: int
    fuente: str                # de donde vino el dato (ej. "TerriData", "FNA")
    intervalo_confianza: float | None = None  # solo para filas de relleno/proyeccion -- ver aviso en ingestion/db.py


@dataclass
class GeometryRecord:
    """Formato comun para fuentes que devuelven GEOMETRIA en vez de un
    numero (por ahora, solo vias: INVIAS/OpenStreetMap). No se puede
    mezclar con IndicadorRecord porque la naturaleza del dato es distinta
    -- una linea en el mapa, no un valor escalar."""
    codigo_divipola: str        # municipio al que pertenece (o mas cercano)
    tipo_via: str                # ej. "primaria", "secundaria", "terciaria"
    geometria_wkt: str           # geometria en formato WKT (Well-Known Text), lista para PostGIS
    fuente: str                  # "INVIAS" o "OpenStreetMap"


class DataSourceAdapter(ABC):
    """Interfaz que debe implementar cada fuente de datos."""

    #: Nombre corto de la fuente, usado en logs y en el campo `fuente`
    nombre: str = "sin-nombre"

    #: Si es False, el adaptador existe pero se sabe que no puede correr
    #: todavia (ver el atributo `motivo_bloqueo`) -- el runner lo omite
    #: y lo reporta como pendiente, en vez de fallar.
    disponible: bool = True

    #: Si `disponible` es False, aqui se explica por que (para que quede
    #: documentado y no haya que volver a investigarlo desde cero).
    motivo_bloqueo: str = ""

    @abstractmethod
    def fetch(self, variables: list[str], anio: int | None = None):
        """Descarga y transforma los datos de esta fuente al formato comun
        (lista de IndicadorRecord o GeometryRecord, segun el adaptador).
        Debe lanzar DataSourceUnavailableError si `disponible` es False."""
        raise NotImplementedError


class DataSourceUnavailableError(Exception):
    """La fuente existe pero no se puede consultar todavia (ver motivo_bloqueo)."""
    pass
