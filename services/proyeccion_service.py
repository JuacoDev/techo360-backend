from pathlib import Path
from importlib import import_module

from sqlalchemy import text
from sqlalchemy.orm import Session

from schemas.proyecciones import PuntoProyeccion


INDICADOR_VIGENTE = "densidad_poblacional"
MODELO_VIGENTE = "LightGBM"
ORIGEN_PROYECCION = "LightGBM-proyeccion"
HORIZONTE_PROYECCION = 5
RUTA_MODELO = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "lightgbm_densidad_poblacional.txt"
)


class IndicadorNoSoportadoError(ValueError):
    pass


class ModeloNoDisponibleError(RuntimeError):
    pass


class ContratoModeloNoConfiguradoError(RuntimeError):
    pass


def obtener_datos_historicos(
    db: Session,
    codigo_municipio: str,
    indicador: str,
):
    query = text("""
        SELECT yearm, valuevar
        FROM public.mapcalordb
        WHERE codigodivipola = :codigo_municipio
          AND namevar = :indicador
          AND valuevar IS NOT NULL
        ORDER BY yearm
    """)

    return db.execute(
        query,
        {
            "codigo_municipio": codigo_municipio,
            "indicador": indicador,
        },
    ).mappings().all()


def cargar_modelo():
    if not RUTA_MODELO.exists():
        raise ModeloNoDisponibleError(
            "El modelo LightGBM no está disponible."
        )

    try:
        lgb = import_module("lightgbm")
    except ImportError as error:
        raise ModeloNoDisponibleError(
            "La dependencia LightGBM no está instalada."
        ) from error

    try:
        return lgb.Booster(model_file=str(RUTA_MODELO))
    except Exception as error:
        raise ModeloNoDisponibleError(
            "No se pudo cargar el modelo LightGBM."
        ) from error


def construir_caracteristicas(datos_historicos, anios_futuros: list[int]):
    raise ContratoModeloNoConfiguradoError(
        "Las características del modelo LightGBM aún no están configuradas."
    )


def calcular_proyeccion(
    db: Session,
    codigo_municipio: str,
    indicador: str,
) -> list[PuntoProyeccion]:
    if indicador != INDICADOR_VIGENTE:
        raise IndicadorNoSoportadoError(
            f"El indicador soportado es '{INDICADOR_VIGENTE}'."
        )

    datos_historicos = obtener_datos_historicos(
        db,
        codigo_municipio,
        indicador,
    )

    if not datos_historicos:
        raise LookupError("No existen datos para el municipio.")

    modelo = cargar_modelo()
    ultimo_anio = max(int(fila["yearm"]) for fila in datos_historicos)
    anios_futuros = list(
        range(ultimo_anio + 1, ultimo_anio + HORIZONTE_PROYECCION + 1)
    )
    caracteristicas = construir_caracteristicas(
        datos_historicos,
        anios_futuros,
    )
    valores = modelo.predict(caracteristicas)

    return [
        PuntoProyeccion(anio=anio, valor=round(float(valor), 2))
        for anio, valor in zip(anios_futuros, valores)
    ]