"""4.2 Constructor de contexto (fase4-especificacion-1). Solo lectura de mapcalordb por DIVIPOLA y año.

Por cada (municipio, variable, año): si hay fila -> elemento ("dato" o "proyeccion" segun su origen);
si no -> completar_puntos (3.2) -> "proyeccion", o si es sin_dato, una linea explicita
"Sin dato para X, Y, año: motivo" (asi se entrena la abstencion). Ningun dato de usuario entra aqui.

Limites: maximo 20 elementos y ~900 tokens (3,0 caracteres por token, conservador). Prioridad al
recortar: lo pedido explicitamente; despues los años vecinos (hoy no se generan: el campo existe para
cuando se agreguen); las lineas "Sin dato" de lo pedido NUNCA se descartan; si se omite algo, una linea
final "Se omitieron N datos por el límite de contexto.".

Linea de ubicacion: si el resolutor devolvio municipios sin ambiguedad y NINGUNA variable, se antepone por municipio
"Ubicación: <Municipio> pertenece al departamento de <Departamento>." (sin [cN], sin id, sin numeros). Es
obligatoria como las lineas "Sin dato": nunca se recorta y cuenta para el presupuesto.
"""
import asyncio
import json
import math
from dataclasses import dataclass, field

from sqlalchemy import text

from services import proyeccion_service as pservicio
from services.origen import es_proyeccion, fiabilidad_de_origen
from services.orquestador.alias import (
    NOMBRE_VARIABLE,
    V_ACUEDUCTO,
    V_ALCANTARILLADO,
    V_DEFICIT_CUALITATIVO,
    V_DEFICIT_CUANTITATIVO,
    V_DENSIDAD,
    V_SCORE,
)
from services.orquestador.fuentes import fuente_de_origen
from services.score_privacion import normalizar_minmax, calcular_score_privacion

MAX_ELEMENTOS = 20
MAX_TOKENS_CONTEXTO = 900
CARACTERES_POR_TOKEN = 3.0
LINEA_SIN_CONTEXTO = "Sin datos disponibles para esta consulta."
MOTIVOS = {
    "sin_historia": "no hay datos anteriores suficientes para estimarlo",
    "sin_extremo": "falta un valor de referencia para estimarlo",
    "fuera_de_rango": "el año está fuera del rango de la variable",
    "fuera_de_universo": "el municipio no está en el universo de consulta",
    "valor_sospechoso": "el valor estimado no es confiable",
    "componente_sin_dato": "falta alguno de los componentes del score",
    "score_no_calculable": "no se pudo calcular el score",
    "sin_hueco": "no se pudo calcular el score",
    "sin_dato_real": "no hay ningún dato real",
}
UNIDAD = {
    V_ACUEDUCTO: "porcentaje", V_ALCANTARILLADO: "porcentaje",
    V_DEFICIT_CUANTITATIVO: "porcentaje", V_DEFICIT_CUALITATIVO: "porcentaje",
    V_DENSIDAD: "hab/km²", V_SCORE: "indice",
}


def estimar_tokens(texto: str) -> int:
    return math.ceil(len(texto) / CARACTERES_POR_TOKEN) + 1  # +1: salto de linea


def formato_es(valor: float, decimales: int) -> str:
    """Coma decimal y punto de miles (94,2 ; 4.012)."""
    s = f"{valor:,.{decimales}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def formato_valor(variable: str, valor: float) -> str:
    unidad = UNIDAD[variable]
    if unidad == "porcentaje":
        return f"{formato_es(valor, 1)} %"
    if unidad == "hab/km²":
        return f"{formato_es(valor, 0 if abs(valor) >= 100 else 1)} hab/km²"
    return formato_es(valor, 4)


@dataclass
class Candidato:
    prioridad: int  # 0 pedido explicitamente, 1 año vecino
    orden: int
    item: dict | None  # elemento del contrato 2.2 (sin id), None en las lineas "Sin dato"
    texto: str  # linea sin el "[cN] " inicial
    sin_dato: bool = False


