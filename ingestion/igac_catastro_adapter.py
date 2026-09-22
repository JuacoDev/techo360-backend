"""
Adaptador de IGAC -- Catastro Multiproposito (SINIC), para la variable
"tenencia_informal_terrenos".

Investigado y confirmado en vivo con un navegador real (misma tecnica
que se uso para encontrar el portal real de TerriData): el portal
ArcGIS Enterprise del IGAC (sigi.igac.gov.co) publica una capa real,
publica, actualizada bimestralmente:

    https://sigi.igac.gov.co/habilitacion/rest/services/sinic/SINIC_DA/FeatureServer/1
    (tabla "capa_ladm_predio")

Con un campo `condicion_predio` que incluye directamente el valor
"Informal" (confirmado: 949.981 predios a nivel nacional con ese
valor). Esto es MEJOR que la alternativa que se investigo primero (una
tabla de cruce numero-predial-formal/informal,
"capa_ladm_predio_informalidad") -- esa tabla es un cruce de
identificadores, no da directamente un conteo por municipio.

UNIDAD DE MEDIDA: a diferencia del diseño original (que fue pensado en
base al Censo del DANE, midiendo HOGARES), esta fuente mide PREDIOS
(unidades de terreno), no hogares. Esto se decidio conscientemente
con el equipo -- son conceptos relacionados pero no identicos (un
predio puede tener varios hogares o estar vacio). La formula final:

    % Tenencia Informal = (Predios con condicion_predio='Informal'
                            en el municipio / Total de predios del
                            municipio) x 100

VENTAJA DE RENDIMIENTO: se usa agregacion del lado del servidor
(outStatistics + groupByFieldsForStatistics de la API de ArcGIS) para
pedir CONTEOS ya calculados por municipio, en vez de descargar millones
de predios individuales -- confirmado en vivo: Medellin (05001) tiene
1.150.797 predios totales, y la consulta agregada devuelve el conteo
al instante, sin descargar cada fila.

>>> LIMITACION CONOCIDA, no ocultar (encontrada 22-sep-2026 durante la
auditoria de cobertura de la Fase 2 de scraping, ver
auditoria_cobertura_variables.py) <<<
El 73% de los municipios con fila real (816 de 1.118, verificado en
vivo contra produccion) tienen valuevar=0.0 exacto para
tenencia_informal_terrenos. La causa tecnica esta en fetch() (linea
~122 mas abajo): `informales.get(cod_divipola, (nombre, 0))` trata la
AUSENCIA de cualquier predio con condicion_predio='Informal' en un
municipio como "0% de informalidad real", sin poder distinguir eso de
"el catastro multiproposito todavia no ha llegado a clasificar la
condicion de tenencia en este municipio" -- el campo `total` (linea
119) SI viene poblado (por eso el municipio no se descarta por completo
en la linea 120), pero eso solo confirma que existen predios
catastrados, no que su condicion_predio ya fue evaluada.

Contraste con una fuente independiente (UPRA, Indice de Informalidad en
la Tenencia de la Tierra): segun su metadata oficial
(catalogometadatos.upra.gov.co, uuid c45c0895-43c9-4a2b-b350-a4ca7da900aa,
consultada en vivo 22-sep-2026), de los 1.104 municipios evaluados solo
79 caen en la banda 0%-25% de informalidad -- un panorama radicalmente
distinto al 73% en 0% exacto que produce este adaptador. Esto es
evidencia corroborante (no una prueba matematica directa, las dos
fuentes miden con metodologias distintas) de que el 0.0 de este
adaptador subestima la informalidad real en una parte importante de los
municipios, en vez de reflejarla genuinamente.

UPRA NO se adopta como reemplazo por dos razones confirmadas en vivo
contra su propio catalogo de metadatos: (1) el UNICO formato publicado
es PDF (`<dc:format>PDF</dc:format>` en el XML de metadata, sin
shapefile/CSV/geodatabase ni servicio WFS/REST registrado) -- extraer
un valor por municipio requeriria parsear tablas de un PDF (Cuadro 8,
"Indice de informalidad municipal 2020"), el ultimo recurso segun la
jerarquia de fuentes de este proyecto (API > descarga estructurada >
scraping), no algo a lo que comprometerse sin evaluar el costo real de
ese parseo; (2) la vigencia declarada en el propio titulo del dataset
sigue siendo "2020" -- la fecha `dct:modified=2023-07-19` en la
metadata es cuando se retoco el REGISTRO del catalogo, no una
actualizacion del dato subyacente, confirmado leyendo la descripcion
completa del registro (sigue hablando de "la vigencia 2020" en presente).

Estado: el bug queda documentado pero SIN corregir todavia -- corregirlo
bien (distinguir "0% real" de "catastro no clasificado aqui") requeriria
un campo adicional en MapCalorDB para registrar cobertura de
clasificacion por municipio, o una fuente de reemplazo con mejor
metodologia y formato descargable, ninguna de las cuales se investigo
todavia mas alla de UPRA. No se cambia el pipeline en este commit.
"""
import logging

