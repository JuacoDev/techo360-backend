"""Plantillas de respuesta de la familia B (abstencion). Regla: decir que no hay el dato, sin estimar ni dar
cifras de municipios; ofrecer lo que si hay o como reformular. Sin numeros salvo anios del contexto o de la pregunta
(`B3_CON_CIFRAS` es la excepcion explicita y apagada por defecto: ver README)."""
from . import lengua as lg
from .resp_comun import Eleccion, unir_pc

# --------------------------------------------------------------------------- motivos en lenguaje llano
MOTIVO_LLANO = {
    "no hay datos anteriores suficientes para estimarlo": [
        "no hay datos anteriores suficientes para calcularlo", "no existen datos anteriores suficientes para calcularlo"],
    "falta un valor de referencia para estimarlo": [
        "falta un valor de referencia para calcularlo", "no hay un valor de referencia con el cual calcularlo"],
    "el año está fuera del rango de la variable": [
        "ese año queda fuera del periodo que cubre esta variable", "ese año está fuera del periodo que cubre esta variable"],
    "el municipio no está en el universo de consulta": [
        "el municipio no hace parte de los que se pueden consultar", "ese municipio no está entre los que cubre la consulta"],
    "el valor estimado no es confiable": [
        "el valor que resultaría no es confiable", "el cálculo no arroja un valor confiable"],
    "falta alguno de los componentes del score": [
        "falta alguno de los datos con los que se calcula el score", "no están todos los componentes del score"],
    "no se pudo calcular el score": [
        "no fue posible calcular el score", "el score no se pudo calcular"],
    "no hay ningún dato real": [
        "no hay ningún dato real registrado para este municipio", "este municipio no tiene ningún dato real de esa variable"],
}


def motivo_llano(sd: lg.SinDato, el: Eleccion) -> str:
    _, m = el.de("motivo:" + sd.motivo, MOTIVO_LLANO[sd.motivo])
    return m


def _nombre(variable: str) -> str:
    return lg.nombre_variable(variable)


def _dato(variable: str) -> str:
    return f"el dato {lg.var_de(variable)}"


# --------------------------------------------------------------------------- B1: linea «Sin dato» pedida
B1_ANIO = [
    "No cuento con {dato} de {cita} para {anio}: {motivo}. Puedes consultar otro año u otra variable de ese municipio.",
    "Para {anio} no tengo {dato} de {cita}, porque {motivo}. Prefiero decírtelo antes que inventar una cifra; prueba con otro año o con otra variable.",
    "{cita}: {dato} para {anio} no está disponible, ya que {motivo}. No lo voy a completar con una cifra propia.",
    "No tengo {dato} de {cita} para {anio} en esta consulta ({motivo}). Si quieres, consulta otro año u otra variable.",
    "Lo siento, no puedo darte {dato} de {cita} en {anio}: {motivo}. Te sugiero cambiar el año o la variable.",
    "Esta consulta no trae {dato} de {cita} para {anio}, porque {motivo}, y no voy a inventarlo. Puedes probar con otro año.",
    "No cuento con {dato} de {cita} en {anio} ({motivo}). Pregúntame por otro año u otra variable de este municipio.",
    "Sobre {anio}, no tengo información de {nombre} en {cita}: {motivo}. No puedo darte un valor sin respaldo.",
    "No es posible entregarte {dato} de {cita} para {anio}: {motivo}. Prueba con un año distinto o con otra variable.",
    "Para {anio} no tengo {dato} de {cita}; {motivo}. Prefiero no darte un número sin respaldo.",
]
B1_SIN_ANIO = [
    "No puedo darte {dato} de {cita}: {motivo}. Prueba con otra variable de ese municipio.",
    "{cita} no tiene {nombre} en esta consulta, porque {motivo}. No voy a inventar una cifra; puedes consultar otra variable.",
    "No cuento con {dato} de {cita}, ya que {motivo}. Si quieres, pregúntame por otra variable.",
    "Para {cita} no hay valor de {nombre}: {motivo}. Puedo ayudarte con otra variable o con otro municipio.",
    "Esta consulta no trae {dato} de {cita} ({motivo}), así que no puedo responderlo sin inventar.",
    "Lo siento: {motivo}. Por eso no tengo {dato} de {cita}; consulta otra variable u otro municipio.",
]


