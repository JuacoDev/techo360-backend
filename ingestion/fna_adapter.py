"""
Adaptador del Fondo Nacional del Ahorro (FNA) -- dataset "Proyectos
vivienda nueva".

>>> ESTADO: BLOQUEADO, confirmado con evidencia real, no es una
>>> suposicion <<<

El dataset "8njd-mfua" ("Proyectos vivienda nueva") si existio y estuvo
activo en datos.gov.co -- hay registro de acceso real de 2021
(confirmado via busqueda). Pero al consultar el endpoint real desde la
VPS (con red real hacia datos.gov.co, algo que nunca se pudo probar
antes desde este entorno de desarrollo), la API respondio:

    404 Not Found — https://www.datos.gov.co/resource/8njd-mfua.json

Se investigo si el dataset se movio a otro ID o portal, sin encontrar
un reemplazo confirmado. Todo indica que el FNA dio de baja este
dataset del portal en algun momento despues de 2021 -- no es un
problema de nombres de columna ni de la forma de consultarlo, es que
el dataset ya no esta publicado ahi.

Variable objetivo: "Cercania a constructoras o proyectos de vivienda
accesible".

Para desbloquear esto: buscar si el FNA republico esta informacion en
otro lugar (su propio portal mivivienda.fna.gov.co, que si tiene un
buscador web de proyectos, aunque sin confirmar si expone una API), o
aceptar que esta variable especifica queda fuera de alcance del
proyecto por ahora.
"""
import logging

import httpx

from ingestion.base import DataSourceAdapter, DataSourceUnavailableError, HTTP_HEADERS, IndicadorRecord
from ingestion.validacion_esquema import verificar_esquema

logger = logging.getLogger(__name__)

BASE_URL = "https://www.datos.gov.co/resource/8njd-mfua.json"

COLUMNAS_ESPERADAS = {"municipio", "tipo_vivienda", "nombre_proyecto"}


class FNAAdapter(DataSourceAdapter):
    nombre = "FNA"
    disponible = False
    motivo_bloqueo = (
        "El dataset '8njd-mfua' (Proyectos vivienda nueva) devuelve 404 "
        "Not Found al consultarlo -- confirmado con una peticion real "
        "desde la VPS, no es una suposicion. El dataset parece haber "
        "sido dado de baja del portal datos.gov.co despues de 2021. No "
        "se encontro un ID de reemplazo confirmado."
    )

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    def obtener_muestra(self, limite: int = 5) -> list[dict]:
        """Se deja el metodo por si en el futuro se encuentra un dataset
        de reemplazo -- hoy en dia va a fallar con 404, igual que fetch()."""
        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            response = client.get(BASE_URL, params={"$limit": limite})
            response.raise_for_status()
            return response.json()

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        raise DataSourceUnavailableError(self.motivo_bloqueo)
