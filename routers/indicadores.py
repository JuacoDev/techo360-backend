from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.session import get_db
from schemas.indicadores import IndicadorHistoricoResponse
from services.indicador_service import (
    ANIO_INICIO_HISTORICO,
    ANIO_FIN_HISTORICO,
    obtener_historico_indicador,
)


router = APIRouter(
    prefix="/api/indicadores",
    tags=["indicadores"],
)


@router.get(
    "/{codigo_divipola}/{variable}/historico",
    response_model=IndicadorHistoricoResponse,
    summary="Consultar evolución histórica de un indicador",
    description=(
        "Devuelve la serie histórica del indicador para un municipio "
        "entre 2008 y 2024, diferenciando datos observados, datos "
        "completados mediante backfill y años sin información."
    ),
)
def obtener_historico(
    codigo_divipola: str,
    variable: str,
    db: Session = Depends(get_db),
):
    codigo_divipola = codigo_divipola.strip()
    variable = variable.strip()

    if not codigo_divipola:
        raise HTTPException(
            status_code=400,
            detail="El código DIVIPOLA es obligatorio.",
        )

    if not variable:
        raise HTTPException(
            status_code=400,
            detail="La variable es obligatoria.",
        )

    serie = obtener_historico_indicador(
        db,
        codigo_divipola,
        variable,
    )

    return {
        "codigo_divipola": codigo_divipola,
        "variable": variable,
        "anio_inicio": ANIO_INICIO_HISTORICO,
        "anio_fin": ANIO_FIN_HISTORICO,
        "serie": serie,
    }