@dataclass
class ContextoConstruido:
    candidatos: list[Candidato] = field(default_factory=list)
    # fragmentos del RAG curado (6.6), por relevancia: SOLO preguntas conceptuales; van tras los datos estructurados
    fragmentos_rag: list[dict] = field(default_factory=list)
    rag_usados: list[str] = field(default_factory=list)  # ids de los fragmentos que entraron en la ultima seleccion

    def seleccionar(self, max_elementos: int = MAX_ELEMENTOS, max_tokens: int = MAX_TOKENS_CONTEXTO):
        """-> (items con id c1..cn, lineas de texto, omitidos). Las lineas Sin dato siempre entran."""
        obligatorias = [c for c in self.candidatos if c.sin_dato]
        opcionales = sorted((c for c in self.candidatos if not c.sin_dato), key=lambda c: (c.prioridad, c.orden))
        presupuesto = max_tokens - sum(estimar_tokens(c.texto) for c in obligatorias)
        incluidos: list[Candidato] = []
        for c in opcionales:
            costo = estimar_tokens("[c00] " + c.texto)
            if len(incluidos) >= max_elementos or costo > presupuesto:
                break  # prefijo por prioridad: no se salta un elemento para colar uno menos prioritario
            incluidos.append(c)
            presupuesto -= costo
        omitidos = len(opcionales) - len(incluidos)
        if omitidos:  # reserva para la linea final; si no cabe, se saca el ultimo incluido
            nota = nota_omision(omitidos)
            while incluidos and estimar_tokens(nota) > presupuesto:
                presupuesto += estimar_tokens("[c00] " + incluidos.pop().texto)
                omitidos += 1
                nota = nota_omision(omitidos)
        elegidos = sorted(incluidos + obligatorias, key=lambda c: c.orden)
        items, lineas = [], []
        n = 0
        for c in elegidos:
            if c.sin_dato:
                lineas.append(c.texto)
                continue
            n += 1
            item = {"id": f"c{n}", **c.item}
            items.append(item)
            lineas.append(f"[c{n}] {c.texto}")
        self.rag_usados = []
        if self.fragmentos_rag:  # prioridad: el dato estructurado primero; el RAG usa lo que quede del presupuesto
            from services.orquestador.rag import seleccionar_lineas  # import tardio: rag usa estimar_tokens de aqui
            gastado = sum(estimar_tokens(l) for l in lineas) + (estimar_tokens(nota_omision(omitidos)) if omitidos else 0)
            lineas_rag = seleccionar_lineas(self.fragmentos_rag, max_tokens - gastado)
            self.rag_usados = [f["fragmento_id"] for f in self.fragmentos_rag[:len(lineas_rag)]]
            lineas.extend(lineas_rag)
        if omitidos:
            lineas.append(nota_omision(omitidos))
        return items, lineas, omitidos


def nota_omision(n: int) -> str:
    return f"Se omitieron {n} datos por el límite de contexto."


# ------------------------------------------------------------------------- acceso a datos
def leer_serie(db, codigo: str, variable: str) -> list[dict]:
    """Todas las filas del municipio para la variable (consulta parametrizada, solo lectura)."""
    filas = db.execute(
        text("""
            SELECT yearm, valuevar, originvar, intervaloconfianza
            FROM public.mapcalordb
            WHERE codigodivipola = :codigo AND namevar = :variable
            ORDER BY yearm
        """),
        {"codigo": codigo, "variable": variable},
    ).mappings().all()
    return [{"anio": int(f["yearm"]), "valor": None if f["valuevar"] is None else float(f["valuevar"]),
             "origen": f["originvar"], "intervalo": f["intervaloconfianza"]} for f in filas]


def _item(variable, municipio, anio, valor, origen, es_real, fiabilidad):
    return {
        "tipo": "dato" if es_real else "proyeccion",
        "variable": variable,
        "codigodivipola": municipio["divipola"],
        "anio": int(anio),
        "valor": round(float(valor), 4),
        "unidad": UNIDAD[variable],
        "origen": origen,
        "es_real": es_real,
        "fiabilidad": None if es_real else int(round(fiabilidad if fiabilidad is not None else 30)),
        "fuente": fuente_de_origen(origen),
    }


