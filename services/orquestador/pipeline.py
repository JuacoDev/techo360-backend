"""4.4 / 4.5 Pipeline v2: de la pregunta a la respuesta final (texto + metadatos para persistir).

responder(...) encadena: orquestador (4.1-4.3) -> RPC v2 al consumer -> bloque de grafica (4.6) -> validador de
cifras (4.5, una regeneracion como maximo) -> sanitizacion (4.7). Devuelve un dict con "accion":
  "aclarar"   plantilla fija (homonimo, inconsistente, demasiados): NUNCA llega a RabbitMQ
  "responder" texto final + "meta" (campos de la migracion 0004)
  "error"     el consumer respondio con status error: {"tipo", "detail"} (el router lo traduce a 502/503)
Un timeout del RPC se propaga como ChatbotTimeoutError. Todo queda tras CHATBOT_ORQUESTADOR_ACTIVO (el router).
No escribe en la base: la persistencia (4.8) la hace quien llama con meta.

Regeneracion (decision de Juan): cifra no validada -> UNA regeneracion con el mismo contexto (intento + 1) solo
si queda margen en los 200 s del RPC (restante >= 1,3 x la duracion de la primera generacion); si la segunda
tambien falla, o no hay margen, respuesta DETERMINISTA con las lineas del contexto. El texto no validado nunca
se entrega ni se guarda (solo cuantas cifras fallaron).
"""
import asyncio
import logging
import re
import time
from datetime import date

from schemas.chatbot_contrato_v2 import ErrorContrato, validar_respuesta
from services import chatbot_rpc_client
from services import proyeccion_service as pservicio
from services.orquestador import aclaracion as acl
from services.orquestador.contexto import LINEA_SIN_CONTEXTO, leer_serie
from services.orquestador.grafica_bloque import procesar_grafica
from services.orquestador.orquestador import preparar_consulta
from services.orquestador.resolutor import cargar_catalogo
from services.orquestador.sanitizador import sanitizar_texto
from services.orquestador.validador_cifras import validar_cifras

logger = logging.getLogger(__name__)

RPC_TIMEOUT_S = chatbot_rpc_client.RPC_TIMEOUT_SECONDS
FACTOR_MARGEN_REGENERACION = 1.3
NOTA_FALLBACK = "No pude redactar una explicación con cifras verificadas; estos son los datos disponibles:"
TEXTO_SIN_DATOS = "No pude redactar una explicación con cifras verificadas y no hay datos disponibles para esta consulta."


def respuesta_determinista(lineas: list[str]) -> str:
    """Plantilla armada SOLO con las lineas del contexto (sin los ids [cN])."""
    limpias = [l.split("] ", 1)[1] if re.match(r"\[[cd]\d+\] ", l) else l for l in lineas]
    if not limpias:
        return TEXTO_SIN_DATOS
    return NOTA_FALLBACK + "\n" + "\n".join(f"- {l}" for l in limpias)


def _meta(version_prompt, respuesta, regeneraciones, validacion, grafica, contexto_compacto, contexto_ids) -> dict:
    m = respuesta.metadatos
    return {
        "version_contrato": 2,
        "modelo": m.modelo,
        "modelo_digest": m.digest,
        "version_prompt": version_prompt,
        "done_reason": m.done_reason,
        "tokens_prompt": m.prompt_eval_count,
        "tokens_respuesta": m.eval_count,
        "duracion_ms": m.duracion_ms,
        "contexto_ids": contexto_ids,
        "contexto": contexto_compacto,
        "validacion_cifras": validacion,
        "regeneraciones": regeneraciones,
        "grafica": grafica,
    }


async def _generar(peticion: dict, solicitar, timeout: float):
    """-> (RespuestaOkV2, None) | (None, resultado de error)."""
    crudo = await solicitar(peticion, timeout=timeout)
    try:
        respuesta = validar_respuesta(crudo)
    except ErrorContrato as error:
        return None, {"accion": "error", "tipo": "error_interno", "detail": f"Respuesta del asistente fuera de contrato: {error.detail}"[:300]}
    if respuesta.status != "ok":
        return None, {"accion": "error", "tipo": respuesta.tipo, "detail": respuesta.detail}
    return respuesta, None