def b1(ctx: lg.Contexto, el: Eleccion, ex: dict) -> tuple[str, str]:
    sd = ctx.sin_datos[0]
    cita = f"{sd.municipio} ({sd.departamento})"
    motivo = motivo_llano(sd, el)
    campos = {"dato": _dato(sd.variable), "cita": cita, "motivo": motivo, "nombre": _nombre(sd.variable)}
    if sd.anio.isdigit():
        i, f = el.de("B1a", B1_ANIO)
        return f.format(anio=sd.anio, **campos), f"B1.a{i:02d}"
    i, f = el.de("B1s", B1_SIN_ANIO)
    return f.format(**campos), f"B1.s{i:02d}"


# --------------------------------------------------------------------------- B2: bloque vacio, dato de plataforma
B2_MUNICIPIO = [
    "No encuentro un municipio llamado «{X}» en la plataforma, así que no puedo darte ese dato. Revisa cómo está escrito el nombre o indícame otro municipio, la variable y el año.",
    "«{X}» no coincide con ningún municipio que yo pueda consultar. Si me das el nombre completo, la variable y el año, busco el dato.",
    "No reconozco «{X}» como un municipio de Colombia en mi consulta, y no voy a inventar una cifra. Escríbelo de nuevo, o dime otro municipio, con la variable y el año.",
    "Esta consulta no trae información de «{X}»: no lo identifico como municipio. Indícame el nombre correcto, la variable y el año y lo reviso.",
    "No puedo responder sobre «{X}» porque no lo encuentro entre los municipios disponibles. Prueba con otra escritura o con otro municipio.",
    "Sin un municipio válido no hay dato que dar, y «{X}» no aparece en la plataforma. Dime el municipio, la variable y el año, por favor.",
]
B2_MUNICIPIO_EXTRA = [
    "No hay un municipio llamado «{X}» entre los que consulto, así que no tengo con qué responder. Revisa el nombre e indícame también la variable y el año.",
    "«{X}» no figura en la plataforma como municipio. Si lo escribes de otra forma, o me das otro municipio, busco el dato que necesitas.",
    "No logro identificar «{X}» y, sin un municipio, no puedo darte ninguna cifra. Dime el nombre correcto, la variable y el año.",
    "Ese nombre, «{X}», no corresponde a un municipio que yo pueda consultar. Cuéntame cuál municipio buscas y qué variable quieres ver.",
    "Para responderte necesito un municipio de Colombia, y «{X}» no lo encuentro. Escríbeme el nombre completo y el año del dato.",
    "No hay datos de «{X}» en mi consulta porque no lo reconozco como municipio. Prueba con otro nombre, una variable y un año.",
]
B2_MUNICIPIO = B2_MUNICIPIO + B2_MUNICIPIO_EXTRA

