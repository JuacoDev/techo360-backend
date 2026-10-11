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

from services.origen import es_real, fiabilidad_de_origen

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
    """Normalizacion min-max, RECORTADA a [0, 1]. None si falta cualquier insumo o
    si el rango es degenerado (todas las filas con el mismo valor -- no
    hay forma de normalizar sin inventar un 0 u 1 arbitrario).

    El recorte (2026-10-10) importa en el camino de proyeccion: los limites son los
    FIJOS de 2024 y un valor de otro año puede caer fuera de ese rango (p. ej. un
    deficit proyectado por debajo del minimo de 2024 daba un normalizado < 0 y un
    score < 0 en Sabaneta). En el camino real (limites del propio año) no cambia nada."""
    if valor is None or minimo is None or maximo is None or maximo == minimo:
        return None
    if valor != valor:  # NaN: dato faltante, no un 0 (max(0.0, nan) devolveria 0.0 y lo ocultaria)
        return None
    return round(min(1.0, max(0.0, (valor - minimo) / (maximo - minimo))), 4)


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

    # Con los tres insumos ya en [0, 1] el score queda en [0, 1]; el recorte final solo
    # protege contra el redondeo y contra un llamador que pase normalizados sin recortar.
    return min(1.0, max(0.0, round((1 - acueducto) / 3 + (1 - alcantarillado) / 3 + deficit / 3, 4)))


# Codigos que NO son municipios y no deben fijar la escala del score: "00000" (Area en
# Litigio, poligono sin municipio) y "01001" (agregado nacional de TerriData).
CODIGOS_FUERA_DEL_UNIVERSO = ("00000", CODIGO_AGREGADO_NACIONAL_TERRIDATA)


def obtener_codigos_universo(db: Session) -> set[str]:
    """Codigos de los municipios REALES del mapa: los de zonas_municipio sin 00000/01001
    (1,104 hoy). Excluye los 18-19 codigos huerfanos de areas no municipalizadas
    (91263, 94883, 97511...), que tienen filas de deficit en mapcalordb pero ningun poligono."""
    filas = db.execute(
        text("SELECT codigo_municipio FROM public.zonas_municipio WHERE codigo_municipio <> ALL(:excluidos)"),
        {"excluidos": list(CODIGOS_FUERA_DEL_UNIVERSO)},
    ).all()
    return {fila[0] for fila in filas}


def calcular_limites_desde_filas(filas, codigos_universo: set[str]) -> dict[str, tuple[float | None, float | None]]:
    """Min/max de cada variable del score SOLO sobre los municipios de `codigos_universo`.
    `filas`: iterable de {"codigodivipola", "namevar", "valuevar"} (cualquier origen: aqui no
    se decide que filas se leen, solo sobre que municipios se calcula la escala)."""
    valores_por_variable: dict[str, list[float]] = {v: [] for v in VARIABLES_SCORE}
    for fila in filas:
        if fila["valuevar"] is None or fila["codigodivipola"] not in codigos_universo:
            continue
        if fila["namevar"] in valores_por_variable:
            valores_por_variable[fila["namevar"]].append(float(fila["valuevar"]))
    return {
        variable: (min(valores), max(valores)) if valores else (None, None)
        for variable, valores in valores_por_variable.items()
    }


def calcular_limites_base(db: Session, anio_base: int) -> dict[str, tuple[float | None, float | None]]:
    """Min/max de cada una de las 3 variables del score, sobre las filas de
    `anio_base` (de cualquier origen) de los MUNICIPIOS del universo -- los de
    zonas_municipio sin 00000/01001; los codigos huerfanos de areas no municipalizadas
    NO fijan la escala (16 de ellos tenian el deficit cuantitativo saturado en 100 y
    mantenian el maximo en 100 aunque ningun municipio llegara a ese valor). Mismo patron
    que el min-max dinamico de routers/zonas.py, pero fijado a un solo
    año en vez de recalcularse por request. Se llama una sola vez (ver
    services/proyeccion_cache.py::obtener_limites_base/guardar_limites_base
    para el cacheo) y el resultado se reutiliza para normalizar TODOS los
    años proyectados, de forma que el score proyectado y el score real
    del año base queden en la MISMA escala."""
    query = text("""
        SELECT codigodivipola, namevar, valuevar
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

    return calcular_limites_desde_filas(filas, obtener_codigos_universo(db))


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


def obtener_deficit_para_anio(db: Session, anio: int, anio_base: int) -> dict[str, dict]:
    """Insumo de deficit_habitacional_cuantitativo del score para el año pedido, por
    municipio, con su procedencia:

      * si existe fila para `anio` (de cualquier origen) se usa ESA fila y es_real =
        es_real(origen) (un deficit con origen proyeccion-* sale como proyeccion);
      * si no existe, se usa el valor congelado del año base (2024) y es_real=False
        SIEMPRE (es un valor repetido, no una observacion de ese año).

    Devuelve {codigo: {"valor", "origen", "es_real", "fiabilidad", "anio_fuente"}}.
    obtener_deficit_base() queda para quien solo necesite el valor congelado."""
    query = text("""
        SELECT codigodivipola, yearm, valuevar, originvar, intervaloconfianza
        FROM public.mapcalordb
        WHERE namevar = :variable
          AND yearm = ANY(:anios)
          AND codigodivipola IS NOT NULL
          AND codigodivipola != :codigo_agregado_nacional
          AND valuevar IS NOT NULL
    """)
    filas = db.execute(
        query,
        {
            "variable": VARIABLE_DEFICIT_COMPUESTO,
            "anios": sorted({anio, anio_base}),
            "codigo_agregado_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
        },
    ).mappings().all()

    del_anio: dict[str, dict] = {}
    del_base: dict[str, dict] = {}
    for fila in filas:
        origen = fila["originvar"]
        intervalo = float(fila["intervaloconfianza"]) if fila["intervaloconfianza"] is not None else None
        registro = {
            "valor": float(fila["valuevar"]),
            "origen": origen,
            "es_real": es_real(origen),
            "fiabilidad": fiabilidad_de_origen(origen, intervalo),
            "anio_fuente": int(fila["yearm"]),
        }
        if int(fila["yearm"]) == anio:
            del_anio[fila["codigodivipola"]] = registro
        if int(fila["yearm"]) == anio_base:
            del_base[fila["codigodivipola"]] = {**registro, "es_real": False}

    resultado = dict(del_base)
    resultado.update(del_anio)
    return resultado
