"""7.1 / W1 Payload de grafica para el frontend (mitad backend de 7.6; documentado en claude/fase7-6-payload.md).

construir_payload(spec, ...) -> dict
  {"v":1,"tipo","modo","variables","zonas","anios",["titulo"],
   "datos":[{"variable","zona","nombre","anio","valor","origen":"real"|"proyeccion","fiabilidad":null|N}],
   "universo": null | {"periodo":[anio_inicio, anio_fin], "estadisticos":{variable:{media,min,q1,mediana,q3,max,n}}}}

El modelo solo emite la INTENCION (grafica_bloque.py); los valores salen de aqui, de la base y de la proyeccion, con
los mismos lectores que el contexto. El frontend no consulta nada por su cuenta. `universo` solo se arma para los
tipos que lo necesitan segun el catalogo (gauge, bullet, barras_ranking, ...) y viene de reportes_datos_graficas
(mismo calculo que el reporte). Un punto sin valor no se inventa: simplemente no aparece en `datos`. Si falta el
universo de un tipo que lo necesita, la grafica no se entrega (ErrorPayload).
"""
from services import proyeccion_service as pservicio
from services.orquestador.alias import V_SCORE
from services.orquestador.contexto import _item_desde_fila, _item_desde_punto, _score
from services.reportes_graficas_catalogo import TIPOS

V_PAYLOAD = 1


class ErrorPayload(Exception):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def _fila(variable: str, zona: str, nombre: str, item: dict) -> dict:
    return {
        "variable": variable, "zona": zona, "nombre": nombre, "anio": item["anio"], "valor": item["valor"],
        "origen": "real" if item["es_real"] else "proyeccion", "fiabilidad": item["fiabilidad"],
    }


async def _items_de_serie(variable, municipio, anios, *, leer_serie_fn, completar, score_fn, score_con_filas) -> list[dict]:
    zona = municipio["divipola"]
    if variable == V_SCORE:
        items = []
        for anio in anios:
            estado, dato = await _score(municipio, anio, None, leer_serie_fn, score_fn)
            if estado == "sin_hueco":
                dato = await score_con_filas(municipio, anio) if score_con_filas is not None else None
            elif estado != "ok":
                dato = None
            if dato is not None:
                items.append(dato)
        return items
    serie = await leer_serie_fn(zona, variable)
    por_anio = {f["anio"]: f for f in serie if f["valor"] is not None}
    items = [_item_desde_fila(variable, municipio, por_anio[a]) for a in anios if a in por_anio]
    faltan = [a for a in anios if a not in por_anio]
    for i in range(0, len(faltan), pservicio.MAX_LOTE_PUNTOS):
        lote = faltan[i:i + pservicio.MAX_LOTE_PUNTOS]
        puntos = await completar([{"indicador": variable, "codigo": zona, "anio": a} for a in lote])
        items.extend(_item_desde_punto(variable, municipio, p) for p in puntos if p["estado"] == "calculado")
    return items


async def construir_payload(spec: dict, municipios: dict, *, leer_serie_fn, completar, score_fn, score_con_filas=None, universo_fn=None) -> dict:
    """`spec`: especificacion YA validada (GraficaSpec.model_dump()). `municipios`: codigo -> objeto con
    divipola, nombre y departamento (el catalogo del resolutor)."""
    anios = list(spec["anios"])
    datos = []
    for variable in spec["variables"]:
        for zona in spec["zonas"]:
            m = municipios[zona]
            municipio = {"divipola": m.divipola, "nombre": m.nombre, "departamento": m.departamento}
            nombre = f"{m.nombre} ({m.departamento})" if m.departamento else m.nombre
            items = await _items_de_serie(variable, municipio, anios, leer_serie_fn=leer_serie_fn, completar=completar,
                                          score_fn=score_fn, score_con_filas=score_con_filas)
            datos.extend(_fila(variable, zona, nombre, it) for it in sorted(items, key=lambda i: i["anio"]))
    universo = None
    if TIPOS[spec["tipo"]].necesita_nacional:
        if universo_fn is None:
            raise ErrorPayload("universo_no_disponible: no hay lector del universo nacional")
        estadisticos = await universo_fn(list(spec["variables"]), min(anios), max(anios))
        faltan = [v for v in spec["variables"] if v not in estadisticos]
        if faltan:
            raise ErrorPayload(f"universo_no_disponible: sin estadisticos nacionales de {faltan[0]}")
        universo = {"periodo": [min(anios), max(anios)], "estadisticos": {v: estadisticos[v] for v in spec["variables"]}}
    payload = {k: spec[k] for k in ("tipo", "modo", "variables", "zonas", "anios")}
    if spec.get("titulo"):
        payload["titulo"] = spec["titulo"]
    return {"v": V_PAYLOAD, **payload, "datos": datos, "universo": universo}