async def responder(pregunta: str, db_datos, metadatos: dict, *, catalogo=None, almacen=None, hoy: date | None = None, **kw) -> dict:
    """Entrada del pipeline v2. Antes del orquestador aplica la memoria de aclaraciones (4.13): si la conversacion
    esta esperando la respuesta a «¿A cuál te refieres?», una respuesta corta e inequivoca fija el municipio y la
    pregunta original sigue su flujo; si no, se descarta el estado y la pregunta se procesa normal. El estado vive
    en Redis por usuario y conversacion (aclaracion.py); si Redis falla todo sigue como antes de la 4.13."""
    usuario_id, conversacion_id = metadatos.get("usuario_id"), metadatos.get("conversacion_id")
    almacen = almacen or acl.almacen_por_defecto()
    catalogo = catalogo or cargar_catalogo(db_datos)
    estado = await asyncio.to_thread(almacen.leer, usuario_id, conversacion_id)
    fijados: dict = {}
    consumido = None
    if estado is not None:
        eleccion = acl.interpretar_respuesta(pregunta, estado, catalogo)
        if eleccion.resultado == "elegida":
            consumido = estado
            await asyncio.to_thread(almacen.borrar, usuario_id, conversacion_id)
            fijados = {**estado["fijados"], acl.clave_fijado(eleccion.opcion): eleccion.opcion["divipola"]}
            pregunta = estado["pregunta_original"]
        elif eleccion.resultado in ("ambigua", "repetir"):
            opciones = eleccion.opciones if eleccion.resultado == "ambigua" else estado["opciones"]
            if eleccion.resultado == "ambigua":  # acota la aclaracion a esas opciones y renueva el plazo
                nuevo = {**estado, "tipo": "homonimo", "opciones": opciones, "creado_en": int(time.time())}
                await asyncio.to_thread(almacen.guardar, usuario_id, conversacion_id, nuevo)
            return _aclaracion(acl.texto_homonimo(opciones), "homonimo")
        else:  # pregunta nueva: el estado se descarta
            await asyncio.to_thread(almacen.borrar, usuario_id, conversacion_id)
    try:
        resultado = await _responder(pregunta, db_datos, metadatos, hoy=hoy, catalogo=catalogo, fijados=fijados, **kw)
    except BaseException:
        if consumido is not None:  # el RPC fallo o se cancelo: el reintento debe poder resolver la misma respuesta
            await asyncio.shield(asyncio.to_thread(almacen.guardar, usuario_id, conversacion_id, consumido))
        raise
    opciones = resultado.pop("opciones", None)
    if resultado["accion"] == "aclarar" and opciones:
        nuevo = acl.construir_estado(pregunta, {"tipo": resultado["tipo"], "opciones": opciones}, fijados)
        if nuevo is not None:
            await asyncio.to_thread(almacen.guardar, usuario_id, conversacion_id, nuevo)
    elif resultado["accion"] == "error" and consumido is not None:
        await asyncio.to_thread(almacen.guardar, usuario_id, conversacion_id, consumido)
    return resultado


def _aclaracion(texto: str, tipo: str, opciones: list | None = None) -> dict:
    r = {
        "accion": "aclarar", "tipo": tipo, "texto": texto,
        "meta": {"version_contrato": 2, "regeneraciones": 0, "validacion_cifras": {"estado": "abstencion", "no_validadas": []}},
    }
    if opciones:
        r["opciones"] = opciones
    return r


