"""Plantillas de respuesta de la familia A (con contexto) y piezas reutilizadas por D, E y F.

Reglas de fase5-1 §2: español formal, sin emojis ni Markdown, 1 a 3 frases (series hasta ~120 palabras), cada dato
con `Municipio (Departamento)` y año copiados DEL CONTEXTO, advertencia solo en proyecciones y con la fiabilidad
tal cual aparece en cada linea (nunca promediada). Las cifras llegan del contexto; las unicas cifras nuevas son una
diferencia o una variacion porcentual entre valores del contexto (maximo ~10 % de los pares) y se calculan sobre
los valores MOSTRADOS, exigiendo que coincidan con el calculo del validador (que usa los valores sin redondear).
"""
from . import lengua as lg
from .resp_comun import Eleccion, aviso, datos_de, fiab_frase, lista_anios, sust, ser, tiempos, unir_pc


def _campos(d: lg.Dato) -> dict:
    t = tiempos(d.anio)
    return {"cita": d.cita, "varl": lg.var_frase(d.variable), "de_varl": lg.var_de(d.variable), "Varl": lg.cap1(lg.var_frase(d.variable)),
            "anio": d.anio, "val": d.texto_valor, "fuente": d.fuente, **t}


# --------------------------------------------------------------------------- A1: dato puntual real
A1 = [
    "En {anio}, {varl} en {cita} {fue} de {val}.",
    "{Varl} en {cita} {fue} de {val} en {anio}.",
    "Para {anio}, {cita} {reg} {varl} de {val}.",
    "El dato de {anio} indica que {varl} en {cita} {fue} de {val}.",
    "{cita} {tuvo} en {anio} {varl} de {val}.",
    "{val} {fue} el valor {de_varl} en {cita} en {anio}.",
    "En {cita}, {varl} {fue} de {val} en {anio} (fuente: {fuente}).",
    "Los datos de {fuente} muestran, para {anio}, {varl} de {val} en {cita}.",
    "El resultado es {val}, que corresponde al valor {de_varl} en {cita} en {anio}.",
    "{cita}: {varl} de {val} en {anio}.",
    "{Varl} de {cita} para {anio}: {val}.",
    "El valor {de_varl} en {cita} para {anio} {fue} {val}.",
    "En {anio}, {cita} {pres} {varl} de {val}.",
    "{sereg} {varl} de {val} en {cita} para {anio}.",
    "Para {cita}, el valor {de_varl} en {anio} {fue} {val}.",
    "El registro de {anio} para {varl} en {cita} es {val}.",
]


def frase_dato_real(d: lg.Dato, el: Eleccion, clave: str = "A1") -> tuple[str, str]:
    i, f = el.de(clave, A1)
    return f.format(**_campos(d)), f"{clave}.r{i:02d}"


