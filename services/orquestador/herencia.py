"""7.1b / H1 Herencia de entidades entre turnos de UNA conversacion (claude/fase7-1b-adenda-modelo-final.md, seccion 3).

Determinista, sin modelo ni base: trabaja sobre Resolucion (4.1) y sobre el texto de las preguntas del usuario.

heredar(actual, previa, pregunta) -> Resolucion
    Un seguimiento («¿y en 2020?», «¿y en Cali?», «¿y el alcantarillado?», «¿ese año fue real?») hereda de `previa` el
    municipio, la variable o el año que no trae. Reglas:
      - una pregunta AUTOCONTENIDA (trae municipio y variable) no hereda nada, ni siquiera el año;
      - una pregunta que no trae nada y no es referencial (saludo, cambio de tema, concepto sin municipio) no hereda;
      - una pregunta conceptual (intencion_conceptual) no hereda, salvo que sea referencial («¿por qué ese dato es estimado?»);
      - con aclaracion pendiente o resuelta en la propia pregunta no se hereda;
      - una grafica de evolucion no hereda el año (necesita la serie completa, 7.1).
    Lo heredado queda en Resolucion.heredado ("municipios", "variables", "anios").

resolver_cadena(preguntas, catalogo, hoy) -> Resolucion | None
    Resolucion EFECTIVA del ultimo turno de la lista (la `previa` del turno actual): cada pregunta hereda de la anterior,
    y una aclaracion de homonimo se repite como en pipeline.responder (la respuesta corta que la resuelve fija el
    municipio y rompe la herencia). Sin herencia entre conversaciones: el que llama pasa SOLO las preguntas de la
    conversacion y del usuario actuales (historial.leer_intercambios).
"""
import re
from dataclasses import replace
from datetime import date

from services.orquestador import aclaracion as acl
from services.orquestador.grafica_intencion import _PIDE_GRAFICA, intencion_grafica
from services.orquestador.intencion import intencion_conceptual
from services.orquestador.resolutor import Resolucion, normalizar, resolver

# «ese año», «ese municipio», «ese dato», «eso», «lo mismo»: apuntan al turno anterior aunque no nombren nada.
_REFERENCIA = re.compile(
    r"\b(?:ese|esa|dicho|dicha|mismo|misma) (?:ano|municipio|ciudad|dato|indicador|valor|cifra|resultado|variable|score)\b"
    r"|\b(?:eso|lo anterior|lo mismo)\b"
)


def _trae(res: Resolucion) -> bool:
    return bool(res.municipios or res.variables or res.anios)


def _util(previa: Resolucion | None) -> bool:
    return previa is not None and previa.aclaracion is None and _trae(previa)


def heredar(actual: Resolucion, previa: Resolucion | None, pregunta: str) -> Resolucion:
    if actual.aclaracion is not None or not _util(previa):
        return actual
    texto = normalizar(pregunta)
    referencia = bool(_REFERENCIA.search(texto))
    pide_grafica = bool(_PIDE_GRAFICA.search(texto))
    if actual.municipios and actual.variables and not referencia:
        return actual  # autocontenida
    if not (_trae(actual) or referencia or pide_grafica):
        return actual  # cambio de tema, saludo o concepto sin pistas
    if not referencia and intencion_conceptual(pregunta, actual):
        return actual
    heredado: list[str] = []
    resultado = replace(actual)
    if not actual.municipios and previa.municipios:
        resultado.municipios = [dict(m) for m in previa.municipios]
        heredado.append("municipios")
    if not actual.variables and previa.variables:
        resultado.variables = list(previa.variables)
        heredado.append("variables")
    if actual.anio_implicito and previa.anios and not (
        (ig := intencion_grafica(pregunta, resultado)).activa and ig.evolucion
    ):
        resultado.anios, resultado.rango, resultado.anio_implicito = list(previa.anios), previa.rango, False
        heredado.append("anios")
    resultado.heredado = heredado
    return resultado


def resolver_cadena(preguntas: list[str], catalogo, hoy: date) -> Resolucion | None:
    previa: Resolucion | None = None
    pendiente: dict | None = None  # estado de aclaracion como lo guardaria el pipeline (4.13)
    for texto in preguntas:
        fijados: dict = {}
        heredable = pendiente is None
        if pendiente is not None:
            eleccion = acl.interpretar_respuesta(texto, pendiente, catalogo)
            if eleccion.resultado == "elegida":
                fijados = {**pendiente["fijados"], acl.clave_fijado(eleccion.opcion): eleccion.opcion["divipola"]}
                texto = pendiente["pregunta_original"]
                pendiente = None
            elif eleccion.resultado in ("ambigua", "repetir"):
                if eleccion.resultado == "ambigua":
                    pendiente = {**pendiente, "tipo": "homonimo", "opciones": eleccion.opciones}
                previa = None
                continue
            else:
                pendiente = None
        actual = resolver(texto, catalogo, hoy, fijados)
        if actual.aclaracion is not None:
            pendiente = acl.construir_estado(texto, actual.aclaracion, fijados)
            previa = None
            continue
        previa = heredar(actual, previa, texto) if heredable else actual
    return previa