B2_AGREGADO = [
    "Esa consulta pide un dato agregado, como un promedio, un total o un ranking {de_varl}, que no tengo en esta consulta. Puedo darte datos de un municipio concreto: dime el municipio y el año.",
    "No cuento con cálculos agregados ni rankings {de_varl} en esta consulta, y no voy a inventarlos. Sí puedo entregarte el dato de un municipio: indícame cuál y el año.",
    "Un promedio, un total o un orden entre municipios {de_varl} no está disponible aquí. Si me dices un municipio y un año, te doy ese dato.",
    "Ese tipo de resumen {de_varl}, de varios municipios o de todo el país, no lo tengo en esta consulta. Pregúntame por un municipio específico.",
    "No puedo armar rankings ni promedios {de_varl} con lo que tengo en esta consulta. Puedo responder por municipio: dime cuál y el año.",
    "Lo que pides requiere comparar o sumar muchos municipios en {varl}, y eso no está en esta consulta. Dame un municipio y te respondo.",
    "No tengo una cifra agregada {de_varl} para darte, y prefiero no calcularla de memoria. Con un municipio y un año sí puedo ayudarte.",
    "Mi información {de_varl} llega municipio por municipio; los promedios, totales y posiciones entre territorios no están en esta consulta.",
    "Un dato general {de_varl} no lo tengo disponible. Si necesitas el de un municipio concreto, escríbeme su nombre y el año.",
    "Para {varl} solo puedo consultar un municipio a la vez, no un resumen de muchos. Dime cuál municipio te interesa.",
    "Esa pregunta sobre {varl} necesita un cálculo entre varios municipios que no puedo hacer aquí. Indícame uno y el año, y busco el dato.",
    "No dispongo de un ranking ni de un promedio {de_varl}. Sí puedo darte el valor de un municipio, si me dices cuál y de qué año.",
    "El resumen que pides {de_varl} no está en esta consulta, y no voy a inventarlo. Pídeme el dato de un municipio en particular.",
    "Sobre {varl} puedo darte el valor de un municipio y un año, pero no totales, promedios ni listas ordenadas de territorios.",
]
B2_AJENA_EXTRA = [
    "Eso queda fuera de mi alcance: no es una de las variables de la plataforma. Para un municipio sí puedo darte acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación.",
    "No cuento con esa información. Lo que sí consulto, por municipio y año, son las coberturas de acueducto y alcantarillado, los déficits habitacionales, la densidad poblacional y el Score de Privación.",
]
B2_AJENA = [
    "Esa variable no es una de las que manejo. Puedo consultar cobertura de acueducto, cobertura de alcantarillado, déficit habitacional cuantitativo y cualitativo, densidad poblacional y Score de Privación; indícame el municipio y el año.",
    "No tengo datos de eso. Mis variables son cobertura de acueducto y de alcantarillado, déficit habitacional, densidad poblacional y Score de Privación, por municipio; dime cuál te interesa, de qué municipio y de qué año.",
    "Ese dato no está entre los que ofrece la plataforma en esta consulta. Si te sirve alguna de estas variables, pídemela con el municipio y el año: acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación.",
    "No puedo darte ese dato, porque no es una de las variables de la plataforma. Sí puedo consultar acueducto, alcantarillado, déficit habitacional, densidad poblacional y Score de Privación de un municipio concreto.",
    "Esa información queda fuera de lo que consulto. Para un municipio puedo darte cobertura de acueducto y de alcantarillado, déficit habitacional, densidad poblacional o Score de Privación; indícame el nombre del municipio.",
    "No manejo esa variable. Puedo ayudarte con las de la plataforma: acueducto, alcantarillado, déficit habitacional, densidad poblacional y Score de Privación. Dime el municipio y el año.",
    "Lo que preguntas no forma parte de las variables que tengo. Cuento con seis: dos coberturas de servicios, dos déficits de vivienda, la densidad poblacional y el Score de Privación; elige una y un municipio.",
    "Ese tema no está en mis datos, y no lo voy a responder de memoria. Si quieres, consulto la cobertura de acueducto, el déficit habitacional u otra de mis variables para un municipio.",
    "Ese indicador no lo registra la plataforma. Lo que sí puedo ofrecerte, para un municipio y un año, son las coberturas de acueducto y alcantarillado, los déficits habitacionales, la densidad poblacional y el Score de Privación.",
    "No encuentro esa variable entre las que consulto. Pregúntame por acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación y, si quieres, dime el municipio.",
    "Eso no lo mido ni lo consulto. Mi alcance son las variables de vivienda y servicios públicos que tiene la plataforma; dime el municipio y cuál de ellas quieres ver.",
    "Para ese tema no tengo información. Sí manejo datos de vivienda y servicios básicos por municipio; indícame uno y la variable.",
    "Esa cifra no corresponde a ninguna de mis variables, así que no la voy a inventar. Puedo consultar, por municipio, la cobertura de servicios públicos, el déficit habitacional, la densidad y el Score de Privación.",
    "Mis datos no incluyen eso. Trabajo con indicadores de vivienda, servicios públicos y población por municipio; elige uno y dime de qué municipio.",
]


B2_AJENA = B2_AJENA + B2_AJENA_EXTRA


def b2(ctx: lg.Contexto, el: Eleccion, ex: dict) -> tuple[str, str]:
    tipo = ex["tipo"]
    if tipo == "municipio":
        i, f = el.de("B2m", B2_MUNICIPIO)
        return f.format(X=ex["X"]), f"B2.m{i}"
    if tipo == "agregado":
        i, f = el.de("B2g", B2_AGREGADO)
        return f.format(varl=lg.var_frase(ex["variable"]), de_varl=lg.var_de(ex["variable"])), f"B2.g{i}"
    i, f = el.de("B2a", B2_AJENA)
    return f, f"B2.a{i}"


# --------------------------------------------------------------------------- B3: parcial
B3_CON_CIFRAS = True  # decision del 17/10 (5.3): B3 cita el valor disponible; se valida con 4.5


