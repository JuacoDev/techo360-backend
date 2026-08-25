from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_db


router = APIRouter(
    prefix="/api/zonas",
    tags=["zonas"]
)


@router.get("")
def obtener_zonas(
    nivel: str = Query(
        ...,
        pattern="^(departamento|municipio)$",
        description="Nivel geográfico: departamento o municipio"
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

            features.append({
                "type": "Feature",
                "geometry": zona["geometry"],
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