def a1(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    d = ctx.datos[0]
    texto, pid = frase_dato_real(d, el, "A1")
    return texto, pid


# --------------------------------------------------------------------------- A2: dato puntual proyeccion
A2_BASE = [
    "Para {anio}, {varl} en {cita} {est} en {val}.",
    "{Varl} en {cita} para {anio} {est} en {val}.",
    "El valor {de_varl} en {cita} para {anio} {fue} de {val}.",
    "En {anio}, {cita} tendría {varl} de {val}.",
    "{cita}: {varl} de {val} para {anio}.",
    "Para {anio}, la cifra {de_varl} en {cita} es de {val}.",
    "En {cita}, {varl} para {anio} es de {val}.",
    "Respecto de {anio}, {varl} en {cita} {est} en {val}.",
    "El dato de {anio} para {varl} en {cita} es {val}.",
    "Para {cita} en {anio}, {varl} da un valor de {val}.",
    "Con corte a {anio}, {varl} en {cita} se ubica en {val}.",
    "{Varl} de {cita} en {anio}: {val}.",
]


def frase_dato_proy(d: lg.Dato, el: Eleccion, clave: str = "A2") -> tuple[str, str]:
    i, base = el.de(clave + "b", A2_BASE)
    j, av = aviso([d], el, clave + "a")
    return f"{base.format(**_campos(d))} {av}", f"{clave}.b{i:02d}+a{j:02d}"


def a2(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    return frase_dato_proy(ctx.datos[0], el, "A2")


def frase_dato(d: lg.Dato, el: Eleccion, clave: str) -> tuple[str, str]:
    return frase_dato_real(d, el, clave) if d.real else frase_dato_proy(d, el, clave)


# --------------------------------------------------------------------------- derivados validados
def _a_float(texto: str) -> float:
    s = texto.replace(" hab/km²", "").replace(" %", "").replace(".", "").replace(",", ".")
    return float(s)


def diferencia_consistente(a: lg.Dato, b: lg.Dato) -> str | None:
    """Diferencia entre los valores MOSTRADOS, solo si coincide (al redondear a los decimales de la variable) con la
    de los valores sin redondear que usa el validador. None si no coincide: se elige otro par."""
    if a.variable != b.variable:
        return None
    dec = lg.decimales_variable(a.variable, max(abs(a.valor), abs(b.valor)))
    mostrada = round(abs(_a_float(a.texto_valor) - _a_float(b.texto_valor)), dec)
    real = round(abs(a.valor - b.valor), dec)
    if mostrada != real or mostrada == 0:
        return None
    return lg.texto_diferencia(a.variable, _a_float(a.texto_valor), _a_float(b.texto_valor))


def variacion_consistente(antes: lg.Dato, despues: lg.Dato) -> str | None:
    va, vb = _a_float(antes.texto_valor), _a_float(despues.texto_valor)
    if not va or not antes.valor:
        return None
    mostrada = round(abs((vb - va) / va * 100.0), 1)
    real = round(abs((despues.valor - antes.valor) / antes.valor * 100.0), 1)
    if mostrada != real or mostrada == 0:
        return None
    return lg.texto_variacion(va, vb)


# --------------------------------------------------------------------------- A3: serie o rango
LEAD_SERIE = [
    "En {cita}, {varl} registra estos valores: {lista}.",
    "{Varl} en {cita}: {lista}.",
    "Estos son los valores {de_varl} en {cita}: {lista}.",
    "En {cita}, {varl} muestra la siguiente serie: {lista}.",
    "La serie {de_varl} en {cita} es la siguiente: {lista}.",
    "{lista}: así evoluciona {varl} en {cita}.",
    "{cita} presenta {varl} de {lista}.",
    "Los datos {de_varl} en {cita} son: {lista}.",
    "Esto es lo que hay {de_varl} en {cita}: {lista}.",
    "{Varl}, {cita}. {lista}.",
]


def _lista_valores(ds: list[lg.Dato], etiquetar: bool = False) -> str:
    return lg.unir([f"{d.texto_valor}{_etiqueta_proy(d) if etiquetar else ''} en {d.anio}" for d in ds])


def _cola_mixta(reales: list[lg.Dato], proy: list[lg.Dato], el: Eleccion, clave: str) -> tuple[str, str]:
    ar, ap = lista_anios(reales), lista_anios(proy)
    nr, np_ = len(reales), len(proy)
    fiab = fiab_frase(proy)
    est = sust(np_, "una estimación", "estimaciones")
    real = sust(nr, "real", "reales")
    posterior = min(d.anio for d in proy) > max(d.anio for d in reales)
    opciones = [
        f"{datos_de(nr).capitalize()} {ar} {ser(nr)} {real} y {datos_de(np_)} {ap} {ser(np_)} {est}, con {fiab}.",
        f"{datos_de(np_).capitalize()} {ap} {ser(np_)} {est}, con {fiab}; {datos_de(nr)} {ar} {ser(nr)} {real}.",
        f"Aviso: {datos_de(np_)} {ap} {ser(np_)} {est} (con {fiab}). {datos_de(nr).capitalize()} {ar} {ser(nr)} {real}.",
        f"Distingue entre ambos: {datos_de(nr)} {ar} {ser(nr)} {real}, y {datos_de(np_)} {ap} {ser(np_)} {est}, con {fiab}.",
    ]
    if posterior:
        opciones.append(f"Los datos reales llegan hasta {max(d.anio for d in reales)}; después se trata de {est}, con {fiab}.")
    i, t = el.de(clave, opciones)
    return t, f"m{i}"


def _pid_cola_derivada(derivada: str | None) -> str:
    return "+d" if derivada else ""


def a3(ctx: lg.Contexto, el: Eleccion, con_derivado: bool = False) -> tuple[str, str]:
    """Serie de UNA variable en UN municipio, o de varias variables (un parrafo por variable)."""
    por_var: dict[str, list[lg.Dato]] = {}
    for d in ctx.datos:
        por_var.setdefault(d.variable, []).append(d)
    partes, pids = [], []
    todos = ctx.datos
    reales = [d for d in todos if d.real]
    proy = [d for d in todos if not d.real]
    cita = todos[0].cita
    for variable, ds in por_var.items():
        ds = sorted(ds, key=lambda d: d.anio)
        i, lead = el.de("A3l", LEAD_SERIE)
        partes.append(lead.format(cita=cita, varl=lg.var_frase(variable), de_varl=lg.var_de(variable), Varl=lg.cap1(lg.var_frase(variable)),
                                  lista=_lista_valores(ds, etiquetar=len(por_var) > 1)))
        pids.append(f"l{i:02d}")
    texto = " ".join(partes)
    if reales and proy:
        if len(por_var) == 1:
            cola, pid = _cola_mixta(sorted(reales, key=lambda d: d.anio), sorted(proy, key=lambda d: d.anio), el, "A3m")
        else:
            cola, pid = _cola_estimaciones(todos, el, "A3c")
            pid = "mv" + pid
        texto += " " + cola
        pids.append(pid)
    elif proy:
        j, av = aviso(proy, el, "A3a")
        texto += " " + av
        pids.append(f"a{j:02d}")
    if con_derivado and len(por_var) == 1:
        ds = sorted(todos, key=lambda d: d.anio)
        primero, ultimo = ds[0], ds[-1]
        if primero.real == ultimo.real and primero.anio != ultimo.anio:
            if el.rng.random() < 0.5:
                dif = diferencia_consistente(primero, ultimo)
                if dif:
                    verbo = "aumentó" if ultimo.valor > primero.valor else "disminuyó"
                    texto += f" Entre {primero.anio} y {ultimo.anio}, {lg.var_frase(primero.variable)} {verbo} {dif}."
                    pids.append("d1")
            else:
                var = variacion_consistente(primero, ultimo)
                if var:
                    verbo = "aumentó" if ultimo.valor > primero.valor else "disminuyó"
                    texto += f" Entre {primero.anio} y {ultimo.anio}, {lg.var_frase(primero.variable)} {verbo} {var}."
                    pids.append("d2")
    return texto, "A3." + "".join(pids)


def lista_anios_unicos(ds: list[lg.Dato]) -> str:
    return lg.unir([str(a) for a in sorted({d.anio for d in ds})])


# --------------------------------------------------------------------------- etiquetado en linea (A4, A5, A7)
def _etiqueta_proy(d: lg.Dato) -> str:
    return "" if d.real else " (estimación)"


def _cola_estimaciones(ds: list[lg.Dato], el: Eleccion, clave: str) -> tuple[str, str]:
    proy = [d for d in ds if not d.real]
    if not proy:
        return "", ""
    fiab = fiab_frase(proy)
    if len(proy) == len(ds):
        i, t = el.de(clave + "t", [
            f"Todos los valores son estimaciones, con {fiab}.",
            f"Se trata de valores proyectados, con {fiab}.",
            f"Son estimaciones, no datos observados, con {fiab}.",
            f"Todos son proyecciones, con {fiab}.",
        ])
        return t, f"t{i}"
    i, t = el.de(clave + "m", [
        f"Los valores señalados como estimación tienen {fiab}; los demás son datos reales.",
        f"Las estimaciones marcadas tienen {fiab}, y el resto son datos reales.",
        f"Solo los valores marcados como estimación son proyecciones, con {fiab}.",
        f"Los demás valores son datos reales; los marcados como estimación tienen {fiab}.",
    ])
    return t, f"m{i}"


# --------------------------------------------------------------------------- A4: comparacion de municipios
LEAD_COMPARA = [
    "En {anio}, {varl} {fue} así: {lista}.",
    "Estos son los valores {de_varl} en {anio}: {lista}.",
    "Comparando {anio}, {varl} da: {lista}.",
    "{Varl} en {anio}: {lista}.",
    "Para {anio}, {varl} se reparte así entre los municipios consultados: {lista}.",
    "Los valores {de_varl} en {anio} son: {lista}.",
    "En {anio}, estos son los resultados {de_varl}: {lista}.",
    "Lado a lado, {varl} en {anio}: {lista}.",
]
REMARCA_MAX = [
    "El valor más alto corresponde a {cita}.",
    "{cita} tiene el valor más alto entre los municipios consultados.",
    "Entre los municipios consultados, el valor más alto es el de {cita}.",
    "El mayor valor es el de {cita}.",
]
LEAD_COMPARA_ANIOS = [
    "Así estuvo {varl} en cada municipio: {lista}.",
    "Estos son los valores {de_varl} por municipio: {lista}.",
    "{Varl}, municipio por municipio: {lista}.",
    "Por municipio, {varl} registra: {lista}.",
]


def a4(ctx: lg.Contexto, el: Eleccion, con_diferencia: bool = False) -> tuple[str, str]:
    ds = ctx.datos
    variable = ds[0].variable
    anios = sorted({d.anio for d in ds})
    pids = []
    if len(anios) == 1:
        anio = anios[0]
        i, lead = el.de("A4l", LEAD_COMPARA)
        lista = unir_pc([f"{d.cita}: {d.texto_valor}{_etiqueta_proy(d)}" for d in ds])
        texto = lead.format(anio=anio, varl=lg.var_frase(variable), de_varl=lg.var_de(variable), Varl=lg.cap1(lg.var_frase(variable)), lista=lista, **tiempos(anio))
        pids.append(f"l{i:02d}")
        valores = sorted(ds, key=lambda d: d.valor)
        if len(ds) >= 2 and valores[-1].valor != valores[-2].valor:
            if con_diferencia and len(ds) >= 2 and valores[0].valor != valores[-1].valor:
                dif = diferencia_consistente(valores[-1], valores[0])
                if dif:
                    texto += f" La diferencia entre {valores[-1].cita} y {valores[0].cita} es de {dif}."
                    pids.append("d")
            if "d" not in pids:
                j, r = el.de("A4r", REMARCA_MAX)
                texto += " " + r.format(cita=valores[-1].cita)
                pids.append(f"r{j}")
    else:
        i, lead = el.de("A4la", LEAD_COMPARA_ANIOS)
        municipios = []
        for d in ds:
            if d.divipola not in municipios:
                municipios.append(d.divipola)
        trozos = []
        for m in municipios:
            propios = sorted((d for d in ds if d.divipola == m), key=lambda d: d.anio)
            trozos.append(f"{propios[0].cita}: " + lg.unir([f"{d.texto_valor}{_etiqueta_proy(d)} en {d.anio}" for d in propios]))
        texto = lead.format(varl=lg.var_frase(variable), de_varl=lg.var_de(variable), Varl=lg.cap1(lg.var_frase(variable)), lista=unir_pc(trozos, con_y=False))
        pids.append(f"a{i}")
        ultimo = [d for d in ds if d.anio == anios[-1]]
        orden = sorted(ultimo, key=lambda d: d.valor)
        if len(orden) >= 2 and orden[-1].valor != orden[-2].valor:
            texto += f" En {anios[-1]}, el valor más alto es el de {orden[-1].cita}."
            pids.append("r")
    cola, pc = _cola_estimaciones(ds, el, "A4c")
    if cola:
        texto += " " + cola
        pids.append(pc)
    return texto, "A4." + "".join(pids)


# --------------------------------------------------------------------------- A5: perfil
LEAD_PERFIL = [
    "En {anio}, esto es lo que hay de {cita}: {lista}.",
    "Perfil de {cita} en {anio}: {lista}.",
    "{cita}, {anio}: {lista}.",
    "Para {anio}, {cita} presenta: {lista}.",
    "Estos son los datos de {cita} para {anio}: {lista}.",
    "Resumen de {cita} en {anio}: {lista}.",
    "Los valores de {cita} en {anio} son: {lista}.",
    "En {cita}, para {anio}: {lista}.",
]
LEAD_PERFIL_ANIOS = [
    "Perfil de {cita}:",
    "Estos son los datos de {cita}:",
    "En {cita}, año por año:",
    "Resumen de {cita}:",
]


def _item_perfil(d: lg.Dato) -> str:
    return f"{lg.nombre_variable(d.variable)}, {d.texto_valor}{_etiqueta_proy(d)}"


def a5(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    ds = ctx.datos
    cita = ds[0].cita
    anios = sorted({d.anio for d in ds})
    pids = []
    if len(anios) == 1:
        i, lead = el.de("A5l", LEAD_PERFIL)
        lista = unir_pc([_item_perfil(d) for d in ds])
        texto = lead.format(anio=anios[0], cita=cita, lista=lista)
        pids.append(f"l{i}")
    else:
        i, lead = el.de("A5la", LEAD_PERFIL_ANIOS)
        trozos = []
        for a in anios:
            propios = [d for d in ds if d.anio == a]
            trozos.append(f"En {a}: " + unir_pc([_item_perfil(d) for d in propios]) + ".")
        texto = lead.format(cita=cita) + " " + " ".join(trozos)
        pids.append(f"a{i}")
    cola, pc = _cola_estimaciones(ds, el, "A5c")
    if cola:
        texto += " " + cola
        pids.append(pc)
    return texto, "A5." + "".join(pids)


# --------------------------------------------------------------------------- A6: anio implicito
A6 = [
    "El último año con dato real es {anio}: {varl} en {cita} {fue} de {val}.",
    "El dato real más reciente es de {anio}: {varl} en {cita} {fue} de {val}.",
    "El último dato real disponible corresponde a {anio}: en {cita}, {varl} {fue} de {val}.",
    "{Varl} en {cita} {fue} de {val} en {anio}, el último año con dato real.",
    "Lo más reciente con dato real es {anio}: {val}, valor {de_varl} en {cita}.",
    "En {cita}, el último dato real {de_varl} es de {anio}: {val}.",
    "Con dato real, el año más reciente es {anio}; ese año {varl} en {cita} {fue} de {val}.",
    "Para {cita}, {varl} {reg} {val} en {anio}, el último año con dato real.",
    "No indicaste un año, así que tomo el último con dato real, {anio}: {varl} en {cita} {fue} de {val}.",
]


def a6(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    ds = ctx.datos
    if len(ds) == 1:
        d = ds[0]
        i, f = el.de("A6", A6)
        return f.format(**_campos(d)), f"A6.{i:02d}"
    anios = sorted({d.anio for d in ds})
    if len(anios) == 1:
        trozos = []
        for d in ds:
            trozos.append(f"{d.cita}: {d.texto_valor}" if len({x.divipola for x in ds}) > 1 else f"{lg.nombre_variable(d.variable)}, {d.texto_valor}")
        i, f = el.de("A6m1", [
            "El último año con dato real es {anio}. Los valores son: {lista}.",
            "Tomo el último año con dato real, {anio}: {lista}.",
            "El dato real más reciente es de {anio}: {lista}.",
            "Con dato real, lo más reciente es {anio}: {lista}.",
        ])
        return f.format(anio=anios[0], lista=unir_pc(trozos)), f"A6.m1.{i}"
    trozos = []
    for d in ds:
        sujeto = d.cita if len({x.divipola for x in ds}) > 1 else lg.nombre_variable(d.variable)
        trozos.append(f"{sujeto}, {d.texto_valor} en {d.anio}")
    i, f = el.de("A6m2", [
        "Estos son los datos reales más recientes: {lista}.",
        "Tomo el último año con dato real de cada uno: {lista}.",
        "Lo más reciente con dato real es: {lista}.",
    ])
    cita = ds[0].cita if len({x.divipola for x in ds}) == 1 else ""
    prefijo = f"En {cita}, " if cita else ""
    texto = f.format(lista=unir_pc(trozos))
    return (prefijo + texto[0].lower() + texto[1:] if prefijo else texto), f"A6.m2.{i}"


# --------------------------------------------------------------------------- A7: contexto con omision
def _muestra_a7(datos: list[lg.Dato]) -> list[lg.Dato]:
    """Hasta 3 datos de combinaciones (municipio, variable) distintas, en el orden del contexto."""
    vistos, muestra = set(), []
    for d in datos:
        clave = (d.divipola, d.variable)
        if clave not in vistos:
            vistos.add(clave)
            muestra.append(d)
        if len(muestra) == 3:
            break
    return muestra


def a7(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    ds = _muestra_a7(ctx.datos)
    mismo = len({d.divipola for d in ds}) == 1
    if mismo:
        trozos = [f"{lg.nombre_variable(d.variable)} {d.texto_valor} en {d.anio}{_etiqueta_proy(d)}" for d in ds]
    else:
        trozos = [f"{d.cita}, {lg.nombre_variable(d.variable)} {d.texto_valor} en {d.anio}{_etiqueta_proy(d)}" for d in ds]
    i, f = el.de("A7", [
        "Puedo mostrarte una parte: {lista}. Se omitieron {n} datos por el límite de contexto; para ver el resto, acota la consulta a menos municipios, variables o años.",
        "Esta consulta trae más datos de los que caben a la vez. Con lo disponible: {lista}. Faltan {n} datos; pídelos por partes, con menos municipios, variables o años.",
        "Por el límite de contexto solo puedo responder con una parte de lo que pides: {lista}. No incluí {n} datos; acota la consulta para ver el resto.",
        "Con lo que cabe en la consulta: {lista}. Quedaron fuera {n} datos por el límite de contexto; si quieres, pregunta por un municipio o una variable a la vez.",
        "Lo que alcanzo a mostrarte es esto: {lista}. Se omitieron {n} datos; para completar la respuesta, reduce los municipios, las variables o los años.",
        "Solo cabe una parte de lo solicitado: {lista}. Dejé por fuera {n} datos por el límite de contexto; divide la consulta en partes más pequeñas.",
    ])
    lista = unir_pc(trozos)
    if mismo:
        lista = f"en {ds[0].cita}, {lista}"
    texto = f.format(lista=lista, n=ctx.omitidos)
    proy = [d for d in ds if not d.real]
    pid = f"A7.{i}"
    if proy:
        texto += f" Los valores marcados como estimación tienen {fiab_frase(proy)}."
        pid += "+e"
    return texto, pid
