"""
Criterios de aceptacion para los datos que llegan de las fuentes externas,
antes de que se hagan UPSERT en la base. La idea: un dato que no pasa
estos criterios se descarta y se alerta, en vez de corromper el
historico silenciosamente.
"""
import math
from dataclasses import dataclass

from ingestion.base import IndicadorRecord

# Cuanto puede desviarse un valor nuevo respecto al del mismo municipio+
# variable en el año anterior, antes de considerarse sospechoso.
# 0.6 = no puede caer/subir mas del 60% de un año a otro.
DESVIACION_MAXIMA_RELATIVA = 0.6


class ValidacionError(Exception):
    """El dato no paso los criterios de aceptacion. No se debe insertar."""
    pass


@dataclass
class ResultadoValidacion:
    valido: bool
    advertencia: str | None = None  # paso, pero con algo que vale la pena loguear


def validar_indicador(
    record: IndicadorRecord,
    valor_anio_anterior: float | None = None,
) -> ResultadoValidacion:
    """Valida un IndicadorRecord antes de insertarlo. Lanza ValidacionError
    si el dato debe rechazarse por completo; devuelve advertencia (sin
    rechazar) si es sospechoso pero no claramente invalido."""

    # 1. No nulo / no NaN
    if record.valor is None or (isinstance(record.valor, float) and math.isnan(record.valor)):
        raise ValidacionError(
            f"Valor nulo/NaN para {record.variable} en {record.entidad_territorial} ({record.anio})"
        )

    # 2. No negativo (ninguna de nuestras variables actuales admite valores negativos:
    #    son porcentajes, densidades o conteos)
    if record.valor < 0:
        raise ValidacionError(
            f"Valor negativo ({record.valor}) para {record.variable} en "
            f"{record.entidad_territorial} ({record.anio}) -- no es fisicamente posible"
        )

    # 3. Tiene que haber alguna forma de identificar el municipio (codigo
    #    DIVIPOLA O nombre), y la variable no puede venir vacia. No se exige
    #    el codigo DIVIPOLA de forma estricta porque no todas las fuentes lo
    #    dan (ej. FNA probablemente solo da el nombre del municipio) -- basta
    #    con tener AL MENOS una forma de identificar donde es.
    if not (record.codigo_divipola or record.entidad_territorial) or not record.variable:
        raise ValidacionError(
            f"Registro sin forma de identificar el municipio (ni codigo ni "
            f"nombre) o sin variable: {record}"
        )

    # 4. Comparacion contra el año anterior, si se tiene (advertencia, no rechazo
    #    automatico -- una caida real y legitima puede pasar, pero se quiere
    #    que quede visible para revision humana)
    if valor_anio_anterior is not None and valor_anio_anterior > 0:
        desviacion = abs(record.valor - valor_anio_anterior) / valor_anio_anterior
        if desviacion > DESVIACION_MAXIMA_RELATIVA:
            return ResultadoValidacion(
                valido=True,
                advertencia=(
                    f"{record.variable} en {record.entidad_territorial} cambio "
                    f"{desviacion:.0%} respecto a {valor_anio_anterior} "
                    f"(nuevo valor: {record.valor}) en un año -- revisar manualmente"
                ),
            )

    return ResultadoValidacion(valido=True)
