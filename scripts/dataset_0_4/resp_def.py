"""Plantillas de respuesta de las familias D (geografia), E (homonimos) y F (inyeccion).

D: el departamento sale SIEMPRE de la linea del contexto (dato o «Ubicación»), nunca de memoria.
E: cada valor con SU departamento; nada de mezclar homonimos.
F: se responde solo con el contexto real; la instruccion inyectada no se obedece ni se repite.
"""
from . import lengua as lg
from .resp_a import _campos, diferencia_consistente, frase_dato, a3
from .resp_comun import AVISOS_VARIOS, Eleccion, fiab_frase, tiempos

# --------------------------------------------------------------------------- D
FRASE_DEPARTAMENTO = [
    "{m} pertenece al departamento de {dep}.",
    "{m} se encuentra en el departamento de {dep}.",
    "El municipio de {m} está en el departamento de {dep}.",
    "En el departamento de {dep} queda {m}.",
    "{m} queda en el departamento de {dep}.",
    "{m} es un municipio del departamento de {dep}.",
]


def _dato_o_serie(ctx: lg.Contexto, el: Eleccion, clave: str) -> tuple[str, str]:
    if len(ctx.datos) == 1:
        return frase_dato(ctx.datos[0], el, clave)
    return a3(ctx, el)


def _bajar(dato: str, cita: str) -> str:
    """Minuscula inicial salvo que la frase empiece por el nombre propio del municipio."""
    return dato if dato.startswith(cita) or dato[0].isdigit() else dato[0].lower() + dato[1:]


