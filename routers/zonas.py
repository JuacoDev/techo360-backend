import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from routers.auth import get_current_user
from schemas.zonas import GeoJSONFeatureCollection


router = APIRouter(
    prefix="/api/zonas",
    tags=["zonas"]
)

# Las 7 variables del choropleth, listadas explicitamente (no generadas
# desde lo que exista en MapCalorDB para el anio pedido) -- asi, si a
# una variable le falta el anio pedido por completo, igual aparece en
# la respuesta con valor null, en vez de desaparecer del JSON. Ver
# ingestion/db.py para el estado real de cobertura de cada una (Sprint 3).
VARIABLES_INDICADORES = [
    "densidad_poblacional",
    "cobertura_acueducto",
    "cobertura_alcantarillado",
    "tenencia_informal_terrenos",
    "deficit_habitacional_cuantitativo",
    "deficit_habitacional_cualitativo",
    "deficit_habitacional_total",
]

ANIO_DEFAULT = 2024

# '01001' ('Colombia') es el agregado NACIONAL que TerriData mezcla con
# las filas de municipio real para densidad_poblacional/cobertura_
# acueducto/cobertura_alcantarillado -- confirmado en vivo (set-diff
# mapcalordb vs GeoJSON de municipios, sep-2026): 75 filas, años
# 2000-2042. No es un municipio ni tiene poligono en el GeoJSON.
#
# Se filtra ACA (ademas de en ingestion/terridata_adapter.py, ver
# _es_agregado_nacional() ahi) como defensa en profundidad: este WHERE
# protege cualquier consumidor de GET /api/zonas contra las 75 filas ya
# existentes en mapcalordb HOY (el fix del ingestor solo evita que se
# reintroduzca en una carga futura, no limpia lo que ya esta guardado).
CODIGO_AGREGADO_NACIONAL_TERRIDATA = "01001"

# Las 3 variables que entran al score compuesto de privacion (expuesto
# como score_privacion -- "Score_privación" en el documento de
# metodologia, sin tilde/mayuscula aqui por consistencia con el resto
# de nombres de campo de la API). Alkire-Foster / IPM adaptado, sin el
# umbral k=33% oficial -- aqui es un promedio simple de 3 componentes
# con peso igual 1/3 cada uno.
# Acueducto y alcantarillado se INVIERTEN (1 - normalizado) porque mas
# cobertura = menos privacion; deficit_habitacional_total ya viene en
# la direccion correcta (mas deficit = mas privacion), no se invierte.
# densidad_poblacional queda fuera a proposito -- es ponderador visual
# de tamaño/opacidad en el frontend, no se mezcla en el color del mapa.
VARIABLE_ACUEDUCTO = "cobertura_acueducto"
VARIABLE_ALCANTARILLADO = "cobertura_alcantarillado"
VARIABLE_DEFICIT_COMPUESTO = "deficit_habitacional_total"


def _normalizar_minmax(valor: float | None, minimo: float | None, maximo: float | None) -> float | None:
    """Normalizacion min-max a [0, 1]. None si falta cualquier insumo o
    si el rango es degenerado (todas las filas con el mismo valor -- no
    hay forma de normalizar sin inventar un 0 u 1 arbitrario)."""
    if valor is None or minimo is None or maximo is None or maximo == minimo:
        return None
    return round((valor - minimo) / (maximo - minimo), 4)


def _score_privacion(indicadores_zona: dict) -> float | None:
    """Score compuesto de privacion (0=mejor, 1=peor), promedio simple
    1/3 + 1/3 + 1/3 de acueducto invertido, alcantarillado invertido, y
    deficit habitacional total (sin invertir). None si falta cualquiera
    de los 3 insumos -- incluye el caso MpCodigo=00000 (Area en Litigio
    Cauca-Huila), que no tiene fila real en mapcalordb para ninguna
    variable, asi que cae aqui de forma natural sin caso especial."""
    acueducto = indicadores_zona.get(VARIABLE_ACUEDUCTO, {}).get("score_normalizado")
    alcantarillado = indicadores_zona.get(VARIABLE_ALCANTARILLADO, {}).get("score_normalizado")
    deficit = indicadores_zona.get(VARIABLE_DEFICIT_COMPUESTO, {}).get("score_normalizado")

    if acueducto is None or alcantarillado is None or deficit is None:
        return None

    return round((1 - acueducto) / 3 + (1 - alcantarillado) / 3 + deficit / 3, 4)


