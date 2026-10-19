"""Orquestador 4.1 + 4.2 + 4.3: de la pregunta del usuario al cuerpo PeticionV2 (o a una aclaracion fija).

NO esta conectado a ninguna ruta publica ni a RabbitMQ (eso es el paso 4.4). Sin escrituras en la base.

preparar_consulta(...) devuelve
  {"accion": "aclarar", "tipo": ..., "texto": ...}     -> responder con la plantilla fija, SIN llamar al modelo
  {"accion": "consultar", "peticion": ..., "contexto_compacto": ..., "contexto_ids": [...], "notas": {...}}
"""
from datetime import date

from services.orquestador.contexto import construir_contexto
from services.orquestador.ensamblador import ensamblar
from services.orquestador.resolutor import cargar_catalogo, resolver


async def preparar_consulta(pregunta: str, db, metadatos: dict, *, hoy: date | None = None, catalogo=None, fijados=None, **inyecciones) -> dict:
    hoy = hoy or date.today()
    catalogo = catalogo or cargar_catalogo(db)
    resolucion = resolver(pregunta, catalogo, hoy, fijados)
    if resolucion.aclaracion is not None:
        return {"accion": "aclarar", **resolucion.aclaracion}
    contexto = await construir_contexto(resolucion, db, hoy_anio=hoy.year, **inyecciones)
    resultado = ensamblar(pregunta, contexto, metadatos)
    return {"accion": "consultar", "resolucion": resolucion.a_dict(), **resultado}
