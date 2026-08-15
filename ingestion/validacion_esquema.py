"""
Verifica que una fuente externa no haya cambiado de esquema (columnas
renombradas o eliminadas) ANTES de traer el dataset completo. Las APIs
de gobierno cambian de formato sin avisar con más frecuencia de lo que
uno esperaría -- mejor detectarlo con una muestra pequeña y detenerse,
que insertar filas rotas o vacías silenciosamente.
"""


class EsquemaCambiadoError(Exception):
    """La fuente ya no tiene las columnas que se esperaban."""

    def __init__(self, fuente: str, columnas_faltantes: set[str], columnas_recibidas: set[str]):
        self.fuente = fuente
        self.columnas_faltantes = columnas_faltantes
        self.columnas_recibidas = columnas_recibidas
        super().__init__(
            f"[{fuente}] Cambio de esquema detectado. "
            f"Columnas esperadas que ya no aparecen: {columnas_faltantes}. "
            f"Columnas que si llegaron: {columnas_recibidas}"
        )


def verificar_esquema(fuente: str, muestra: list[dict], columnas_esperadas: set[str]) -> None:
    """Compara las columnas de una muestra pequeña contra las esperadas.
    Lanza EsquemaCambiadoError si falta alguna -- no continua en silencio."""
    if not muestra:
        raise EsquemaCambiadoError(fuente, columnas_esperadas, set())

    columnas_recibidas = set(muestra[0].keys())
    faltantes = columnas_esperadas - columnas_recibidas

    if faltantes:
        raise EsquemaCambiadoError(fuente, faltantes, columnas_recibidas)