def linea_dato(variable, municipio, item, implicito: bool) -> str:
    # «último año con dato real» solo para un valor real; un valor proyectado con año implícito (Score) es un año de referencia
    if implicito:
        anio = f"{item['anio']} (último año con dato real)" if item["es_real"] else f"{item['anio']} (año de referencia)"
    else:
        anio = str(item["anio"])
    cierre = "Dato real." if item["es_real"] else f"Proyección, fiabilidad {item['fiabilidad']} %."
    return (f"{NOMBRE_VARIABLE[variable]}, {municipio['nombre']} ({municipio['departamento']}), {anio}: "
            f"{formato_valor(variable, item['valor'])}. Fuente: {item['fuente']}. {cierre}")


def linea_ubicacion(municipio) -> str:
    texto = f"Ubicación: {municipio['nombre']} pertenece al departamento de {municipio['departamento']}"
    return texto if texto.endswith(".") else texto + "."  # «Bogotá, D.C.» ya termina en punto


def linea_sin_dato(variable, municipio, anio, motivo: str) -> str:
    detalle = MOTIVOS.get(motivo, motivo)
    return (f"Sin dato para {NOMBRE_VARIABLE[variable]}, {municipio['nombre']} ({municipio['departamento']}), "
            f"{anio if anio else 'sin año con dato real'}: {detalle}.")


def _item_desde_fila(variable, municipio, fila):
    real = not es_proyeccion(fila["origen"])
    return _item(variable, municipio, fila["anio"], fila["valor"], fila["origen"], real,
                 None if real else fiabilidad_de_origen(fila["origen"], fila.get("intervalo")))


def _item_desde_punto(variable, municipio, punto):
    return _item(variable, municipio, punto["anio"], punto["valor"], punto["origen"], False, punto["fiabilidad"])


async def _score(municipio, anio, db, leer_serie_fn, score_fn):
    """Score de un municipio-año: si algun componente falta, calcular_score_punto (3.2); si los tres
    existen, se calcula con las filas y los limites fijos de 2024 (misma escala que el score proyectado)."""
    codigo = municipio["divipola"]
    resultado = await score_fn(codigo, anio)
    if resultado["estado"] == "calculado":
        return "ok", _item(V_SCORE, municipio, anio, resultado["valor"], resultado["origen"], False, resultado["fiabilidad"])
    if resultado["motivo"] != "sin_hueco":
        return "sin_dato", resultado["motivo"]
    return "sin_hueco", None