import httpx

from ingestion.base import DataSourceAdapter, HTTP_HEADERS, IndicadorRecord

logger = logging.getLogger(__name__)

FEATURE_SERVER_URL = "https://sigi.igac.gov.co/habilitacion/rest/services/sinic/SINIC_DA/FeatureServer/1/query"


class IGACCatastroAdapter(DataSourceAdapter):
    nombre = "IGAC Catastro"
    disponible = True

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    def _conteos_por_municipio(self, client: httpx.Client, condicion: str | None = None) -> dict[str, tuple[str, int]]:
        """UNA sola consulta agregada que devuelve el conteo de TODOS
        los municipios de una vez (confirmado en vivo: cientos de
        municipios en una sola respuesta) -- nunca se itera municipio
        por municipio, eso hubiera sido ~2.400 peticiones HTTP para
        todo el pais en vez de 2.

        Se agrupa por cod_divipola Y municipio a la vez (confirmado en
        vivo que ArcGIS soporta agrupar por 2 campos) -- esto evita
        necesitar una tabla de resolucion de nombres aparte, ya que
        capa_ladm_predio ya trae el nombre del municipio como campo
        propio, no solo el codigo.

        Devuelve {cod_divipola: (nombre_municipio, conteo)}.
        """
        where = f"condicion_predio='{condicion}'" if condicion is not None else "1=1"
        params = {
            "where": where,
            "groupByFieldsForStatistics": "cod_divipola,municipio",
            "outStatistics": (
                '[{"statisticType":"count","onStatisticField":"objectid",'
                '"outStatisticFieldName":"total"}]'
            ),
            "resultRecordCount": 2000,  # Colombia tiene ~1122 municipios, cabe en una pagina
            "f": "json",
        }
        response = client.get(FEATURE_SERVER_URL, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("error"):
            raise RuntimeError(f"Error del servidor IGAC: {data['error']}")

        return {
            f["attributes"]["cod_divipola"]: (f["attributes"]["municipio"], f["attributes"]["total"])
            for f in data.get("features", [])
        }

    def calcular_para_municipio(self, codigo_divipola: str) -> float | None:
        """Porcentaje de tenencia informal para UN solo municipio -- para
        verificar rapido en la VPS sin correr el pipeline completo.
        Reusa la misma agregacion de servidor que fetch(), solo filtra
        el resultado a un municipio. None si el municipio no aparece en
        la fuente (o tiene 0 predios totales)."""
        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            totales = self._conteos_por_municipio(client)
            informales = self._conteos_por_municipio(client, condicion="Informal")

        entrada_total = totales.get(codigo_divipola)
        if not entrada_total or not entrada_total[1]:
            return None
        _, total = entrada_total
        _, cantidad_informal = informales.get(codigo_divipola, ("", 0))
        return round((cantidad_informal / total) * 100, 2)

    def fetch(self, variables: list[str], anio: int | None = None) -> list[IndicadorRecord]:
        if "tenencia_informal_terrenos" not in variables:
            return []

        with httpx.Client(timeout=self._timeout, headers=HTTP_HEADERS) as client:
            totales = self._conteos_por_municipio(client)
            informales = self._conteos_por_municipio(client, condicion="Informal")

        records: list[IndicadorRecord] = []
        for cod_divipola, (nombre_municipio, total) in totales.items():
            if total == 0:
                continue
            # informales.get(...) puede no tener este codigo si el
            # municipio no tiene NINGUN predio informal -- eso es
            # legitimo (0%), no un error.
            _, cantidad_informal = informales.get(cod_divipola, (nombre_municipio, 0))
            porcentaje = round((cantidad_informal / total) * 100, 2)
            records.append(IndicadorRecord(
                codigo_divipola=cod_divipola,
                entidad_territorial=nombre_municipio.title(),  # viene en MAYUSCULAS del servidor, se normaliza
                variable="tenencia_informal_terrenos",
                valor=porcentaje,
                anio=anio or 0,
                fuente=self.nombre,
            ))
        return records
