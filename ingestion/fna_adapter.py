"""
Adaptador del Fondo Nacional del Ahorro (FNA) -- dataset "Proyectos
vivienda nueva", tambien via Socrata API (mismo tipo de acceso que
TerriData).

Dataset id: "8njd-mfua"
Endpoint: https://www.datos.gov.co/resource/8njd-mfua.json

Variable objetivo: "Cercania a constructoras o proyectos de vivienda
accesible" -- se calcula a partir de la cantidad de proyectos VIS/VIP
registrados por municipio (no distancia geografica literal, sino
disponibilidad de oferta de vivienda accesible en la zona).

IMPORTANTE: a diferencia de TerriData (donde ya se probaron los nombres
de columna), este dataset NUNCA se ha consultado en vivo desde este
entorno -- no hay red hacia datos.gov.co desde aqui. Los nombres de
columna de abajo (COLUMNAS_ESPERADAS, campos usados en fetch()) son la
mejor suposicion basada en el patron tipico de datasets de vivienda de
Socrata Colombia, NO estan confirmados. La primera corrida en la VPS
debe usar `obtener_muestra()` para confirmarlos y ajustar este archivo
si hace falta -- por eso el metodo existe.
"""
import logging

import httpx

from ingestion.base import DataSourceAdapter, IndicadorRecord

logger = logging.getLogger(__name__)

BASE_URL = "https://www.datos.gov.co/resource/8njd-mfua.json"

# MEJOR SUPOSICION, no confirmada -- ajustar con obtener_muestra() en la
# primera corrida real.
COLUMNAS_ESPERADAS = {"municipio", "tipo_vivienda", "nombre_proyecto"}


class FNAAdapter(DataSourceAdapter):
    nombre = "FNA"
    disponible = True

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    def obtener_muestra(self, limite: int = 5) -> list[dict]:
        """Trae una muestra pequeña para confirmar los nombres reales de
        columna antes de la primera corrida completa -- ver el aviso en
        el docstring del modulo."""
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(BASE_URL, params={"$limit": limite})
            response.raise_for_status()
            return response.json()

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        """Cuenta proyectos VIS/VIP registrados por municipio y lo
        convierte en un IndicadorRecord por municipio (variable =
        'cercania_constructoras_vis', valor = numero de proyectos)."""
        if "cercania_constructoras_vis" not in variables:
            return []

        with httpx.Client(timeout=self._timeout) as client:
            params = {"$select": "municipio, count(*) as total_proyectos", "$group": "municipio"}
            if anio is not None:
                # Filtro de año best-effort -- el nombre real de la columna
                # de fecha/año no esta confirmado, ver docstring del modulo.
                params["$where"] = f"date_extract_y(fecha_registro)={anio}"

            response = client.get(BASE_URL, params=params)
            response.raise_for_status()
            filas = response.json()

        records: list[IndicadorRecord] = []
        for fila in filas:
            try:
                records.append(IndicadorRecord(
                    codigo_divipola="",  # el dataset probablemente da nombre, no codigo DIVIPOLA -- revisar en obtener_muestra()
                    entidad_territorial=fila.get("municipio", "").strip(),
                    variable="cercania_constructoras_vis",
                    valor=float(fila.get("total_proyectos", 0)),
                    anio=anio or 0,
                    fuente=self.nombre,
                ))
            except (TypeError, ValueError):
                continue

        return records