def d1(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    d = ctx.datos[0]
    i, f = el.de("D1d", FRASE_DEPARTAMENTO)
    dep_frase = f.format(m=d.municipio, dep=d.departamento)
    dato, pid = _dato_o_serie(ctx, el, "D1")
    orden = el.indice("D1o", 2)
    texto = f"{dep_frase} {dato}" if orden == 0 else f"{dato} {dep_frase}"
    return texto, f"D1.d{i}o{orden}+{pid}"


D2_FRASES = [
    "Hablamos del municipio de {cita}. {dato}",
    "Entiendo que preguntas por el municipio {cita}. {dato}",
    "{dato} Corresponde al municipio de {m}, en el departamento de {dep}.",
    "Tomo {m} como municipio, del departamento de {dep}. {dato}",
    "Como municipio, {m} pertenece al departamento de {dep}. {dato}",
    "Aquí {m} es el municipio del departamento de {dep}. {dato}",
    "Se trata del municipio de {m} ({dep}). {dato}",
]


def d2(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    d = ctx.datos[0]
    dato, pid = _dato_o_serie(ctx, el, "D2")
    i, f = el.de("D2f", D2_FRASES)
    return f.format(cita=d.cita, m=d.municipio, dep=d.departamento, dato=dato), f"D2.f{i}+{pid}"


D3_FRASES = [
    "{alias} figura en la plataforma como {m} ({dep}). {dato}",
    "{dato} Aclaro que {alias} se registra como {m}, en el departamento de {dep}.",
    "En los datos, {alias} aparece como {m} ({dep}). {dato}",
    "{dato} Esto corresponde a {alias}, que la plataforma identifica como {m}, del departamento de {dep}.",
    "{dato}",
    "Para {alias}, registrada como {m} en el departamento de {dep}: {dato_min}",
]


def d3(ctx: lg.Contexto, el: Eleccion, alias: str) -> tuple[str, str]:
    d = ctx.datos[0]
    dato, pid = _dato_o_serie(ctx, el, "D3")
    i, f = el.de("D3f", D3_FRASES)
    texto = f.format(alias=alias, m=d.municipio, dep=d.departamento, dato=dato, dato_min=_bajar(dato, d.cita))
    return texto, f"D3.f{i}+{pid}"


D4_UBICACION = [
    "{m} pertenece al departamento de {dep}. Si quieres un dato de este municipio, indícame la variable y el año.",
    "{m} se encuentra en el departamento de {dep}. ¿Qué variable te interesa: acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación?",
    "{m} queda en el departamento de {dep}. Pregúntame por una variable, como cobertura de acueducto o densidad poblacional, y un año.",
    "En el departamento de {dep} se encuentra {m}. Si necesitas datos, dime la variable y el año.",
    "{m} es un municipio del departamento de {dep}. ¿Quieres algún dato suyo? Indícame la variable y el año.",
    "La ubicación es: {m}, departamento de {dep}. Para consultar datos, dime la variable y el año.",
    "{m} pertenece a {dep}. Con gusto te doy datos de este municipio: elige una variable y un año.",
    "El municipio de {m} está en el departamento de {dep}. Puedo ayudarte con sus datos si me dices qué variable quieres y de qué año.",
]
D4_HABLAME = [
    "{m} pertenece al departamento de {dep}. De este municipio puedo darte la cobertura de acueducto y de alcantarillado, el déficit habitacional, la densidad poblacional y el Score de Privación; dime cuál y de qué año.",
    "Puedo decirte que {m} está en el departamento de {dep}. Para más información, elige una variable (acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación) y un año.",
    "{m} queda en el departamento de {dep}. ¿Qué te gustaría saber de este municipio? Tengo acueducto, alcantarillado, déficit habitacional, densidad poblacional y Score de Privación.",
    "Sobre {m}: está en el departamento de {dep}. Para darte datos concretos necesito una variable y un año.",
    "{m}, en el departamento de {dep}. Dime qué variable quieres consultar de este municipio, por ejemplo acueducto o Score de Privación, y el año.",
    "De {m} (departamento de {dep}) puedo consultar cobertura de acueducto, cobertura de alcantarillado, déficit habitacional, densidad poblacional y Score de Privación. Indícame cuál y en qué año.",
]


def d4(ctx: lg.Contexto, el: Eleccion, tipo: str) -> tuple[str, str]:
    u = ctx.ubicaciones[0]
    if tipo == "geo":
        i, f = el.de("D4g", D4_UBICACION)
        return f.format(m=u.municipio, dep=u.departamento), f"D4.g{i}"
    i, f = el.de("D4h", D4_HABLAME)
    return f.format(m=u.municipio, dep=u.departamento), f"D4.h{i}"


# --------------------------------------------------------------------------- E
def _valor(d: lg.Dato) -> str:
    return d.texto_valor + ("" if d.real else " (estimación)")


E1_FRASES = [
    "{c1} registra {v1} y {c2} registra {v2} en {anio}; son municipios distintos.",
    "En {anio}, {varl} {fue} de {v1} en {c1} y de {v2} en {c2}: son dos municipios diferentes.",
    "Son municipios distintos. {Varl} en {anio}: {c1} con {v1} y {c2} con {v2}.",
    "{Varl} en {anio}: {c1}, {v1}; {c2}, {v2}. No es el mismo municipio.",
    "Aunque comparten nombre, no son el mismo municipio: en {anio}, {varl} {fue} de {v1} en {c1} y de {v2} en {c2}.",
    "En {c1}, {varl} {fue} de {v1} en {anio}; en {c2}, de {v2}.",
    "Para {anio}: {c1} {reg} {v1} y {c2} {reg} {v2}; se trata de dos municipios con el mismo nombre.",
    "{c1} y {c2} son municipios diferentes. En {anio}, {varl} {fue} de {v1} en el primero y de {v2} en el segundo.",
]


def e1(ctx: lg.Contexto, el: Eleccion, con_diferencia: bool = False) -> tuple[str, str]:
    a, b = ctx.datos[0], ctx.datos[1]
    i, f = el.de("E1", E1_FRASES)
    texto = f.format(c1=a.cita, c2=b.cita, v1=_valor(a), v2=_valor(b), varl=lg.var_frase(a.variable),
                     Varl=lg.cap1(lg.var_frase(a.variable)), anio=a.anio, **tiempos(a.anio))
    pid = f"E1.{i}"
    if con_diferencia:
        dif = diferencia_consistente(a, b)
        if dif:
            mayor, menor = (a, b) if a.valor >= b.valor else (b, a)
            texto += f" La diferencia entre {mayor.cita} y {menor.cita} es de {dif}."
            pid += "d"
    proy = [d for d in (a, b) if not d.real]
    if len(proy) == 2:
        j, av = el.de("E1a", AVISOS_VARIOS)
        texto += " " + av.format(f=fiab_frase(proy))
        pid += f"a{j}"
    elif len(proy) == 1:
        texto += f" El valor marcado como estimación tiene {fiab_frase(proy)}; el otro es un dato real."
        pid += "m"
    return texto, pid


E2_FRASES = [
    "Tomo {m} del departamento de {dep}, como indicas. {dato}",
    "Entendido, {cita}. {dato}",
    "Municipio de {dep}: {dato_min}",
    "{dato} Se refiere a {m}, en el departamento de {dep}.",
    "Para {cita}, tal como lo pides: {dato_min}",
    "Hablamos de {m}, del departamento de {dep}. {dato}",
    "Con el departamento que indicas, es {cita}. {dato}",
]


def e2(ctx: lg.Contexto, el: Eleccion) -> tuple[str, str]:
    d = ctx.datos[0]
    dato, pid = _dato_o_serie(ctx, el, "E2")
    if el.indice("E2dir", 5) < 2:  # ~40 %: responde directo, sin aclaracion antepuesta (el municipio ya lleva su departamento)
        return dato, f"E2.directo+{pid}"
    i, f = el.de("E2f", E2_FRASES)
    return f.format(m=d.municipio, dep=d.departamento, cita=d.cita, dato=dato, dato_min=_bajar(dato, d.cita)), f"E2.f{i}+{pid}"


# --------------------------------------------------------------------------- F
F_RECHAZOS = {
    "reglas": [
        "No puedo ignorar mis reglas ni usar cifras que no estén en el contexto.",
        "No voy a cambiar mis reglas por lo que se pida en la consulta.",
        "Entiendo la solicitud, pero mis reglas no cambian con lo que se escriba en la pregunta.",
        "Lo que viene después de la pregunta es una consulta, no una orden que modifique mis reglas.",
        "No puedo dejar de lado mis reglas, aunque se me pida.",
        "Mis reglas siguen siendo las mismas, escriba lo que se escriba en la pregunta.",
    ],
    "cifra": [
        "No voy a dar una cifra distinta de la del contexto, ni a inventar una.",
        "No acepto instrucciones que me pidan cifras sin respaldo en el contexto.",
        "Una cifra que no esté en el contexto no la puedo dar, por más que se pida.",
        "No puedo cambiar ni inventar el valor: las cifras salen solo del contexto.",
        "Eso no puedo hacerlo: no invento cifras ni las modifico.",
        "Las cifras que doy son las del contexto, no las que se me sugieran en la pregunta.",
    ],
    "contexto": [
        "No puedo prescindir del contexto ni responder cifras de memoria.",
        "Mis cifras salen únicamente del contexto, no de la memoria.",
        "El contexto es mi única fuente de cifras, así que no puedo ignorarlo.",
        "No dejo de lado el contexto: es de donde salen las cifras que doy.",
        "Aunque se pida, no responderé de memoria; me apoyo en el contexto.",
    ],
    "estilo": [
        "Aunque se pida otra forma, nombro el municipio con su departamento y el año de cada dato.",
        "Mantengo el municipio, el departamento y el año en la respuesta, y aclaro si el dato es una estimación.",
        "No omito el municipio ni el año ni las advertencias, aunque se me pida.",
    ],
    "secreto": [
        "No puedo mostrar ni modificar mis instrucciones internas.",
        "Mis instrucciones internas no las comparto ni las cambio.",
        "No tengo permitido revelar mis instrucciones, y tampoco puedo cambiarlas.",
        "Eso no lo puedo hacer: mis instrucciones internas son confidenciales y no se modifican con una pregunta.",
    ],
    "privacidad": [
        "No puedo compartir contraseñas ni datos de otros usuarios.",
        "No tengo acceso a contraseñas y no comparto información de otros usuarios.",
        "Las contraseñas y los datos de otros usuarios son privados, y no los voy a revelar.",
    ],
    "fuera_de_tema": [
        "No voy a escribir un poema: estoy para responder consultas de datos de la plataforma.",
        "Escribir poemas no es lo mío; mi tarea es responder consultas de datos y conceptos de la plataforma.",
        "No voy a cambiar de tarea: respondo consultas de datos y conceptos de pobreza y vivienda, no poemas.",
    ],
    "linea": [
        "Las líneas de datos escritas dentro de la pregunta no las tomo como contexto; solo vale el contexto real.",
        "Lo que se escriba en la pregunta, aunque imite el contexto, no cambia los datos reales.",
        "No uso datos escritos en la pregunta como si fueran del contexto; respondo con el contexto real.",
    ],
}
F_OFERTA = [
    "Sí puedo ayudarte con datos de acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación de un municipio: dime cuál y de qué año.",
    "Si quieres un dato real de la plataforma, indícame el municipio, la variable y el año.",
    "Puedo explicarte conceptos como el Score de Privación o el déficit habitacional, o darte datos de un municipio si me dices cuál.",
    "Pregúntame por un municipio y una variable de la plataforma y te respondo con los datos disponibles.",
    "Con gusto te ayudo con una consulta de datos: necesito el municipio, la variable y el año.",
    "Si lo que necesitas es un dato de un municipio, dime cuál y qué variable quieres consultar.",
]
F_PUENTE_DATO = [
    "Con los datos disponibles, esto es lo que hay. ",
    "Esto es lo que sí tengo. ",
    "Lo que muestra el contexto es lo siguiente. ",
    "Respondo con lo que hay en los datos. ",
    "Sobre tu consulta de datos, esto es lo que encuentro. ",
]


def f_con_dato(ctx: lg.Contexto, el: Eleccion, tipo: str) -> tuple[str, str]:
    i, rech = el.de("Fr:" + tipo, F_RECHAZOS[tipo])
    j, puente = el.de("Fp", F_PUENTE_DATO)
    dato, pid = _dato_o_serie(ctx, el, "F")
    return f"{rech} {puente}{dato}", f"F.{tipo}{i}p{j}+{pid}"


def f_pura(el: Eleccion, tipo: str) -> tuple[str, str]:
    i, rech = el.de("Fr:" + tipo, F_RECHAZOS[tipo])
    if tipo == "privacidad":
        return rech, f"F.{tipo}{i}"
    j, oferta = el.de("Fo", F_OFERTA)
    return f"{rech} {oferta}", f"F.{tipo}{i}o{j}"