@router.get(
    "",
    response_model=GeoJSONFeatureCollection,
    summary="Obtener zonas geográficas",
    description=(
        "Obtiene las zonas geográficas de Techo 360 en formato GeoJSON. "
        "Permite consultar departamentos o municipios. Cuando nivel="
        "municipio, cada feature incluye ademas properties.anio y "
        "properties.indicadores (valor/origen/intervalo_confianza por "
        "variable, para el año pedido)."
    ),
    responses={
        200: {
            "description": "Zonas obtenidas correctamente."
        },
        401: {
            "description": "Requiere sesión iniciada -- envíe el token en el header Authorization: Bearer <token>."
        },
        422: {
            "description": "El parámetro nivel no tiene un valor válido."
        },
        500: {
            "description": "Error al consultar las zonas en la base de datos."
        }
    }
)
def obtener_zonas(
    nivel: str = Query(
        ...,
        pattern="^(departamento|municipio)$",
        description="Nivel geográfico que se desea consultar."
    ),
    anio: int = Query(
        ANIO_DEFAULT,
        description=(
            "Año para el que se quieren los indicadores (solo aplica "
            "con nivel=municipio). Un unico año se aplica a las 7 "
            "variables por igual -- si una variable no tiene dato para "
            "ese año, su valor sale null (no es un error)."
        )
    ),
    db: Session = Depends(get_db),
    # Requiere login (decision de producto, no solo el chatbot -- toda la
    # app necesita cuenta: trazabilidad de usuarios, logs, vinculo con
    # historial del chatbot). Sin sistema de roles todavia -- cualquier
    # usuario autenticado puede consultar, sin distincion de tipo (eso
    # queda para un futuro panel de administrador, fuera de este cambio).
    current_user: MapCalorUser = Depends(get_current_user)
):
    if nivel == "departamento":
        query = text("""
            SELECT
                codigo_departamento AS codigo,
                nombre,
                ST_AsGeoJSON(geom) AS geometry
            FROM zonas_departamento
            ORDER BY nombre
        """)

    else:
        query = text("""
            SELECT
                codigo_municipio AS codigo,
                codigo_departamento,
                nombre,
                ST_AsGeoJSON(geom) AS geometry
            FROM zonas_municipio
            ORDER BY nombre
        """)

    try:
        resultados = db.execute(query).mappings().all()

        # --- Indicadores por municipio, UNA sola query para las 7
        # variables del año pedido (evita N+1 contra ~1,105 zonas x 7
        # variables) -- se arma un dict en memoria, indexado por
        # (codigodivipola, namevar), y se cruza abajo por Feature. ---
        indicadores_por_codigo: dict[str, dict[str, dict]] = {}
        if nivel == "municipio":
            query_indicadores = text("""
                SELECT codigodivipola, namevar, valuevar, originvar, intervaloconfianza
                FROM public.mapcalordb
                WHERE yearm = :anio
                  AND namevar = ANY(:variables)
                  AND codigodivipola IS NOT NULL
                  AND codigodivipola != :codigo_agregado_nacional
            """)
            filas_indicadores = db.execute(
                query_indicadores,
                {
                    "anio": anio,
                    "variables": VARIABLES_INDICADORES,
                    "codigo_agregado_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
                },
            ).mappings().all()

            # Min-max por variable, calculado sobre TODAS las filas de
            # filas_indicadores (las 1,105 zonas reales del año pedido,
            # sin paginar ni filtrar) -- nunca sobre un subconjunto, para
            # que el score_normalizado sea comparable entre municipios.
            # MpCodigo=00000 no tiene fila aqui (mapcalordb no tiene
            # datos para ese codigo), asi que queda fuera del min-max
            # de forma natural, sin caso especial.
            valores_por_variable: dict[str, list[float]] = {v: [] for v in VARIABLES_INDICADORES}
            for fila in filas_indicadores:
                if fila["valuevar"] is not None:
                    valores_por_variable[fila["namevar"]].append(float(fila["valuevar"]))

            minmax_por_variable: dict[str, tuple[float | None, float | None]] = {
                variable: (min(valores), max(valores)) if valores else (None, None)
                for variable, valores in valores_por_variable.items()
            }

            for fila in filas_indicadores:
                codigo = fila["codigodivipola"]
                variable = fila["namevar"]
                valor = float(fila["valuevar"]) if fila["valuevar"] is not None else None
                minimo, maximo = minmax_por_variable.get(variable, (None, None))

                indicadores_por_codigo.setdefault(codigo, {})[variable] = {
                    "valor": valor,
                    # Se expone el originVar real (ej. "TerriData",
                    # "ultimo-valor-observado", "DANE-CNPV2018"), no un
                    # generico "estimado" -- el frontend necesita poder
                    # distinguir dato real de fuente X vs. estimado por
                    # metodo Y, no solo "es una estimacion" (decision
                    # documentada aqui, no solo en el codigo).
                    "origen": fila["originvar"],
                    "intervalo_confianza": (
                        float(fila["intervaloconfianza"])
                        if fila["intervaloconfianza"] is not None
                        else None
                    ),
                    # Normalizacion min-max [0,1] de esta variable sola
                    # (T-49): permite al frontend pintar el choropleth
                    # por UNA variable elegida por el usuario, en vez
                    # del score compuesto. NO invertida -- es el valor
                    # crudo reescalado, sin semantica de "privacion".
                    "score_normalizado": _normalizar_minmax(valor, minimo, maximo),
                }
        else:  # último valor disponible para departamentos
            query_indicadores_depto = text("""
                SELECT DISTINCT ON (zm.codigo_departamento, m.namevar)
                       zm.codigo_departamento,
                       m.namevar,
                       m.valuevar,
                       m.originvar,
                       m.yearm
                FROM public.mapcalordb m
                JOIN zonas_municipio zm ON m.codigodivipola = zm.codigo_municipio
                WHERE m.namevar = ANY(:variables)
                  AND zm.codigo_departamento IS NOT NULL
                ORDER BY zm.codigo_departamento, m.namevar, m.yearm DESC
            """)
            filas_indicadores_depto = db.execute(
                query_indicadores_depto, {"variables": VARIABLES_INDICADORES}
            ).mappings().all()

            for fila in filas_indicadores_depto:
                codigo = fila["codigo_departamento"]
                indicadores_por_codigo.setdefault(codigo, {})[fila["namevar"]] = {
                    "valor": float(fila["valuevar"]) if fila["valuevar"] is not None else None,
                    "origen": fila["originvar"],
                    "intervalo_confianza": None,
                    "anio_real": fila["yearm"]
                }

        features = []

        for zona in resultados:
            properties = {
                "codigo": zona["codigo"],
                "nombre": zona["nombre"]
            }

            if nivel == "municipio":
                properties["codigo_departamento"] = zona["codigo_departamento"]
                properties["anio"] = anio
                indicadores_zona = indicadores_por_codigo.get(zona["codigo"], {})
            else:
                properties["anio"] = anio
                indicadores_zona = indicadores_por_codigo.get(zona["codigo"], {})

            # Aquí usamos indicadores_por_codigo en ambos casos
            properties["indicadores"] = {
                variable: indicadores_zona.get(
                    variable,
                    {
                        "valor": None,
                        "origen": None,
                        "intervalo_confianza": None,
                        "anio_real": None,
                        "score_normalizado": None,
                    },
                )
                for variable in VARIABLES_INDICADORES
            }

            if nivel == "municipio":
                # Score compuesto de privacion (T-49) -- None
                # para MpCodigo=00000 (Area en Litigio) de forma natural,
                # ya que esa zona no tiene score_normalizado en ninguna
                # de las 3 variables que entran al compuesto. Solo se
                # calcula a nivel municipio (el choropleth del mapa es
                # a este nivel; departamento no lo necesita hoy).
                properties["score_privacion"] = _score_privacion(properties["indicadores"])

            features.append({
                "type": "Feature",
                "geometry": json.loads(zona["geometry"]),
                "properties": properties
            })

        return {
            "type": "FeatureCollection",
            "features": features
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error consultando las zonas: {str(e)}"
        )