async def construir_contexto(
    resolucion,
    db,
    *,
    hoy_anio: int,
    leer_serie_fn=None,
    completar=None,
    score_fn=None,
    score_con_filas=None,
) -> ContextoConstruido:
    leer_serie_fn = leer_serie_fn or (lambda codigo, variable: asyncio.to_thread(leer_serie, db, codigo, variable))
    completar = completar or pservicio.completar_puntos
    if score_fn is None:
        async def score_fn(codigo, anio):
            return await pservicio.calcular_score_punto(codigo, anio, db)
    if score_con_filas is None:
        async def score_con_filas(municipio, anio):
            """Score cuando los tres componentes ya existen en BD: limites fijos de 2024, igual que el proyectado."""
            filas = {}
            for v in (V_ACUEDUCTO, V_ALCANTARILLADO, V_DEFICIT_CUANTITATIVO):
                serie = await leer_serie_fn(municipio["divipola"], v)
                fila = next((f for f in serie if f["anio"] == anio and f["valor"] is not None), None)
                if fila is None:
                    return None
                filas[v] = fila
            limites = await pservicio._obtener_limites_base_cacheados(db)
            normalizados = {v: {"score_normalizado": normalizar_minmax(f["valor"], *limites.get(v, (None, None)))}
                            for v, f in filas.items()}
            score = calcular_score_privacion(normalizados)
            if score is None:
                return None
            real = all(not es_proyeccion(f["origen"]) for f in filas.values())
            fiab = min(100.0 if not es_proyeccion(f["origen"]) else fiabilidad_de_origen(f["origen"], f.get("intervalo"))
                       for f in filas.values())
            origen = pservicio.ORIGEN_SCORE_REAL if real else pservicio.ORIGEN_SCORE_PROYECTADO
            return _item(V_SCORE, municipio, anio, score, origen, real, None if real else fiab)

    contexto = ContextoConstruido()
    orden = 0
    implicito = bool(resolucion.anio_implicito)
    pendientes: list[tuple[int, dict, str, int]] = []  # (orden, municipio, variable, año) a completar

    if resolucion.municipios and not resolucion.variables:
        for municipio in resolucion.municipios:
            orden += 1
            contexto.candidatos.append(Candidato(0, orden, None, linea_ubicacion(municipio), True))

    for municipio in resolucion.municipios:
        for variable in resolucion.variables:
            if variable == V_SCORE:
                anios = list(resolucion.anios) if not implicito else [pservicio.ANIO_BASE_SCORE]
                for anio in anios:
                    orden += 1
                    estado, dato = await _score(municipio, anio, db, leer_serie_fn, score_fn)
                    if estado == "ok":
                        contexto.candidatos.append(Candidato(0, orden, dato, linea_dato(variable, municipio, dato, implicito)))
                    elif estado == "sin_dato":
                        contexto.candidatos.append(Candidato(0, orden, None, linea_sin_dato(variable, municipio, anio, dato), True))
                    else:  # los tres componentes existen: score con filas reales/proyectadas
                        item = await score_con_filas(municipio, anio)
                        if item is None:
                            contexto.candidatos.append(Candidato(0, orden, None, linea_sin_dato(variable, municipio, anio, "score_no_calculable"), True))
                        else:
                            contexto.candidatos.append(Candidato(0, orden, item, linea_dato(variable, municipio, item, implicito)))
                continue
            serie = await leer_serie_fn(municipio["divipola"], variable)
            por_anio = {f["anio"]: f for f in serie if f["valor"] is not None}
            if implicito:
                reales = [a for a, f in por_anio.items() if not es_proyeccion(f["origen"])]
                if not reales:
                    orden += 1
                    contexto.candidatos.append(Candidato(0, orden, None, linea_sin_dato(variable, municipio, None, "sin_dato_real"), True))
                    continue
                anios = [max(reales)]
            else:
                anios = list(resolucion.anios)
            for anio in anios:
                orden += 1
                if anio in por_anio:
                    item = _item_desde_fila(variable, municipio, por_anio[anio])
                    contexto.candidatos.append(Candidato(0, orden, item, linea_dato(variable, municipio, item, implicito)))
                else:
                    pendientes.append((orden, municipio, variable, anio))

    for i in range(0, len(pendientes), pservicio.MAX_LOTE_PUNTOS):
        lote = pendientes[i:i + pservicio.MAX_LOTE_PUNTOS]
        puntos = await completar([{"indicador": v, "codigo": m["divipola"], "anio": a} for _, m, v, a in lote])
        for (ord_, municipio, variable, anio), p in zip(lote, puntos):
            if p["estado"] == "calculado":
                item = _item_desde_punto(variable, municipio, p)
                contexto.candidatos.append(Candidato(0, ord_, item, linea_dato(variable, municipio, item, implicito)))
            else:
                contexto.candidatos.append(Candidato(0, ord_, None, linea_sin_dato(variable, municipio, anio, p["motivo"]), True))
    contexto.candidatos.sort(key=lambda c: c.orden)
    return contexto


# ------------------------------------------------------------------ forma compacta (migracion 0004)
_CLAVES = {"id": "i", "variable": "v", "codigodivipola": "d", "anio": "a", "valor": "x", "origen": "o", "fiabilidad": "f"}


def compactar_contexto(items: list[dict]) -> str:
    """JSON compacto para persistir (fase2-2 §7): sin espacios, sin nulos ni derivables (tipo, unidad,
    es_real), claves cortas y la fuente una sola vez en un mapa, referenciada por indice."""
    fuentes: list[str] = []
    elementos = []
    for it in items:
        e = {_CLAVES[k]: it[k] for k in ("id", "variable", "codigodivipola", "anio", "valor", "origen") if it.get(k) is not None}
        if it.get("fiabilidad") is not None:
            e["f"] = it["fiabilidad"]
        if it["fuente"] not in fuentes:
            fuentes.append(it["fuente"])
        e["s"] = fuentes.index(it["fuente"])
        elementos.append(e)
    return json.dumps({"s": fuentes, "e": elementos}, separators=(",", ":"), ensure_ascii=False)
