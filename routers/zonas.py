import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_db
from models.mapcalor_user import MapCalorUser
from routers.auth import get_current_user
from schemas.zonas import GeoJSONFeatureCollection
from services.score_privacion import calcular_score_privacion, normalizar_minmax


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

# "Area En Litigio Cauca - Huila": existe como poligono en zonas_municipio
# (codigo '00000') y en zonas_departamento (codigo '00'), pero no es un
# municipio ni un departamento reales -- DANE no le asigna codigo propio
# y mapcalordb no tiene ninguna fila para ella (verificado en produccion,
# 4-oct-2026). Se excluye de la respuesta para que no aparezca en el mapa
# ni en los selectores. Es un filtro de lectura: NO se borra ninguna fila
# de zonas_municipio / zonas_departamento (el ETL de zonas la repone).
CODIGOS_MUNICIPIO_EXCLUIDOS = ["00000"]
CODIGOS_DEPARTAMENTO_EXCLUIDOS = ["00"]

# Las 3 variables que entran al score compuesto de privacion (expuesto
# como score_privacion -- "Score_privación" en el documento de
# metodologia, sin tilde/mayuscula aqui por consistencia con el resto
# de nombres de campo de la API). Alkire-Foster / IPM adaptado, sin el
# umbral k=33% oficial -- aqui es un promedio simple de 3 componentes
# con peso igual 1/3 cada uno.
# Acueducto y alcantarillado se INVIERTEN (1 - normalizado) porque mas
# cobertura = menos privacion; el deficit ya viene en la direccion
# correcta (mas deficit = mas privacion), no se invierte.
# densidad_poblacional queda fuera a proposito -- es ponderador visual
# de tamaño/opacidad en el frontend, no se mezcla en el color del mapa.
#
# >>> AVISO: por que el componente de deficit es el CUANTITATIVO y no el
# >>> TOTAL (corregido 23-sep-2026) -- doble conteo confirmado <<<
# Hasta este cambio, el tercer componente era deficit_habitacional_total.
# Eso contaba acueducto y alcantarillado DOS VECES: una como variables
# independientes (los dos primeros terminos) y otra dentro del propio
# deficit. Confirmado en la fuente oficial, no asumido -- se descargo el
# anexo real del DANE que ya ingiere ingestion/dane_deficit_adapter.py
# (deficit-hab-2020-anexo-nueva-metodologia.xlsx, hoja "Total
# Municipios") y se leyeron las columnas de componentes que ese adapter
# NO usa (11-28):
#   - Deficit CUANTITATIVO (cols 11-14): Tipo de vivienda, Material de
#     Paredes, Cohabitacion, Hacinamiento no mitigable. NINGUN
#     componente de servicios publicos.
#   - Deficit CUALITATIVO (cols 15-28): incluye explicitamente "Agua
#     para cocinar" (col 21) y "Alcantarillado" (col 23), ademas de
#     Cocina, Energia y Recoleccion de basuras.
# Como deficit_habitacional_total = cuantitativo + cualitativo, el total
# arrastra ese solapamiento; el cuantitativo no.
#
# MAGNITUD del solapamiento: el cociente (Agua para cocinar jerarquizado
# + Alcantarillado jerarquizado) / hogares en deficit cualitativo tiene
# mediana 0.677 sobre 1.121 municipios del anexo, y supera 0.5 en el
# 68,8% de ellos. Los componentes NO son mutuamente excluyentes entre si
# (un hogar puede tener varias carencias a la vez), asi que ese cociente
# mide PESO, no una descomposicion exacta -- puede pasar de 1.
#
# CONFIRMACION EMPIRICA INDEPENDIENTE (mapcalordb, yearm=2024), que
# coincide con lo que predice la estructura de componentes del anexo:
#   corr(deficit_cualitativo,  cobertura_alcantarillado) = -0.358
#   corr(deficit_total,        cobertura_alcantarillado) = -0.307
#   corr(deficit_CUANTITATIVO, cobertura_alcantarillado) = -0.022
#   corr(deficit_CUANTITATIVO, cobertura_acueducto)      = -0.073
# El cuantitativo es practicamente ortogonal a las coberturas -- justo
# lo que se espera de una variable que no las contiene.
#
# POR QUE ESTA OPCION Y NO LA CONTRARIA (quitar las coberturas y dejar
# el deficit total): (1) mantiene la estructura Alkire-Foster de 3
# componentes con peso 1/3, sin rediseñar el score; (2) las coberturas
# son las UNICAS entradas del score con dato real observado (TerriData),
# mientras que el deficit de 2024 es una proyeccion con fiabilidad 70%
# (ver ingestion/proyeccion_2024.py) -- quitarlas dejaria el compuesto
# reducido a una sola variable proyectada.
#
# IMPACTO MEDIDO antes de aplicar, sobre los 1.052 municipios con los 3
# insumos completos (no una muestra chica): delta del score media
# -0.1178, mediana -0.1180, rango -0.2943 a +0.0502; Pearson
# actual-vs-nuevo 0.9392, Spearman (ranking) 0.9478; cambio de puesto en
# el ranking mediana 62, p90 164, maximo 320, con 304 municipios
# moviendose mas de 100 puestos. Es decir: el orden general se conserva
# pero la reordenacion NO es trivial, y el score baja ~0.12 en promedio
# para todos (el cuantitativo es de menor magnitud que el total) -- es
# un cambio visible para el usuario, no cosmetico.
#
# LIMITE HONESTO de esta correccion: usar solo el cuantitativo descarta
# la parte del deficit cualitativo que NO son servicios (hacinamiento
# mitigable, pisos, cocina). La opcion mas fiel seria reconstruir un
# "cualitativo sin servicios" a partir de las columnas de componentes
# del anexo (que ya existen en la fuente, solo falta ingerirlas) y usar
# cuantitativo + ese residuo. No se hizo aqui porque requiere extender
# dane_deficit_adapter.py con variables nuevas; queda como mejora
# pendiente, no como algo ya resuelto.
#
# IMPORTANTE: esto cambia solo QUE ENTRA AL SCORE COMPUESTO.
# deficit_habitacional_total sigue siendo una de las 7 variables que se
# exponen en properties.indicadores y se muestran en el panel -- no se
# oculta ni se deja de calcular.
# normalizar_minmax()/calcular_score_privacion() se movieron a
# services/score_privacion.py (modulo compartido con routers/proyecciones.py
# para el score de privacion por proyeccion) -- mismo codigo, mismo
# comportamiento, solo cambio de ubicacion e import.


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
            WHERE codigo_departamento != ALL(:excluidos)
            ORDER BY nombre
        """)
        parametros_zonas = {"excluidos": CODIGOS_DEPARTAMENTO_EXCLUIDOS}

    else:
        query = text("""
            SELECT
                codigo_municipio AS codigo,
                codigo_departamento,
                nombre,
                ST_AsGeoJSON(geom) AS geometry
            FROM zonas_municipio
            WHERE codigo_municipio != ALL(:excluidos)
            ORDER BY nombre
        """)
        parametros_zonas = {"excluidos": CODIGOS_MUNICIPIO_EXCLUIDOS}

    try:
        resultados = db.execute(query, parametros_zonas).mappings().all()

        # --- Indicadores por municipio, UNA sola query para las 7
        # variables del año pedido (evita N+1 contra ~1,105 zonas x 7
        # variables) -- se arma un dict en memoria, indexado por
        # (codigodivipola, namevar), y se cruza abajo por Feature. ---
        indicadores_por_codigo: dict[str, dict[str, dict]] = {}
        # Query por-municipio (variables crudas para las 7 variables, un
        # año) -- usada por la rama municipio directamente, y por la
        # rama departamento para calcular el score_privacion promedio
        # (ver AVISO mas abajo) sin duplicar el texto SQL entre ambas.
        query_indicadores = text("""
            SELECT codigodivipola, namevar, valuevar, originvar, intervaloconfianza
            FROM public.mapcalordb
            WHERE yearm = :anio
              AND namevar = ANY(:variables)
              AND codigodivipola IS NOT NULL
              AND codigodivipola != :codigo_agregado_nacional
        """)
        if nivel == "municipio":
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
                    "score_normalizado": normalizar_minmax(valor, minimo, maximo),
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
                  AND zm.codigo_municipio != ALL(:municipios_excluidos)
                ORDER BY zm.codigo_departamento, m.namevar, m.yearm DESC
            """)
            filas_indicadores_depto = db.execute(
                query_indicadores_depto,
                {
                    "variables": VARIABLES_INDICADORES,
                    "municipios_excluidos": CODIGOS_MUNICIPIO_EXCLUIDOS,
                },
            ).mappings().all()

            for fila in filas_indicadores_depto:
                codigo = fila["codigo_departamento"]
                indicadores_por_codigo.setdefault(codigo, {})[fila["namevar"]] = {
                    "valor": float(fila["valuevar"]) if fila["valuevar"] is not None else None,
                    "origen": fila["originvar"],
                    "intervalo_confianza": None,
                    "anio_real": fila["yearm"]
                }

            # >>> AVISO -- score_privacion a nivel departamento (T-?, 26-sep-2026) <<<
            # Se reusa LITERALMENTE el mismo score_privacion ya calculado
            # por municipio (mismos insumos min-max que la rama
            # nivel=municipio, misma calcular_score_privacion()) -- no se
            # recalcula un min-max nuevo a nivel departamento, para que
            # sea "el mismo score", como se pidio explicitamente. Esto es
            # una consulta SEPARADA de la de arriba (que solo trae UN
            # valor arbitrario -- el mas reciente -- por variable y
            # departamento, sin agregar entre municipios): aqui se
            # necesita el dato de CADA municipio para promediarlo.
            filas_indicadores_score = db.execute(
                query_indicadores,
                {
                    "anio": anio,
                    "variables": VARIABLES_INDICADORES,
                    "codigo_agregado_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
                },
            ).mappings().all()

            valores_por_variable_score: dict[str, list[float]] = {v: [] for v in VARIABLES_INDICADORES}
            for fila in filas_indicadores_score:
                if fila["valuevar"] is not None:
                    valores_por_variable_score[fila["namevar"]].append(float(fila["valuevar"]))
            minmax_por_variable_score: dict[str, tuple[float | None, float | None]] = {
                variable: (min(valores), max(valores)) if valores else (None, None)
                for variable, valores in valores_por_variable_score.items()
            }

            indicadores_por_municipio_score: dict[str, dict[str, dict]] = {}
            for fila in filas_indicadores_score:
                codigo_mun = fila["codigodivipola"]
                variable = fila["namevar"]
                valor = float(fila["valuevar"]) if fila["valuevar"] is not None else None
                minimo, maximo = minmax_por_variable_score.get(variable, (None, None))
                indicadores_por_municipio_score.setdefault(codigo_mun, {})[variable] = {
                    "score_normalizado": normalizar_minmax(valor, minimo, maximo),
                }

            # >>> AVISO -- ponderacion SIMPLE, no por poblacion (decision
            # explicita del usuario, 26-sep-2026) <<<
            # El pedido original queria promedio ponderado por poblacion,
            # con la clausula "si no hay variable de poblacion
            # disponible, usar promedio simple y dejarlo documentado
            # como tal". No hay una variable de poblacion TOTAL entre los
            # 7 indicadores expuestos -- solo densidad_poblacional
            # (hab/km2, no poblacion cruda). Derivarla exigiria una
            # consulta PostGIS nueva (densidad x area, ST_Area sobre
            # zonas_municipio.geom) que el usuario decidio NO agregar
            # (mantener el cambio acotado a lo pedido) -- se usa promedio
            # SIMPLE entre los municipios del departamento con score
            # valido.
            scores_por_departamento: dict[str, list[float]] = {}
            for codigo_mun, indicadores_mun in indicadores_por_municipio_score.items():
                score_mun = calcular_score_privacion(indicadores_mun)
                if score_mun is None:
                    continue
                codigo_depto = str(codigo_mun).strip().zfill(5)[:2]
                scores_por_departamento.setdefault(codigo_depto, []).append(score_mun)

            # None (no 0) para un departamento sin NINGUN municipio con
            # score valido -- el frontend debe tratarlo como "sin dato"
            # real, no como "privacion cero" (decision explicita del
            # pedido).
            score_privacion_por_departamento: dict[str, float | None] = {
                depto: round(sum(scores) / len(scores), 4) for depto, scores in scores_por_departamento.items()
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
                # de las 3 variables que entran al compuesto.
                properties["score_privacion"] = calcular_score_privacion(properties["indicadores"])
            else:
                # Promedio simple de los score_privacion municipales del
                # departamento (ver AVISO arriba) -- None (no 0) si
                # NINGUN municipio del departamento tiene score valido,
                # para que el frontend lo trate como "sin dato" real.
                properties["score_privacion"] = score_privacion_por_departamento.get(zona["codigo"])

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