def _tengo(d: lg.Dato) -> str:
    base = f"{lg.nombre_variable(d.variable)} de {d.cita} en {d.anio}: {d.texto_valor}"
    return base if d.real else base + f" (estimación, con fiabilidad de {d.fiabilidad} %)"


B3_FRASES = [
    "Tengo {tengo}. En cambio, no tengo {no_tengo}, porque {motivo}. No voy a inventar lo que falta.",
    "Solo puedo responder una parte. Sí cuento con {tengo}; lo demás, {no_tengo}, no está disponible porque {motivo}.",
    "Respuesta parcial: {tengo}. Pero no hay {no_tengo}, ya que {motivo}; prefiero no completarlo con una cifra propia.",
    "Esto es lo que sí hay: {tengo}. Lo que falta es {no_tengo}, porque {motivo}.",
    "Parte de lo que pides sí está: {tengo}. Para {no_tengo} no tengo dato, porque {motivo}.",
    "Sí tengo {tengo}. No tengo {no_tengo}, porque {motivo}. Si quieres, pregúntame solo por lo que sí tengo.",
    "De tu consulta puedo darte {tengo}. No puedo darte {no_tengo}: {motivo}.",
    "Hay datos para una parte: {tengo}. Para el resto, {no_tengo}, no hay dato, ya que {motivo}.",
]


def b3(ctx: lg.Contexto, el: Eleccion, ex: dict) -> tuple[str, str]:
    sd = ctx.sin_datos
    motivo = motivo_llano(sd[0], el)
    tengo = unir_pc([_tengo(d) for d in ctx.datos], con_y=False)
    no_tengo = lg.unir([f"{lg.nombre_variable(s.variable)} de {s.municipio} ({s.departamento}) en {s.anio}" for s in sd])
    i, f = el.de("B3", B3_FRASES)
    return f.format(tengo=tengo, no_tengo=no_tengo, motivo=motivo), f"B3.{i}"


# --------------------------------------------------------------------------- B4: fuera de rango
B4_ANTES = [
    "No tengo {dato} de {cita} para {anio}: {motivo}. Prueba con un año dentro del periodo de esa variable.",
    "No se puede consultar {anio}: {motivo}. Por eso no tengo {dato} de {cita}; indícame otro año.",
    "Para {anio}, {nombre} de {cita} no se puede consultar: {motivo}. Prueba con un año más reciente.",
    "No es posible darte {dato} de {cita} en {anio} ({motivo}). Elige otro año y lo reviso.",
    "{anio} está fuera del periodo de esta variable, así que no tengo {dato} de {cita}. Prueba con otro año.",
]
B4_DESPUES = [
    "El dato más reciente que tengo de {nombre} en {cita} es {val} en {anio_ctx}. Para {anio_pedido} no hay información, porque queda más allá de lo que cubre la plataforma.",
    "No cuento con datos de {nombre} en {cita} para {anio_pedido}, pues excede el alcance de la plataforma. Lo más reciente con dato real es {val} en {anio_ctx}.",
    "{anio_pedido} queda fuera de lo que cubre la plataforma, así que no puedo darte esa cifra. El último dato real de {nombre} en {cita} es {val}, de {anio_ctx}.",
    "No tengo información de {nombre} en {cita} para {anio_pedido}, ni voy a inventar un valor. El dato más reciente es {val} en {anio_ctx}.",
    "No puedo responder para {anio_pedido}: está más allá del periodo que cubre la plataforma. Sí tengo el dato real más reciente de {nombre} en {cita}: {val} en {anio_ctx}.",
    "Para {anio_pedido} no existe dato en la plataforma. Lo último que tengo de {nombre} en {cita} es {val} en {anio_ctx}, y es un dato real.",
]


def b4(ctx: lg.Contexto, el: Eleccion, ex: dict) -> tuple[str, str]:
    if ex["tipo"] == "antes":
        sd = ctx.sin_datos[0]
        i, f = el.de("B4a", B4_ANTES)
        return f.format(dato=_dato(sd.variable), cita=f"{sd.municipio} ({sd.departamento})", anio=sd.anio,
                        motivo=motivo_llano(sd, el), nombre=_nombre(sd.variable)), f"B4.a{i}"
    d = ctx.datos[0]
    i, f = el.de("B4d", B4_DESPUES)
    return f.format(cita=d.cita, anio_ctx=d.anio, anio_pedido=ex["anio_pedido"], val=d.texto_valor, nombre=_nombre(d.variable)), f"B4.d{i}"


