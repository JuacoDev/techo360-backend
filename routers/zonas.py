import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_db
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
    db: Session = Depends(get_db)
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
            """)
            filas_indicadores = db.execute(
                query_indicadores, {"anio": anio, "variables": VARIABLES_INDICADORES}
            ).mappings().all()

            for fila in filas_indicadores:
                codigo = fila["codigodivipola"]
                indicadores_por_codigo.setdefault(codigo, {})[fila["namevar"]] = {
                    "valor": float(fila["valuevar"]) if fila["valuevar"] is not None else None,
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
                }

        features = []

        for zona in resultados:
            properties = {
                "codigo": zona["codigo"],
                "nombre": zona["nombre"]
            }

            if nivel == "municipio":
                properties["codigo_departamento"] = (
                    zona["codigo_departamento"]
                )
                properties["anio"] = anio

                # zona["codigo"] == "00000" (Area en Litigio Cauca-Huila)
                # nunca tiene fila en MapCalorDB -- indicadores_por_codigo
                # simplemente no tiene esa clave, y el .get(...) de abajo
                # cae al default de 3 campos null para las 7 variables,
                # sin caso especial adicional.
                indicadores_zona = indicadores_por_codigo.get(zona["codigo"], {})
                properties["indicadores"] = {
                    variable: indicadores_zona.get(
                        variable,
                        {"valor": None, "origen": None, "intervalo_confianza": None},
                    )
                    for variable in VARIABLES_INDICADORES
                }

            features.append({
                "type": "Feature",
                # ST_AsGeoJSON() devuelve TEXT, no un dict -- sin este
                # json.loads(), la respuesta nunca pasaba la validacion
                # de GeoJSONFeature.geometry (dict[str, Any]), bug
                # preexistente detectado al probar este endpoint en vivo
                # por primera vez (30/31-ago-2026), no introducido por
                # el cambio de indicadores/anio de esta tarea.
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