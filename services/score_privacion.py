"""Score compuesto de privacion -- extraido de routers/zonas.py (T-49)
a un modulo compartido para que routers/proyecciones.py tambien pueda
usarlo (score de privacion por proyeccion). Las dos funciones de abajo
(normalizar_minmax, calcular_score_privacion) son EXACTAMENTE el mismo
codigo que tenia routers/zonas.py, sin cambiar su comportamiento -- ver
ese modulo para el caso de uso "real": ahi los limites min/max se
calculan sobre el conjunto de municipios del AÑO PEDIDO (dinamico, se
recalcula en cada consulta).

Notese que calcular_score_privacion() NO recibe limites como parametro:
solo consume un `score_normalizado` ya calculado por variable. Quien
decide que limites usar es quien arma `indicadores_zona` ANTES de
llamarla -- routers/zonas.py sigue llamando normalizar_minmax() con el
min/max del propio año (comportamiento identico a antes); el camino de
proyeccion (routers/proyecciones.py) llama normalizar_minmax() con los
limites FIJOS del año base 2024 (ver calcular_limites_base() mas abajo)
antes de pasarle el resultado a esta misma funcion. La formula en si no
sabe ni le importa de donde salieron esos limites -- asi se evita
cambiar su contrato/comportamiento para el camino real.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

VARIABLE_ACUEDUCTO = "cobertura_acueducto"
VARIABLE_ALCANTARILLADO = "cobertura_alcantarillado"
VARIABLE_DEFICIT_COMPUESTO = "deficit_habitacional_cuantitativo"

VARIABLES_SCORE = [VARIABLE_ACUEDUCTO, VARIABLE_ALCANTARILLADO, VARIABLE_DEFICIT_COMPUESTO]

# Mismo codigo nacional agregado que routers/zonas.py excluye de todas
# sus consultas contra mapcalordb (ver CODIGO_AGREGADO_NACIONAL_TERRIDATA
# ahi) -- se repite aqui porque calcular_limites_base() hace su propia
# consulta, independiente de la de ese router.
CODIGO_AGREGADO_NACIONAL_TERRIDATA = "01001"


def normalizar_minmax(valor: float | None, minimo: float | None, maximo: float | None) -> float | None:
    """Normalizacion min-max a [0, 1]. None si falta cualquier insumo o
    si el rango es degenerado (todas las filas con el mismo valor -- no
    hay forma de normalizar sin inventar un 0 u 1 arbitrario)."""
    if valor is None or minimo is None or maximo is None or maximo == minimo:
        return None
    return round((valor - minimo) / (maximo - minimo), 4)


def calcular_score_privacion(indicadores_zona: dict) -> float | None:
    """Score compuesto de privacion (0=mejor, 1=peor), promedio simple
    1/3 + 1/3 + 1/3 de acueducto invertido, alcantarillado invertido, y
    deficit habitacional CUANTITATIVO (sin invertir -- ver el aviso de
    doble conteo en el routers/zonas.py original: el cuantitativo, a
    diferencia del total, no contiene componentes de acueducto/
    alcantarillado). None si falta cualquiera de los 3 insumos -- incluye
    el caso MpCodigo=00000 (Area en Litigio Cauca-Huila), que no tiene
    fila real en mapcalordb para ninguna variable, asi que cae aqui de
    forma natural sin caso especial."""
    acueducto = indicadores_zona.get(VARIABLE_ACUEDUCTO, {}).get("score_normalizado")
    alcantarillado = indicadores_zona.get(VARIABLE_ALCANTARILLADO, {}).get("score_normalizado")
    deficit = indicadores_zona.get(VARIABLE_DEFICIT_COMPUESTO, {}).get("score_normalizado")

    if acueducto is None or alcantarillado is None or deficit is None:
        return None

    return round((1 - acueducto) / 3 + (1 - alcantarillado) / 3 + deficit / 3, 4)


def calcular_limites_base(db: Session, anio_base: int) -> dict[str, tuple[float | None, float | None]]:
    """Min/max de cada una de las 3 variables del score, sobre TODAS las
    filas reales de `anio_base` (sin paginar ni filtrar) -- mismo patron
    que el min-max dinamico de routers/zonas.py, pero fijado a un solo
    año en vez de recalcularse por request. Se llama una sola vez (ver
    services/proyeccion_cache.py::obtener_limites_base/guardar_limites_base
    para el cacheo) y el resultado se reutiliza para normalizar TODOS los
    años proyectados, de forma que el score proyectado y el score real
    del año base queden en la MISMA escala."""
    query = text("""
        SELECT namevar, valuevar
        FROM public.mapcalordb
        WHERE yearm = :anio
          AND namevar = ANY(:variables)
          AND codigodivipola IS NOT NULL
          AND codigodivipola != :codigo_agregado_nacional
    """)
    filas = db.execute(
        query,
        {
            "anio": anio_base,
            "variables": VARIABLES_SCORE,
            "codigo_agregado_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
        },
    ).mappings().all()

    valores_por_variable: dict[str, list[float]] = {v: [] for v in VARIABLES_SCORE}
    for fila in filas:
        if fila["valuevar"] is not None:
            valores_por_variable[fila["namevar"]].append(float(fila["valuevar"]))

    return {
        variable: (min(valores), max(valores)) if valores else (None, None)
        for variable, valores in valores_por_variable.items()
    }


def obtener_deficit_base(db: Session, anio_base: int) -> dict[str, float | None]:
    """Valor de deficit_habitacional_cuantitativo del año base por
    municipio -- se reutiliza SIN cambios para todos los años
    proyectados (decision de producto: el deficit no se proyecta por
    regresion, ver app/services/proyeccion.py::INDICADORES_SOPORTADOS en
    techo360-proyeccion para el porque)."""
    query = text("""
        SELECT codigodivipola, valuevar
        FROM public.mapcalordb
        WHERE yearm = :anio
          AND namevar = :variable
          AND codigodivipola IS NOT NULL
          AND codigodivipola != :codigo_agregado_nacional
    """)
    filas = db.execute(
        query,
        {
            "anio": anio_base,
            "variable": VARIABLE_DEFICIT_COMPUESTO,
            "codigo_agregado_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
        },
    ).mappings().all()

    return {
        fila["codigodivipola"]: (float(fila["valuevar"]) if fila["valuevar"] is not None else None)
        for fila in filas
    }
