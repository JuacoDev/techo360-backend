"""Orquestador 4.1 + 4.2 + 4.3: de la pregunta del usuario al cuerpo PeticionV2 (o a una aclaracion fija).

NO esta conectado a ninguna ruta publica ni a RabbitMQ (eso es el paso 4.4). Sin escrituras en la base.

preparar_consulta(...) devuelve
  {"accion": "aclarar", "tipo": ..., "texto": ...}     -> responder con la plantilla fija, SIN llamar al modelo
  {"accion": "consultar", "peticion": ..., "contexto_compacto": ..., "contexto_ids": [...], "notas": {...}}
"""
from datetime import date

from core.config import settings
from services.orquestador import rag
from services.orquestador.contexto import construir_contexto
from services.orquestador.ensamblador import MENSAJE_PREGUNTA_LARGA, ensamblar
from services.orquestador.grafica_intencion import intencion_grafica
from services.orquestador.herencia import heredar
from services.orquestador.intencion import intencion_conceptual
from services.orquestador.resolutor import cargar_catalogo, resolver


async def preparar_consulta(
    pregunta: str, db, metadatos: dict, *, hoy: date | None = None, catalogo=None, fijados=None, rag_fn=None,
    historial=None, previa=None, **inyecciones,
) -> dict:
    """`historial`: intercambios previos de la misma conversacion y usuario (ensamblador.preparar_historial). `previa`:
    resolucion efectiva del turno anterior (herencia.resolver_cadena); con ella un seguimiento hereda municipio,
    variable o año. Sin ninguno de los dos, el comportamiento de siempre."""
    hoy = hoy or date.today()
    catalogo = catalogo or cargar_catalogo(db)
    resolucion = heredar(resolver(pregunta, catalogo, hoy, fijados), previa, pregunta)
    if resolucion.aclaracion is not None:
        return {"accion": "aclarar", **resolucion.aclaracion}
    # 7.1: con intencion de grafica (y un municipio resuelto) el contexto trae la linea de opciones y, si es de evolucion
    # sin años, la serie completa.
    intencion = intencion_grafica(pregunta, resolucion)
    contexto = await construir_contexto(resolucion, db, hoy_anio=hoy.year, intencion_grafica=intencion, **inyecciones)
    # 6.6: RAG curado SOLO con CHATBOT_RAG_ACTIVO y pregunta conceptual (los datos numericos nunca van por RAG).
    # `rag_fn` reemplaza la busqueda (pruebas); no activa el RAG por si solo.
    if settings.chatbot_rag_activo and intencion_conceptual(pregunta, resolucion):
        contexto.fragmentos_rag = await rag.recuperar(pregunta, rag_fn)
    resultado = ensamblar(pregunta, contexto, metadatos, historial=historial)
    if resultado["notas"]["pregunta_no_cabe"]:  # 6.7b: ni sin contexto cabe en num_ctx; mensaje fijo, sin llamar al modelo
        return {"accion": "aclarar", "tipo": "pregunta_larga", "texto": MENSAJE_PREGUNTA_LARGA}
    resultado["notas"]["intencion_grafica"] = intencion.activa
    resultado["notas"]["heredado"] = list(resolucion.heredado)
    return {"accion": "consultar", "resolucion": resolucion.a_dict(), **resultado}