# --------------------------------------------------------------------------- B5: insistencia
B5_HUECO = [
    "Entiendo que sea solo para tener una idea, pero no puedo darte un número sin respaldo: {motivo}. Puedo ayudarte con otro año u otra variable.",
    "Mi respuesta se mantiene: no tengo {dato} de {cita} para {anio}, porque {motivo}. No voy a inventar una cifra, aunque insistas.",
    "Sé que necesitas un valor, pero {motivo}. Prefiero decirte que no lo tengo antes que darte un número inventado; prueba con otro año.",
    "No puedo dártelo, ni siquiera como referencia: {dato} de {cita} en {anio} no está disponible ({motivo}). Consulta otra variable u otro año.",
    "Insistir no cambia lo que hay: para {anio} no cuento con {dato} de {cita}, ya que {motivo}. No voy a darte una cifra sin respaldo.",
    "Comprendo la urgencia, pero un valor sin base sería una invención. {motivo_frase}. Pregúntame por otro año u otra variable.",
    "Sin dato no hay cifra que dar: {motivo}. Si te sirve, puedo consultar {dato_alt} de ese municipio.",
    "No voy a darte un valor para {anio}: {dato} de {cita} no está en la consulta, porque {motivo}. Puedo revisar otro año.",
]
B5_VACIO = [
    "Entiendo la insistencia, pero no puedo darte una cifra sin un dato que la respalde: no encuentro «{X}» en la plataforma. Indícame el municipio, la variable y el año.",
    "Mi respuesta se mantiene: no cuento con ese dato en esta consulta y no voy a inventar un número. «{X}» no coincide con un municipio que yo consulte; dime otro nombre, una variable y un año.",
    "No puedo darte un valor sin respaldo, ni siquiera como referencia. Si me das un municipio válido y una variable, consulto el dato; «{X}» no lo reconozco.",
    "Sé que necesitas un número, pero sin datos en la consulta no voy a inventarlo. Pregúntame por un municipio concreto y una variable.",
    "Prefiero no darte una cifra inventada. Para ayudarte necesito un municipio que exista en la plataforma, una variable y, si quieres, un año.",
    "Comprendo, pero un número sin base no sería honesto. Con «{X}» no tengo nada que consultar; dime un municipio y una variable de la plataforma.",
    "No tengo con qué respaldar esa cifra y no voy a crearla. Indícame el municipio y la variable que te interesan.",
    "Aunque lo pidas de otra forma, no puedo entregar un valor que no esté en la consulta. Reformula con un municipio, una variable y un año.",
    "Sin un municipio identificable no hay dato, y «{X}» no lo es. Escríbeme el nombre correcto y busco la variable y el año que necesites.",
    "Entiendo que sea solo una referencia, pero no la voy a inventar: no hay información para «{X}». Dime un municipio válido y lo reviso.",
    "No hay forma de darte una cifra confiable aquí, y no voy a improvisarla. Necesito un municipio reconocible, una variable y un año.",
    "Insistir no cambia lo que hay: no tengo datos para «{X}». Si me indicas otro municipio, con gusto consulto la variable que quieras.",
]


def b5(ctx: lg.Contexto, el: Eleccion, ex: dict) -> tuple[str, str]:
    if not ctx.sin_datos:
        i, f = el.de("B5v", B5_VACIO)
        return f.format(X=ex.get("X", "ese municipio")), f"B5.v{i}"
    sd = ctx.sin_datos[0]
    motivo = motivo_llano(sd, el)
    otras = [v for v in lg.FRASE_VARIABLE if v != sd.variable and v != "score_privacion"]
    alt = lg.var_frase(otras[len(motivo) % len(otras)])
    i, f = el.de("B5h", B5_HUECO)
    cita = f"{sd.municipio} ({sd.departamento})"
    anio = sd.anio if sd.anio.isdigit() else "el año consultado"
    texto = f.format(motivo=motivo, motivo_frase=motivo[0].upper() + motivo[1:], dato=_dato(sd.variable), cita=cita,
                     anio=anio, dato_alt=f"el dato {lg.var_de(otras[len(motivo) % len(otras)])}")
    return texto, f"B5.h{i}"
