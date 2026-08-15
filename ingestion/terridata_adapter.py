"""
Adaptador de TerriData (DNP), via su espejo en datos.gov.co (Socrata API).

>>> ESTADO: BLOQUEADO, confirmado con evidencia real, no es una
>>> suposicion -- y esto tiene impacto grande, no es un detalle menor <<<

El dataset "64cq-xb2k" (DNP-Terridata) devuelve 404 al consultarlo,
confirmado con una peticion real desde la VPS. Se descarto que sea un
problema del portal completo: otro dataset conocido y estable
(gdxc-w37w, DIVIPOLA - Codigos municipios) SI responde 200 desde la
misma VPS, en el mismo momento. El problema es especifico de este
dataset, no del portal `datos.gov.co` en general.

Se investigo si el dataset se movio a otro ID dentro de datos.gov.co,
sin encontrar reemplazo. Se encontro evidencia de que TerriData migro
a un portal propio del DNP (aparece tanto `terridata.dnp.gov.co` como
un portal aparente mas nuevo, `ddtspr.dnp.gov.co/TDPR/`) -- es decir,
la herramienta sigue viva y mantenida, pero el espejo en datos.gov.co
del que dependia este adaptador fue descontinuado.

IMPACTO: esto bloquea 6 de las 7 variables originales del proyecto
(todas las que dependian de TerriData): deficit habitacional
(cuantitativo y cualitativo), densidad poblacional, cobertura de
acueducto y alcantarillado, y tenencia informal de terrenos. Solo
"conectividad_vial" (INVIAS/OSM) sigue con fuente activa.

Para desbloquear esto se necesita algo que NO se puede hacer solo con
busquedas: inspeccionar el trafico de red (pestaña Network del
navegador) mientras se navega `ddtspr.dnp.gov.co/TDPR/` o
`terridata.dnp.gov.co`, para ver si el visor hace llamadas a alguna
API interna que se pueda reusar (patron comun en dashboards
gubernamentales hechos en JS). Esto requiere que alguien del equipo
lo haga manualmente en un navegador real.
"""
import logging

import httpx

from ingestion.base import HTTP_HEADERS, DataSourceAdapter, DataSourceUnavailableError, IndicadorRecord
from ingestion.validacion_esquema import verificar_esquema

logger = logging.getLogger(__name__)

BASE_URL = "https://www.datos.gov.co/resource/64cq-xb2k.json"

# Columnas que se esperan siempre en la respuesta de TerriData -- si
# alguna vez faltan, es señal de que la fuente cambio de esquema (ver
# ingestion/validacion_esquema.py).
COLUMNAS_ESPERADAS = {"codigo_entidad_territorial", "entidad_territorial", "variable", "dato", "año"}

MAPEO_VARIABLES = {
    "deficit_habitacional_cuantitativo": "Déficit cuantitativo de vivienda",
    "deficit_habitacional_cualitativo": "Déficit cualitativo de vivienda",
    "densidad_poblacional": "Densidad poblacional",
    "cobertura_acueducto": "Cobertura de acueducto",
    "cobertura_alcantarillado": "Cobertura de alcantarillado",
    "cobertura_energia_rural": "Cobertura de energía eléctrica rural",
    "cobertura_internet": "Cobertura de internet",
}

VARIABLES_PENDIENTES_DE_RESOLVER = {
    "tenencia_informal_terrenos": ["tenencia", "posesión", "posesion", "sin título", "sin titulo"],
}


class TerriDataAdapter(DataSourceAdapter):
    nombre = "TerriData"
    disponible = False
    motivo_bloqueo = (
        "El dataset '64cq-xb2k' devuelve 404 -- confirmado con una peticion "
        "real desde la VPS, y se descarto que sea el portal completo (otro "
        "dataset conocido, gdxc-w37w, SI responde 200 al mismo tiempo). "
        "TerriData parece haber migrado a un portal propio del DNP "
        "(terridata.dnp.gov.co / ddtspr.dnp.gov.co), sin dejar un "
        "reemplazo publicado en datos.gov.co. Bloquea 6 de las 7 "
        "variables originales del proyecto -- ver el docstring del "
        "modulo para el detalle completo y como desbloquearlo."
    )

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout
        self._cache_resueltas: dict[str, str] = {}

    def buscar_variables_por_palabra_clave(self, palabra_clave: str) -> list[str]:
        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            params = {
                "$select": "variable",
                "$where": f"upper(variable) like upper('%{palabra_clave}%')",
                "$group": "variable",
            }
            response = client.get(BASE_URL, params=params)
            response.raise_for_status()
            filas = response.json()
        return sorted({fila["variable"] for fila in filas if "variable" in fila})

    def _resolver_variable_pendiente(self, var_interna: str) -> str:
        if var_interna in self._cache_resueltas:
            return self._cache_resueltas[var_interna]

        candidatas = VARIABLES_PENDIENTES_DE_RESOLVER.get(var_interna)
        if candidatas is None:
            raise ValueError(f"'{var_interna}' no esta ni en MAPEO_VARIABLES ni en VARIABLES_PENDIENTES_DE_RESOLVER")

        for palabra in candidatas:
            encontradas = self.buscar_variables_por_palabra_clave(palabra)
            if encontradas:
                elegida = encontradas[0]
                logger.info(
                    f"'{var_interna}' resuelta como '{elegida}' (buscando '{palabra}'). "
                    f"Otras coincidencias encontradas: {encontradas[1:] or 'ninguna'}"
                )
                self._cache_resueltas[var_interna] = elegida
                return elegida

        raise ValueError(
            f"No se encontro ninguna variable en TerriData para '{var_interna}' "
            f"probando: {candidatas}."
        )

    def obtener_muestra(self, limite: int = 5) -> list[dict]:
        """Trae una muestra pequeña, para verificar el esquema antes de
        traer todo (ver ingestion/validacion_esquema.py)."""
        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            response = client.get(BASE_URL, params={"$limit": limite})
            response.raise_for_status()
            return response.json()

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        if not self.disponible:
            raise DataSourceUnavailableError(self.motivo_bloqueo)

        # Verificar esquema ANTES de traer todo -- si TerriData cambio de
        # formato, esto lanza EsquemaCambiadoError y run.py lo captura
        # para alertar al admin, en vez de traer datos mal formados.
        muestra = self.obtener_muestra(limite=5)
        verificar_esquema(self.nombre, muestra, COLUMNAS_ESPERADAS)

        records: list[IndicadorRecord] = []

        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            for var_interna in variables:
                if var_interna in MAPEO_VARIABLES:
                    var_terridata = MAPEO_VARIABLES[var_interna]
                elif var_interna in VARIABLES_PENDIENTES_DE_RESOLVER:
                    var_terridata = self._resolver_variable_pendiente(var_interna)
                else:
                    raise ValueError(f"'{var_interna}' no esta mapeada.")

                params = {"$where": f"variable='{var_terridata}'", "$limit": 5000}
                if anio is not None:
                    params["año"] = anio

                response = client.get(BASE_URL, params=params)
                response.raise_for_status()
                filas = response.json()

                for fila in filas:
                    try:
                        records.append(IndicadorRecord(
                            codigo_divipola=fila.get("codigo_entidad_territorial", "").strip(),
                            entidad_territorial=fila.get("entidad_territorial", "").strip(),
                            variable=var_interna,
                            valor=float(fila.get("dato", "nan")),
                            anio=int(fila.get("año", 0)),
                            fuente=self.nombre,
                        ))
                    except (TypeError, ValueError):
                        continue

        return records
