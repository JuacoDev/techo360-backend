from sqlalchemy import text
from sqlalchemy.orm import Session

from services.origen import es_proyeccion


ANIO_INICIO_HISTORICO = 2008
ANIO_FIN_HISTORICO = 2024


# Métodos que representan datos completados/proyectados. Lista HISTÓRICA (incluye nombres viejos sin el
# prefijo "proyeccion-"); todo origen que es_proyeccion() (empieza por "proyeccion-" o termina en
# "-proyeccion", p. ej. los proyeccion-techo360-* de la carga de proyecciones) también cuenta como backfill.
ORIGENES_BACKFILL = {
    "ultimo-valor-observado",
    "interpolacion-lineal",
    "SAE-censo2018",
    "proyeccion-tendencia-nacional-2018-2024",
    "proyeccion-snapshot-2026",
    "LightGBM-proyeccion",
}


def obtener_historico_indicador(
    db: Session,
    codigo_divipola: str,
    variable: str,
) -> list[dict]:
    query = text("""
        SELECT
            yearm,
            valuevar,
            originvar,
            intervaloconfianza
        FROM public.mapcalordb
        WHERE codigodivipola = :codigo_divipola
          AND namevar = :variable
          AND yearm BETWEEN :anio_inicio AND :anio_fin
        ORDER BY yearm
    """)

    filas = db.execute(
        query,
        {
            "codigo_divipola": codigo_divipola,
            "variable": variable,
            "anio_inicio": ANIO_INICIO_HISTORICO,
            "anio_fin": ANIO_FIN_HISTORICO,
        },
    ).mappings().all()

    datos_por_anio = {
        fila["yearm"]: fila
        for fila in filas
    }

    serie = []

    for anio in range(ANIO_INICIO_HISTORICO, ANIO_FIN_HISTORICO + 1):
        fila = datos_por_anio.get(anio)

        if fila is None:
            serie.append(
                {
                    "anio": anio,
                    "valor": None,
                    "origen": None,
                    "tipo": "sin_dato",
                }
            )
            continue

        origen = fila["originvar"]

        if es_proyeccion(origen) or origen in ORIGENES_BACKFILL:
            tipo = "backfill"
        else:
            tipo = "observado"

        serie.append(
            {
                "anio": anio,
                "valor": (
                    float(fila["valuevar"])
                    if fila["valuevar"] is not None
                    else None
                ),
                "origen": origen,
                "tipo": tipo,
            }
        )

    return serie