async def _responder(
    pregunta: str,
    db_datos,
    metadatos: dict,
    *,
    hoy: date | None = None,
    catalogo=None,
    fijados: dict | None = None,
    solicitar=None,
    reloj=time.monotonic,
    rpc_timeout: float = RPC_TIMEOUT_S,
    leer_serie_fn=None,
    completar=None,
    score_fn=None,
    **inyecciones,
) -> dict:
    solicitar = solicitar or chatbot_rpc_client.solicitar_chatbot
    leer_serie_fn = leer_serie_fn or (lambda codigo, variable: asyncio.to_thread(leer_serie, db_datos, codigo, variable))
    completar = completar or pservicio.completar_puntos
    if score_fn is None:
        async def score_fn(codigo, anio):
            return await pservicio.calcular_score_punto(codigo, anio, db_datos)
    inicio = reloj()
    catalogo = catalogo or cargar_catalogo(db_datos)
    preparada = await preparar_consulta(
        pregunta, db_datos, metadatos, hoy=hoy, catalogo=catalogo,
        leer_serie_fn=leer_serie_fn, completar=completar, score_fn=score_fn, fijados=fijados, **inyecciones,
    )
    if preparada["accion"] == "aclarar":
        return _aclaracion(preparada["texto"], preparada["tipo"], preparada.get("opciones"))
    peticion = preparada["peticion"]
    contexto_compacto, contexto_ids = preparada["contexto_compacto"], preparada["contexto_ids"]
    items = peticion["contexto"]
    bloque_contexto = peticion["mensajes"][1]["contenido"].split("\n\nPREGUNTA DEL USUARIO\n")[0]
    lineas = [l for l in bloque_contexto.split("\n")[1:] if l and l != LINEA_SIN_CONTEXTO]
    codigos = set(catalogo.por_codigo)
    version_prompt = peticion["metadatos"]["version_prompt"]

    async def revisar(texto_modelo: str):
        sin_bloque, grafica = await procesar_grafica(
            texto_modelo, codigos, leer_serie_fn=leer_serie_fn, completar=completar, score_fn=score_fn
        )
        return sin_bloque, grafica, validar_cifras(sin_bloque, lineas, items, pregunta)

    t1 = reloj()
    primera, error = await _generar(peticion, solicitar, rpc_timeout)
    if error:
        return error
    duracion_primera = reloj() - t1
    texto, grafica, validacion = await revisar(primera.response)
    final, regeneraciones = primera, 0

    if validacion["estado"] == "no_validada":
        no_validadas = validacion["no_validadas"]
        restante = rpc_timeout - (reloj() - inicio)
        ok = False
        if restante >= FACTOR_MARGEN_REGENERACION * duracion_primera:
            intento = (peticion["metadatos"].get("intento") or 1) + 1
            segunda_peticion = {**peticion, "metadatos": {**peticion["metadatos"], "intento": intento}}
            regeneraciones = 1
            try:
                segunda, error = await _generar(segunda_peticion, solicitar, max(1.0, restante))
            except Exception as exc:  # noqa: BLE001 -- timeout o fallo de la red en la 2.a generacion
                logger.warning("La regeneracion fallo (%s): se entrega la respuesta determinista.", exc)
                segunda, error = None, True
            if segunda is not None:
                # El error de la PRIMERA generacion sigue siendo error; el de la segunda no: ya hay un texto
                # determinista valido que entregar (auditoria 4.10).
                final = segunda
                texto2, grafica2, validacion2 = await revisar(segunda.response)
                if validacion2["estado"] != "no_validada":
                    ok = True
                    texto, grafica = texto2, grafica2
                    validacion = {"estado": "regenerada", "no_validadas": []}
                else:
                    no_validadas = validacion2["no_validadas"]
        if not ok:
            texto, grafica = respuesta_determinista(lineas), None
            validacion = {"estado": "abstencion", "no_validadas": no_validadas}

    texto = sanitizar_texto(texto) or respuesta_determinista(lineas)
    return {
        "accion": "responder", "texto": texto, "notas": preparada["notas"],
        "meta": _meta(version_prompt, final, regeneraciones, validacion, grafica, contexto_compacto, contexto_ids),
    }
