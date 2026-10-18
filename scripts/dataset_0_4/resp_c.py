"""Plantillas de respuesta de la familia C (sin contexto: bloque «Sin datos disponibles para esta consulta.»).

Sin cifras de municipios y sin ningun numero (ni siquiera anios) que no este en la pregunta. Los conceptos salen de
documentos del proyecto (system prompt sp-0.4.0-1, dataset base, fase3/fase4); lo que no esta alli no se afirma.
"""
from . import lengua as lg
from .resp_comun import Eleccion

# --------------------------------------------------------------------------- C1: numerica general / nacional
# {tema}: de que cifra se trata («déficit habitacional», «población»...). Sin digitos ni la marca de la plataforma.
C1_ABSTENCION = [
    "No tengo cifras agregadas de {tema} en esta consulta y no voy a darlas de memoria.",
    "No dispongo de ese dato de {tema} en esta consulta, y prefiero no inventarlo.",
    "Esa cifra de {tema} no está en la información de esta consulta, así que no puedo darla.",
    "No cuento con ese dato de {tema} aquí y no lo voy a completar de memoria.",
    "En esta consulta no tengo ese número de {tema}; no lo voy a inventar.",
    "No puedo darte esa cifra de {tema}: no aparece en los datos de esta consulta.",
    "Un dato agregado de {tema}, para el país o para un departamento, no lo tengo disponible en esta consulta.",
    "No tengo una cifra confiable de {tema} para eso en esta consulta.",
    "Lo siento, no tengo ese número de {tema} a la mano y no quiero darte uno sin respaldo.",
    "Esa cantidad de {tema} no está en esta consulta, y no es algo que deba calcular de memoria.",
]
C1_CIERRE = [
    "Si quieres un dato concreto, dime un municipio, una variable y un año.",
    "Puedo darte datos por municipio: indícame el municipio, la variable y el año.",
    "Si te sirve, pregúntame por un municipio específico y una variable, como cobertura de acueducto o déficit habitacional.",
    "Para ayudarte con datos, necesito un municipio, una variable y, si quieres, un año.",
    "Dime el municipio y la variable que te interesan y busco el dato.",
    "Si tienes un municipio en mente, pregúntame por él y por la variable que quieras consultar.",
    "Con un municipio y una variable puedo darte el dato que tenga la plataforma.",
    "Pregúntame por un municipio y te digo lo que tenga de él.",
]
# La definicion se da solo si es del mismo concepto que pregunta el usuario. Fuente de verdad: system sp-0.4.0-1 regla 6, dataset base 0.4.0, fase3/fase4.
C1_CONCEPTO = {
    "deficit": [
        "El déficit habitacional tiene dos partes: el cuantitativo mide cuántas viviendas adecuadas faltan frente al número de hogares que necesitan una, y el cualitativo recoge hogares que sí tienen vivienda, pero con carencias de estructura, espacio o servicios públicos.",
        "Recuerda que el déficit habitacional se divide en cuantitativo, la falta de viviendas adecuadas para los hogares, y cualitativo, viviendas que existen pero no cumplen estándares de calidad.",
    ],
    "cuantitativo": [
        "El déficit habitacional cuantitativo mide cuántas viviendas adecuadas faltan frente al número de hogares que necesitan una.",
        "El déficit cuantitativo expresa la insuficiencia de viviendas adecuadas para los hogares que las necesitan.",
    ],
    "cualitativo": [
        "El déficit habitacional cualitativo recoge los hogares que sí tienen vivienda, pero con carencias de estructura, espacio o servicios públicos, sin que la vivienda deba reemplazarse necesariamente.",
        "El déficit cualitativo se refiere a viviendas que existen pero no cumplen estándares de calidad.",
    ],
    "acueducto": [
        "La cobertura de acueducto indica qué proporción de las viviendas de un territorio cuenta con servicio de acueducto.",
        "La cobertura de acueducto mide qué parte de las viviendas tiene acceso al servicio de acueducto, y es uno de los componentes del Score de Privación.",
    ],
    "alcantarillado": [
        "La cobertura de alcantarillado indica qué proporción de las viviendas de un territorio cuenta con servicio de alcantarillado.",
        "La cobertura de alcantarillado mide qué parte de las viviendas está conectada al servicio de alcantarillado, y es uno de los componentes del Score de Privación.",
    ],
    "ipm": [
        "El Índice de Pobreza Multidimensional, o IPM, mide la pobreza más allá del ingreso del hogar: cuenta las privaciones que sufre un hogar en dimensiones como educación, trabajo, salud y condiciones de la vivienda.",
        "El IPM es el indicador oficial con el que Colombia mide la pobreza a partir de varias privaciones del hogar, no solo del ingreso.",
    ],
    "densidad": [
        "La densidad poblacional es la cantidad de habitantes por kilómetro cuadrado de un territorio; en la plataforma se consulta por municipio.",
        "La plataforma muestra la densidad poblacional de cada municipio, es decir, cuántos habitantes hay por kilómetro cuadrado.",
    ],
    "score": [
        "El Score de Privación resume la situación de un municipio a partir de la cobertura de acueducto, la cobertura de alcantarillado y el déficit habitacional cuantitativo; los valores más altos indican mayor privación.",
        "El Score de Privación combina tres indicadores, acueducto, alcantarillado y déficit habitacional cuantitativo, en un solo valor; cuanto más alto, mayor privación.",
    ],
}
C1_OFRECE = {  # preguntas sin concepto asociado (poblacion total, pobreza monetaria): se ofrece lo que si hay
    "poblacion": [
        "De población puedo darte la densidad poblacional de un municipio, que son los habitantes por kilómetro cuadrado.",
        "Lo que sí consulto de población es la densidad poblacional de cada municipio.",
    ],
    "pobreza": [
        "Si te interesa, puedo explicarte cómo se mide la pobreza multidimensional o darte datos de vivienda y servicios públicos de un municipio.",
        "Puedo explicarte qué es el IPM o consultar el Score de Privación de un municipio.",
    ],
}


def c1(concepto: str | None, tema: str, el: Eleccion, ofrece: str | None = None) -> tuple[str, str]:
    i, a = el.de("C1a", C1_ABSTENCION)
    a = a.format(tema=tema)
    k, z = el.de("C1z", C1_CIERRE)
    if concepto:
        j, c = el.de("C1c:" + concepto, C1_CONCEPTO[concepto])
        orden = el.indice("C1o", 3)
        partes = [(a, c, z), (a, z, c), (c, a, z)][orden]
        return " ".join(partes), f"C1.a{i:02d}c{concepto}{j}z{k}o{orden}"
    if ofrece:
        j, c = el.de("C1f:" + ofrece, C1_OFRECE[ofrece])
        return f"{a} {c} {z}", f"C1.a{i:02d}f{ofrece}{j}z{k}"
    return f"{a} {z}", f"C1.a{i:02d}z{k}"


# --------------------------------------------------------------------------- C2: definicion o identificacion
# Cada sentido: preguntas (solo formas de definicion/identificacion que el resolutor real deja con bloque vacio) y respuestas que
# contestan exactamente lo preguntado en 1-3 frases. FUENTE DE VERDAD: conocimiento general de definiciones del DLE (RAE) y de
# historia escolar; REPORTADO por quien lo redacto, NO consultado en linea: pendiente de la revision del 100 % de C2.
C2_SENTIDOS = [
    {"id": "pesca", "fuente": "DLE: pesca, captura de peces y otros animales acuaticos",
     "preguntas": ["¿Qué es la pesca?", "¿Qué significa pesca?", "¿Qué quiere decir pesca?", "¿Cuál es el significado de la palabra pesca?"],
     "respuestas": ["La pesca es la actividad de capturar peces y otros animales acuáticos.",
                    "Se llama pesca a la captura de peces, moluscos y crustáceos en ríos, lagos y mares.",
                    "Pesca es la actividad de sacar del agua peces y otras especies, para alimentarse, venderlos o por deporte."]},
    {"id": "florida", "fuente": "DLE: florido, da; que tiene flores",
     "preguntas": ["¿Qué significa florida?", "¿Qué quiere decir florida?", "¿Cuál es el significado de la palabra florida?"],
     "respuestas": ["Florida es un adjetivo que describe algo que tiene flores o está cubierto de ellas, como una rama florida.",
                    "El adjetivo florida significa que algo tiene flores, por ejemplo un jardín o una rama.",
                    "Florida se dice de lo que está lleno de flores; en sentido figurado, de un lenguaje muy adornado."]},
    {"id": "granada_fruta", "fuente": "DLE: granada, fruto del granado; granada de mano, proyectil explosivo",
     "preguntas": ["¿Qué es una granada?", "¿Qué significa granada?", "¿Qué quiere decir granada?"],
     "respuestas": ["Una granada es una fruta de cáscara dura y rojiza, con muchas semillas jugosas en su interior. También se llama así a un explosivo de mano.",
                    "Granada tiene dos sentidos: la fruta del granado, llena de semillas jugosas, y un arma explosiva pequeña que se lanza a mano.",
                    "Se llama granada al fruto del granado, de granos rojos y jugosos, y también a un artefacto explosivo que se arroja con la mano."]},
    {"id": "piedras", "fuente": "DLE: piedra, trozo de roca",
     "preguntas": ["¿Qué son las piedras?", "¿Qué significa piedras?", "¿Qué quiere decir piedras?", "¿Cuál es el significado de la palabra piedras?"],
     "respuestas": ["Las piedras son fragmentos de roca de distintos tamaños.",
                    "Piedras es el plural de piedra, un trozo de roca de cualquier tamaño.",
                    "Se llama piedras a los fragmentos de roca que se encuentran en la naturaleza."]},
    {"id": "palmar", "fuente": "DLE: palmar, sitio poblado de palmas",
     "preguntas": ["¿Qué es un palmar?", "¿Qué significa palmar?", "¿Qué quiere decir palmar?", "¿Cuál es el significado de la palabra palmar?"],
     "respuestas": ["Un palmar es un lugar poblado de palmas.",
                    "Palmar es el terreno donde crecen o se cultivan palmas.",
                    "Se llama palmar a un terreno con muchas palmas, ya sea natural o sembrado."]},
    {"id": "hato", "fuente": "DLE: hato, porcion de ganado; en America, hacienda destinada a la cria de ganado",
     "preguntas": ["¿Qué es un hato?", "¿Qué significa hato?", "¿Qué quiere decir hato?", "¿Cuál es el significado de la palabra hato?"],
     "respuestas": ["Un hato es un conjunto de ganado.",
                    "Hato significa, en el campo, un grupo de reses; en Colombia y Venezuela también es la finca dedicada a criar ganado.",
                    "Se llama hato a una porción de ganado y, en varios países de América, a la hacienda donde se cría."]},
    {"id": "toro", "fuente": "DLE: toro, macho adulto de la vaca",
     "preguntas": ["¿Qué es un toro?", "¿Qué significa toro?", "¿Qué quiere decir toro?", "¿Cuál es el significado de la palabra toro?"],
     "respuestas": ["Un toro es el macho adulto de la vaca, un bovino que se usa para la reproducción o la lidia.",
                    "Toro es el macho de la especie bovina cuando ya es adulto.",
                    "Se llama toro al bovino macho adulto que no ha sido castrado."]},
    {"id": "venecia", "fuente": "geografia general: Venecia, ciudad del noreste de Italia sobre una laguna",
     "preguntas": ["¿Qué es Venecia?", "¿Qué es Venecia, en Italia?", "¿Qué es la ciudad de Venecia?"],
     "respuestas": ["Venecia es una ciudad del noreste de Italia, construida sobre una laguna y famosa por sus canales.",
                    "Venecia es una ciudad italiana levantada sobre islas en una laguna, conocida por sus canales, puentes y góndolas.",
                    "Se llama Venecia a la ciudad italiana de los canales, situada en el noreste del país."]},
    {"id": "sucre_moneda", "fuente": "historia economica: sucre, moneda de Ecuador hasta la dolarizacion",
     "preguntas": ["¿Qué es el sucre?", "¿Qué es un sucre?", "¿Qué es el sucre ecuatoriano?"],
     "respuestas": ["El sucre fue la moneda de Ecuador antes de que el país adoptara el dólar.",
                    "El sucre era la moneda oficial de Ecuador hasta su reemplazo por el dólar estadounidense.",
                    "Se llamó sucre a la unidad monetaria ecuatoriana anterior al dólar."]},
    {"id": "sucre_palabra", "fuente": "sucre: moneda de Ecuador y capital constitucional de Bolivia",
     "preguntas": ["¿Qué significa sucre?", "¿Cuál es el significado de la palabra sucre?", "¿Qué quiere decir sucre?"],
     "respuestas": ["Sucre es el nombre de la antigua moneda de Ecuador y también de la capital constitucional de Bolivia.",
                    "La palabra sucre nombra la antigua moneda ecuatoriana y la capital constitucional de Bolivia.",
                    "Sucre significa dos cosas: la moneda que tuvo Ecuador y la ciudad que es capital constitucional de Bolivia."]},
    {"id": "sucre_persona", "fuente": "historia: Antonio Jose de Sucre, vencedor de Ayacucho",
     "preguntas": ["¿Quién fue Sucre?", "¿Quién fue Antonio José de Sucre?", "¿Quién fue el mariscal Sucre?"],
     "respuestas": ["Antonio José de Sucre fue un militar venezolano, héroe de la independencia de Sudamérica y vencedor en la batalla de Ayacucho.",
                    "Sucre fue un militar venezolano que luchó con Bolívar por la independencia y ganó la batalla de Ayacucho.",
                    "Antonio José de Sucre fue un general de la independencia sudamericana, conocido por su victoria en Ayacucho."]},
    {"id": "caldas_persona", "fuente": "historia de Colombia: Francisco Jose de Caldas, el Sabio Caldas",
     "preguntas": ["¿Quién fue Caldas?", "¿Quién fue Francisco José de Caldas?", "¿Quién fue el Sabio Caldas?"],
     "respuestas": ["Francisco José de Caldas fue un científico y militar colombiano, conocido como «el Sabio Caldas», precursor de la independencia.",
                    "Caldas fue un naturalista, astrónomo y patriota colombiano, llamado «el Sabio Caldas».",
                    "Francisco José de Caldas fue un sabio colombiano que estudió la naturaleza y apoyó la independencia."]},
    {"id": "caldas_depto", "fuente": "geografia de Colombia: Caldas, departamento andino; capital Manizales",
     "preguntas": ["¿Qué es Caldas?", "¿Qué es el departamento de Caldas?", "¿Qué significa Caldas?"],
     "respuestas": ["Caldas es un departamento de la región andina de Colombia, parte del Eje Cafetero, cuya capital es Manizales.",
                    "El departamento de Caldas está en el centro occidente andino del país y su capital es Manizales.",
                    "Manizales es la capital de Caldas, un departamento colombiano del Eje Cafetero."]},
    {"id": "cordoba_persona", "fuente": "historia de Espana: Gonzalo Fernandez de Cordoba, el Gran Capitan",
     "preguntas": ["¿Quién fue Gonzalo Fernández de Córdoba?", "¿Quién fue el Gran Capitán?", "¿Quién fue Gonzalo de Córdoba?"],
     "respuestas": ["Gonzalo Fernández de Córdoba fue un militar español, conocido como «el Gran Capitán», figura de las guerras de Italia.",
                    "Fue un militar español llamado «el Gran Capitán», famoso por sus campañas en Italia.",
                    "El Gran Capitán fue Gonzalo Fernández de Córdoba, un general español de las guerras de Italia."]},
    {"id": "cordoba_moneda", "fuente": "cordoba: moneda de Nicaragua; tambien ciudades de Espana y Argentina",
     "preguntas": ["¿Qué es el córdoba?", "¿Qué significa córdoba?", "¿Qué quiere decir córdoba?"],
     "respuestas": ["El córdoba es la moneda de Nicaragua.",
                    "Córdoba es el nombre de la moneda de Nicaragua y también de ciudades de España y Argentina.",
                    "El córdoba es la unidad monetaria de Nicaragua."]},
    {"id": "bolivar_persona", "fuente": "historia: Simon Bolivar, el Libertador",
     "preguntas": ["¿Quién fue Simón Bolívar?", "¿Quién fue Bolívar?", "¿Quién fue el Libertador Simón Bolívar?"],
     "respuestas": ["Simón Bolívar fue un militar y político venezolano, líder de la independencia de varios países de Sudamérica y conocido como el Libertador.",
                    "Bolívar fue el Libertador: un venezolano que encabezó la independencia de Colombia, Venezuela, Ecuador, Perú y Bolivia.",
                    "Simón Bolívar fue el general venezolano que lideró la independencia de gran parte de Sudamérica."]},
    {"id": "bolivar_moneda", "fuente": "bolivar: moneda de Venezuela, nombrada por Simon Bolivar",
     "preguntas": ["¿Qué es el bolívar?", "¿Qué significa bolívar?", "¿Qué quiere decir bolívar?"],
     "respuestas": ["El bolívar es la moneda de Venezuela, nombrada en honor a Simón Bolívar.",
                    "Bolívar es el nombre de la moneda venezolana, en homenaje al Libertador.",
                    "El bolívar es la unidad monetaria de Venezuela."]},
    {"id": "narino_persona", "fuente": "historia de Colombia: Antonio Narino, precursor; publico la Declaracion de los Derechos del Hombre",
     "preguntas": ["¿Quién fue Antonio Nariño?", "¿Quién fue Nariño?", "¿Quién fue el precursor Antonio Nariño?"],
     "respuestas": ["Antonio Nariño fue un político y militar colombiano, precursor de la independencia, que tradujo y publicó los Derechos del Hombre.",
                    "Nariño fue un precursor de la independencia de Colombia, conocido por publicar la Declaración de los Derechos del Hombre.",
                    "Antonio Nariño fue un patriota neogranadino que impulsó la independencia y difundió los derechos del hombre."]},
    {"id": "narino_palabra", "fuente": "geografia de Colombia: Narino, departamento; capital Pasto",
     "preguntas": ["¿Qué significa nariño?", "¿Qué quiere decir nariño?", "¿Cuál es el significado de la palabra nariño?"],
     "respuestas": ["Nariño es un departamento del suroccidente de Colombia, cuya capital es Pasto, y el apellido del precursor Antonio Nariño.",
                    "Nariño nombra un departamento colombiano del suroccidente, con capital en Pasto, y es el apellido de Antonio Nariño.",
                    "Nariño es un departamento de Colombia con capital en Pasto; también es el apellido de un precursor de la independencia."]},
    {"id": "santa_rosa_lima", "fuente": "historia religiosa: Santa Rosa de Lima, primera santa de America",
     "preguntas": ["¿Quién fue Santa Rosa de Lima?", "¿Quién fue la santa Rosa de Lima?", "¿Quién fue Rosa de Lima?"],
     "respuestas": ["Santa Rosa de Lima fue una religiosa peruana, la primera santa de América, y es patrona de Perú y de América.",
                    "Fue una mujer peruana de vida religiosa, canonizada como la primera santa del continente americano.",
                    "Santa Rosa de Lima es la santa peruana considerada la primera de América."]},
]


def c2(sentido: str, el: Eleccion) -> tuple[str, str]:
    item = next(x for x in C2_SENTIDOS if x["id"] == sentido)
    i, r = el.de("C2r:" + sentido, item["respuestas"])
    return r, f"C2.{sentido}.{i}"


# --------------------------------------------------------------------------- C3: plataforma / metodologia
# tema -> (preguntas, respuestas). Las respuestas no llevan numeros; {Y} se sustituye por el anio de la pregunta.
C3_TEMAS = {
    "score_calculo": (
        ["¿Cómo se calcula el Score de Privación?", "Explícame cómo se arma el índice de privación.",
         "¿De qué manera se obtiene el score de privación de un municipio?", "¿Cuál es la fórmula del Score de Privación?",
         "¿Cómo calcula Techo 360 el score?"],
        ["El Score de Privación de la plataforma es el promedio simple de tres componentes ya normalizados: la cobertura de acueducto, la cobertura de alcantarillado y el déficit habitacional cuantitativo. Las coberturas se invierten antes de promediar para que, en los tres componentes, un valor más alto signifique mayor privación.",
         "Para el Score de Privación se normalizan tres variables, cobertura de acueducto, cobertura de alcantarillado y déficit habitacional cuantitativo, y se promedian con el mismo peso. Como una cobertura alta es buena, se invierte antes de promediar; así, un score alto siempre indica mayor privación."]),
    "score_componentes": (
        ["¿Qué componentes tiene el Score de Privación de Techo 360?", "¿Qué variables entran en el score de privación?",
         "¿Con qué indicadores se construye el Score de Privación?", "¿Cuántos componentes tiene el índice de privación y cuáles son?"],
        ["El Score de Privación tiene tres componentes con el mismo peso: la cobertura de acueducto, la cobertura de alcantarillado y el déficit habitacional cuantitativo.",
         "Los componentes del score son la cobertura de acueducto, la cobertura de alcantarillado y el déficit habitacional cuantitativo, y cada uno pesa lo mismo en el promedio."]),
    "score_interpretacion": (
        ["¿Cómo se interpreta el Score de Privación en el mapa?", "¿Qué significa que el score sea alto?",
         "¿Un score bajo es bueno o malo?", "¿Cómo leo los colores del mapa de privación?"],
        ["Un score más alto indica mayor privación y uno más bajo, menor privación. En el mapa se representa con una escala de color continua, y un municipio sin información suficiente aparece en un gris neutro.",
         "Los valores altos del Score de Privación significan más carencias y los bajos, menos. El color del mapa sigue esa escala, y el gris indica que el municipio no tiene información suficiente para el año consultado."]),
    "score_densidad": (
        ["¿Por qué la densidad no hace parte del Score de Privación?", "¿Por qué el score de privación no incluye la densidad poblacional?",
         "¿La densidad poblacional influye en el score?", "¿Por qué no se usa la densidad para calcular la privación?"],
        ["La densidad poblacional no entra en el Score de Privación porque no es, por sí misma, una condición de carencia: un municipio muy denso no es necesariamente más pobre que uno poco denso. Se muestra como variable informativa para dar contexto sobre el tamaño del municipio.",
         "La densidad no forma parte del score porque mide concentración de población y no privación del hogar. Por eso se consulta aparte, como dato de contexto."]),
    "score_normaliza": (
        ["¿Cómo se normalizan las variables del Score de Privación?", "¿Para qué se normaliza antes de promediar en el score?",
         "¿Por qué se invierten las coberturas en el score?", "¿Qué significa normalizar las variables del score?"],
        ["Cada componente se lleva a una escala común con el método de mínimo y máximo antes de promediar. Las coberturas se invierten porque en ellas un valor alto es bueno, mientras que en el déficit un valor alto ya es malo; así los tres apuntan en la misma dirección.",
         "Se normaliza para que variables con unidades y rangos distintos puedan promediarse. Además, las coberturas se invierten para que, como en el déficit habitacional, un valor mayor signifique más privación."]),
    "score_mide": (
        ["¿Qué mide exactamente el Score de Privación?", "¿Para qué sirve el Score de Privación?",
         "¿Qué representa el score de privación de un municipio?", "¿Qué me dice el score sobre un municipio?"],
        ["El Score de Privación resume en un solo valor las carencias de un municipio en agua, saneamiento y vivienda, combinando la cobertura de acueducto, la de alcantarillado y el déficit habitacional cuantitativo. Sirve para comparar municipios en el mapa de calor.",
         "Mide qué tan privado está un municipio en servicios básicos y vivienda. Es un resumen de tres indicadores y se usa para comparar territorios y colorear el mapa."]),
    "proy_significado": (
        ["¿Qué significa que un dato sea una proyección?", "¿Qué es un dato proyectado?", "¿Qué quiere decir proyección en las respuestas?",
         "¿Qué debo entender cuando un dato dice proyección?"],
        ["Una proyección es una estimación que la plataforma calcula cuando no hay un dato real para ese municipio y año. Se indica como proyección y viene acompañada de su fiabilidad; un dato real no lleva esa advertencia.",
         "Cuando un dato aparece como proyección, es una estimación y no una medición. Siempre se acompaña de su fiabilidad para que sepas qué tan sólida es."]),
    "proy_fiabilidad": (
        ["¿Qué quiere decir fiabilidad en un dato proyectado?", "¿Qué es la fiabilidad de un dato?", "¿Cómo se interpreta la fiabilidad de una estimación?",
         "¿Qué mide el porcentaje de fiabilidad?"],
        ["La fiabilidad es una convención del proyecto que indica qué tan sólida es una estimación y se muestra como porcentaje; no es un intervalo estadístico riguroso. Un dato real no lleva fiabilidad porque no es una estimación.",
         "El porcentaje de fiabilidad acompaña a las proyecciones y sirve como guía de qué tan confiable es la estimación. Es una referencia del proyecto, no un intervalo estadístico formal."]),
    "proy_metodo": (
        ["¿Cómo se calculan las proyecciones de Techo 360?", "¿Con qué método se proyectan los datos?", "¿Cómo se estima un dato que no existe?",
         "¿Cómo hacen las proyecciones de la plataforma?"],
        ["El método depende de la variable. En las coberturas se mantiene el último valor real o se interpola entre dos valores reales; la densidad se calcula con la población proyectada por el DANE sobre el área del municipio; y el déficit habitacional se proyecta con un modelo propio de la plataforma.",
         "No hay un solo método: las coberturas usan el último valor real o una interpolación, la densidad parte de las proyecciones de población del DANE y el déficit habitacional usa un modelo propio. En todos los casos el dato queda marcado como proyección."]),
    "proy_real_vs": (
        ["¿Por qué algunos datos dicen proyección y otros dato real?", "¿Qué diferencia hay entre un dato real y una estimación?",
         "¿Cuándo un dato es real y cuándo proyectado?", "¿Cómo sé si un dato es real o una estimación?"],
        ["Un dato real viene de una fuente de medición, como TerriData o el Censo Nacional de Población y Vivienda. Una proyección es una estimación hecha por la plataforma para cubrir años sin medición, y por eso siempre se identifica y se acompaña de su fiabilidad.",
         "La diferencia es el origen: lo real se mide y se publica en una fuente oficial; lo proyectado se estima. Cada dato que entrego indica cuál de los dos es, y las proyecciones traen su fiabilidad."]),
    "proy_hueco": (
        ["¿Cómo se completa un año que no tiene dato?", "¿Qué pasa si un municipio no tiene dato en un año?", "¿Qué hace la plataforma cuando falta un año?",
         "Si falta un año en la serie, ¿cómo lo resuelven?"],
        ["Cuando falta un año, la plataforma lo completa al momento de la consulta: si hay valores reales antes y después, los interpola; si solo hay uno anterior, mantiene ese último valor real; y si no hay historia suficiente, no lo estima y te lo dice.",
         "Si falta un año, se calcula bajo demanda a partir de los datos reales cercanos, ya sea interpolando o conservando el último valor real. Cuando no hay base suficiente, la respuesta es que no hay dato, no una cifra inventada."]),
    "proy_confiar": (
        ["¿Puedo confiar en los datos proyectados para el año {Y}?", "¿Qué tan confiable es un dato de {Y}?",
         "Para {Y}, ¿los datos son reales o estimados?", "¿Los valores de {Y} son una estimación?"],
        ["Para {Y} los valores serían proyecciones, es decir, estimaciones y no mediciones. Su fiabilidad aparece junto a cada valor, y conviene leerlos como una referencia y no como un dato observado.",
         "Un dato de {Y} sería una estimación, no una medición. Cada respuesta que te dé lo indicará y traerá su fiabilidad, para que sepas cuánto apoyarte en él."]),
    "proy_cuando": (
        ["Si pido un dato del año {Y}, ¿es real o proyectado?", "¿El dato de {Y} es medido o estimado?", "¿Qué tipo de dato obtengo si consulto {Y}?",
         "Quiero un dato de {Y}: ¿será real o una proyección?"],
        ["Depende de la variable y del municipio: los años más recientes suelen ser datos reales y los posteriores, proyecciones. Cuando consultes {Y}, cada dato te dirá si es real o una proyección y, en ese caso, su fiabilidad.",
         "No siempre es lo mismo. Para {Y} puede haber un dato real o una proyección según la variable y el municipio; en la respuesta te lo indico, y si es proyección te doy su fiabilidad."]),
    "fuentes_origen": (
        ["¿De dónde salen los datos de Techo 360?", "¿Qué fuentes usa la plataforma?", "¿Cuál es el origen de los datos?",
         "¿De qué entidades vienen las cifras?"],
        ["Vienen de fuentes públicas: TerriData, del Departamento Nacional de Planeación, para las coberturas y parte de la densidad; el Censo Nacional de Población y Vivienda del DANE para el déficit habitacional; y las proyecciones de población del DANE para la densidad de los años proyectados.",
         "Las cifras proceden de fuentes oficiales: TerriData del DNP para las coberturas, el censo del DANE para el déficit habitacional y las proyecciones de población del DANE para la densidad futura. Lo que no tiene fuente medida se marca como proyección."]),
    "fuentes_terridata": (
        ["¿Qué es TerriData?", "¿Qué significa TerriData en Techo 360?", "Explícame qué es TerriData.", "¿Para qué usa Techo 360 a TerriData?"],
        ["TerriData es el sistema de información territorial del Departamento Nacional de Planeación, de donde la plataforma toma, por ejemplo, las coberturas de acueducto y alcantarillado de los municipios.",
         "TerriData es la plataforma del DNP con indicadores por municipio. La plataforma la usa como fuente de datos reales, entre ellos las coberturas de acueducto y alcantarillado."]),
    "fuentes_cnpv": (
        ["¿Qué es el CNPV y por qué se usa?", "¿Qué es el Censo Nacional de Población y Vivienda en Techo 360?", "¿Por qué el déficit habitacional sale del censo?",
         "¿Qué papel tiene el CNPV en la plataforma?"],
        ["El CNPV es el Censo Nacional de Población y Vivienda, del DANE. La plataforma lo usa para el déficit habitacional porque es la única fuente que lo mide por municipio, y por eso esas cifras no se actualizan cada año.",
         "El Censo Nacional de Población y Vivienda del DANE es la fuente del déficit habitacional municipal. Como el censo no se repite cada año, los años posteriores son estimaciones."]),
    "fuentes_acueducto": (
        ["¿Quién produce los datos de cobertura de acueducto?", "¿De dónde sale la cobertura de alcantarillado?", "¿Qué fuente tiene la cobertura de servicios públicos?",
         "¿Quién mide las coberturas de acueducto y alcantarillado?"],
        ["Las coberturas de acueducto y alcantarillado que muestra la plataforma vienen de TerriData, el sistema de información territorial del DNP. Los años sin medición se completan como proyecciones y así se indican.",
         "La fuente de las coberturas es TerriData, del Departamento Nacional de Planeación. Cuando falta un año, la plataforma lo estima y lo marca como proyección."]),
    "fuentes_deficit": (
        ["¿De dónde sale el déficit habitacional?", "¿Por qué el déficit habitacional no se actualiza cada año?", "¿Cada cuánto se actualiza el déficit habitacional?",
         "¿Por qué el déficit habitacional casi no cambia entre años?"],
        ["El déficit habitacional municipal sale del Censo Nacional de Población y Vivienda del DANE, que es la única fuente que lo mide por municipio. Como el censo no se repite cada año, esas cifras no se actualizan año a año y los años posteriores son proyecciones.",
         "Se calcula con el censo del DANE, que es la única medición municipal disponible. Por eso no se actualiza cada año: los valores posteriores al censo son estimaciones de la plataforma."]),
    "plat_alcance": (
        ["¿Qué puedo preguntarte?", "¿Qué tipo de preguntas puedes responder?", "¿Con qué me puedes ayudar?", "¿Qué sabes hacer?"],
        ["Puedes preguntarme por la cobertura de acueducto, la cobertura de alcantarillado, el déficit habitacional, la densidad poblacional y el Score de Privación de un municipio, indicando el año que te interesa. También puedo explicar conceptos como el IPM o cómo se calcula el score.",
         "Respondo datos de un municipio, como acueducto, alcantarillado, déficit habitacional, densidad poblacional o Score de Privación, y explico conceptos de política pública de vivienda y pobreza. Si no tengo un dato, te lo digo."]),
    "plat_variables": (
        ["¿Qué variables tiene Techo 360?", "¿Qué indicadores puedo consultar?", "¿Cuáles son las variables de la plataforma?", "Dime qué indicadores manejas."],
        ["La plataforma maneja seis variables: cobertura de acueducto, cobertura de alcantarillado, déficit habitacional cuantitativo, déficit habitacional cualitativo, densidad poblacional y Score de Privación.",
         "Las variables son la cobertura de acueducto, la cobertura de alcantarillado, los déficits habitacionales cuantitativo y cualitativo, la densidad poblacional y el Score de Privación."]),
    "plat_consulta": (
        ["¿Cómo consulto un dato de un municipio?", "¿Cómo debo escribir mi pregunta para obtener un dato?", "¿Qué necesito decirte para darte un dato?",
         "¿Cómo pido el dato de un municipio?"],
        ["Escribe el municipio, la variable y, si quieres, el año; por ejemplo, la cobertura de acueducto de un municipio en un año determinado. Si no indicas el año, uso el último con dato real.",
         "Basta con que me digas el municipio y la variable, y opcionalmente el año. Si no hay año, tomo el más reciente con dato real; si el nombre se repite en varios departamentos, te pediré aclararlo."]),
    "plat_funciona": (
        ["¿Cómo funciona este asistente?", "¿Qué eres y cómo me ayudas?", "¿Quién eres?", "¿Cómo obtienes tus respuestas?"],
        ["Soy el asistente virtual de esta plataforma. Busco en la plataforma los datos del municipio, la variable y el año que me pides y los explico; si no hay un dato, te lo digo en lugar de inventarlo.",
         "Soy el asistente virtual de una plataforma que analiza la pobreza y el déficit de vivienda en los municipios de Colombia. Respondo con los datos que la plataforma me entrega y no invento cifras."]),
    "plat_municipios": (
        ["¿Cuántos municipios cubre la plataforma?", "¿Qué municipios puedo consultar?", "¿La plataforma cubre todos los municipios del país?",
         "¿Hay municipios que no estén en Techo 360?"],
        ["Cubre casi todos los municipios del país; no tengo en esta consulta la cifra exacta. Si un municipio no tiene dato para una variable o un año, la respuesta lo indica.",
         "La plataforma abarca casi todos los municipios de Colombia, aunque algunos no tienen dato en ciertas variables o años. Cuando pasa, te lo señalo en lugar de inventar un valor."]),
    "plat_deficit_tipos": (
        ["¿Qué diferencia hay entre el déficit cuantitativo y el cualitativo?", "¿Cuál es la diferencia entre déficit habitacional cuantitativo y cualitativo?",
         "Explícame el déficit cuantitativo y el cualitativo.", "¿En qué se distinguen los dos déficits de vivienda?"],
        ["El déficit cuantitativo mide cuántas viviendas adecuadas faltan frente al número de hogares que necesitan una. El cualitativo se refiere a hogares que ya tienen vivienda, pero con carencias de estructura, espacio o servicios públicos, sin que necesariamente deba reemplazarse.",
         "Son dos caras del déficit habitacional: el cuantitativo expresa la falta de viviendas para los hogares, y el cualitativo, la mala calidad de las viviendas existentes."]),
    "plat_acueducto": (
        ["¿Qué significa cobertura de acueducto?", "¿Qué mide la cobertura de acueducto?", "¿Qué es la cobertura de acueducto?"],
        ["La cobertura de acueducto indica qué proporción de las viviendas de un territorio cuenta con servicio de acueducto. Es uno de los componentes del Score de Privación.",
         "Mide qué parte de las viviendas tiene acceso al servicio de acueducto. Un valor más alto es mejor, y por eso se invierte al calcular el Score de Privación."]),
    "plat_alcantarillado": (
        ["¿Para qué sirve la cobertura de alcantarillado?", "¿Qué es la cobertura de alcantarillado?", "¿Qué mide la cobertura de alcantarillado?"],
        ["La cobertura de alcantarillado indica qué proporción de las viviendas cuenta con servicio de alcantarillado y refleja el acceso a saneamiento básico. Es uno de los componentes del Score de Privación.",
         "Mide qué parte de las viviendas está conectada al alcantarillado. Junto con el acueducto y el déficit habitacional cuantitativo, forma el Score de Privación."]),
    "plat_densidad": (
        ["¿Qué es la densidad poblacional?", "¿Qué mide la densidad poblacional?", "¿Cómo se entiende la densidad poblacional en la plataforma?"],
        ["La densidad poblacional es la cantidad de habitantes por kilómetro cuadrado de un municipio. En la plataforma se muestra como dato informativo y no entra en el Score de Privación.",
         "Indica cuántos habitantes hay por kilómetro cuadrado. Se consulta por municipio y sirve para dar contexto sobre su tamaño, pero no hace parte del cálculo del score."]),
    "plat_deficit": (
        ["¿Qué significa el déficit habitacional?", "¿Qué es el déficit habitacional?", "Explícame el déficit habitacional."],
        ["El déficit habitacional mide cuántos hogares no tienen una vivienda adecuada. Se divide en cuantitativo, cuando faltan viviendas, y cualitativo, cuando las viviendas existen pero tienen carencias de calidad.",
         "Es la medida de las carencias de vivienda de los hogares: el cuantitativo cuenta las viviendas que faltan y el cualitativo, las que necesitan mejoras de estructura, espacio o servicios."]),
    "plat_ipm": (
        ["¿Qué es el IPM?", "¿Qué es el Índice de Pobreza Multidimensional?", "Explícame el IPM.", "¿Qué mide el IPM?"],
        ["El Índice de Pobreza Multidimensional, o IPM, mide la pobreza más allá del ingreso: cuenta las privaciones de un hogar en dimensiones como educación, niñez y juventud, trabajo, salud, servicios públicos y condiciones de la vivienda. Hoy el DANE es quien lo calcula.",
         "El IPM es el indicador oficial con el que Colombia mide la pobreza considerando varias privaciones del hogar y no solo el ingreso. Su cálculo oficial corresponde al DANE."]),
}


# Variantes adicionales de respuesta por tema (mismo contenido, otra redaccion): sin ellas la deduplicacion de respuestas
# deja pasar una sola respuesta por tema.
_C3_EXTRA = {
    "score_calculo": [
        "El Score de Privación se obtiene así: primero se llevan a una escala común la cobertura de acueducto, la cobertura de alcantarillado y el déficit habitacional cuantitativo; luego se invierten las dos coberturas, porque en ellas un valor alto es favorable; y por último se promedian los tres componentes con el mismo peso.",
    ],
    "score_componentes": [
        "El score se construye con tres variables: cobertura de acueducto, cobertura de alcantarillado y déficit habitacional cuantitativo. Ninguna pesa más que otra.",
        "En el cálculo del Score de Privación entran tres indicadores de igual peso: dos de servicios públicos, acueducto y alcantarillado, y uno de vivienda, el déficit cuantitativo.",
    ],
    "score_interpretacion": [
        "En el mapa, cuanto más intenso es el color, mayor es el score y, por tanto, mayor la privación. Si un municipio aparece en gris, es porque no hay información suficiente para calcularlo en ese año.",
        "Lee el Score de Privación así: valores bajos significan menos carencias y valores altos, más carencias. El gris en el mapa señala municipios sin información suficiente.",
    ],
    "score_densidad": [
        "La densidad es un dato de contexto, no de carencia: dice cuánta gente hay por kilómetro cuadrado, no cuánta privación sufre. Por eso queda fuera del Score de Privación.",
        "Se dejó la densidad fuera del score porque un lugar muy poblado puede tener pocas o muchas carencias; no mide privación del hogar sino concentración de población.",
    ],
    "score_normaliza": [
        "Normalizar es llevar cada variable a una misma escala, de modo que se puedan promediar. Las coberturas se invierten porque más cobertura es mejor, mientras que más déficit es peor.",
        "Antes de promediar, cada componente se reescala con su valor mínimo y su valor máximo. Después se invierten las coberturas, para que los tres componentes midan privación en el mismo sentido.",
    ],
    "score_mide": [
        "El score es una medida resumen de privación: junta en un solo número la situación de un municipio en acueducto, alcantarillado y déficit habitacional cuantitativo.",
        "Sirve para ver de un vistazo qué municipios tienen más carencias de agua, saneamiento y vivienda, y por eso es la base del mapa de calor.",
    ],
    "proy_significado": [
        "Una proyección es un valor calculado, no observado. Se usa para completar años sin medición y siempre se marca como tal, junto con su fiabilidad.",
        "Cuando ves la palabra proyección, el valor es una estimación del futuro o de un año sin dato. A diferencia de un dato real, trae su fiabilidad para que sepas cuánto confiar en él.",
    ],
    "proy_fiabilidad": [
        "La fiabilidad es un porcentaje que acompaña a cada proyección y orienta sobre qué tan confiable es. No es un cálculo estadístico formal sino una convención del proyecto.",
        "Piensa en la fiabilidad como una guía: a mayor porcentaje, más sólida es la estimación. Los datos reales no la llevan porque no son estimaciones.",
    ],
    "proy_metodo": [
        "Cada variable se proyecta a su manera: las coberturas suelen conservar el último valor real o interpolar entre dos reales, la densidad usa la población proyectada por el DANE y el déficit habitacional sale de un modelo propio de la plataforma.",
        "Las proyecciones no siguen una sola fórmula. Dependen de la variable y de los datos reales cercanos, y todas quedan identificadas como proyección en la respuesta.",
    ],
    "proy_real_vs": [
        "Un dato real es una medición publicada por una fuente; una estimación es un cálculo de la plataforma. Te lo indico en cada respuesta, y a las estimaciones les agrego su fiabilidad.",
        "Lo real se mide y se publica; lo proyectado se calcula. Por eso las proyecciones llevan una advertencia y su fiabilidad, y los datos reales no.",
    ],
    "proy_hueco": [
        "Si a una serie le falta un año, la plataforma lo calcula al consultar: interpola cuando hay datos reales a ambos lados o repite el último valor real. Cuando no hay base suficiente, prefiere decir que no hay dato.",
        "Un año sin dato no se deja en blanco sin explicación: se completa con una estimación sencilla a partir de los valores reales cercanos, o se informa que no es posible calcularlo.",
    ],
    "proy_confiar": [
        "Puedes usarlos como referencia, pero no como una medición: para {Y} se trata de estimaciones. Cada valor trae su fiabilidad, que te dice cuánto apoyarte en él.",
        "Los valores de {Y} son proyecciones, no datos observados. Te los entrego marcados como tales y con su fiabilidad, para que los leas con cautela.",
    ],
    "proy_cuando": [
        "Eso depende de la variable y del municipio. Si para {Y} existe una medición, será un dato real; si no, será una proyección, y en ese caso te lo advierto y te doy su fiabilidad.",
        "No hay una regla única. Para {Y} puedo tener un valor medido o uno estimado según el caso; cada respuesta aclara de cuál se trata.",
    ],
    "fuentes_origen": [
        "Los datos reales vienen de TerriData, del DNP, y del Censo Nacional de Población y Vivienda del DANE; la densidad proyectada usa las proyecciones de población del DANE. Lo demás son estimaciones de la plataforma y se marcan como proyección.",
        "Las fuentes son públicas y oficiales: TerriData para coberturas y densidad histórica, el censo del DANE para el déficit habitacional y las proyecciones de población del DANE para la densidad futura.",
    ],
    "fuentes_terridata": [
        "TerriData es una herramienta del Departamento Nacional de Planeación que reúne indicadores de los territorios. De allí salen, por ejemplo, las coberturas de acueducto y alcantarillado que consulta la plataforma.",
        "Es el portal de datos territoriales del DNP. La plataforma lo usa como fuente de datos reales por municipio.",
    ],
    "fuentes_cnpv": [
        "El CNPV es el censo del DANE que cuenta la población y las viviendas del país. Es la fuente del déficit habitacional por municipio y, como no se repite cada año, los años siguientes son estimaciones.",
        "Se usa el Censo Nacional de Población y Vivienda porque es la única medición del déficit habitacional que llega hasta el nivel municipal.",
    ],
    "fuentes_acueducto": [
        "Esas coberturas las publica TerriData, del DNP. Cuando un año no tiene medición, la plataforma lo estima y lo identifica como proyección.",
        "Provienen de TerriData, el sistema de indicadores territoriales del Departamento Nacional de Planeación.",
    ],
    "fuentes_deficit": [
        "El déficit habitacional por municipio sale del censo del DANE. Como el censo no se actualiza cada año, el valor cambia poco y los años siguientes se estiman con un modelo.",
        "Viene del Censo Nacional de Población y Vivienda, la única fuente municipal disponible; por eso no se refresca anualmente.",
    ],
    "plat_alcance": [
        "Puedo darte el valor de una variable en un municipio y un año, comparar municipios y explicar conceptos como el déficit habitacional, el IPM o el Score de Privación.",
        "Estoy para responder consultas de datos por municipio, como acueducto, alcantarillado, déficit habitacional, densidad poblacional y Score de Privación, y para explicar de dónde salen y qué significan.",
    ],
    "plat_variables": [
        "Hay seis variables disponibles: cobertura de acueducto, cobertura de alcantarillado, déficit habitacional cuantitativo, déficit habitacional cualitativo, densidad poblacional y Score de Privación.",
        "Puedes consultar dos coberturas de servicios públicos, dos medidas de déficit habitacional, la densidad poblacional y el Score de Privación.",
    ],
    "plat_consulta": [
        "Dime el municipio, la variable que te interesa y, si lo deseas, el año. Si el nombre del municipio se repite en varios departamentos, te pediré que indiques cuál.",
        "Para obtener un dato basta con nombrar un municipio y una variable. El año es opcional: si lo omites, uso el último con dato real.",
    ],
    "plat_funciona": [
        "Recibo tu pregunta, busco en la plataforma los datos que corresponden y te los explico en un par de frases. Si falta un dato, lo digo; no invento cifras.",
        "Mi trabajo es responder con los datos de la plataforma sobre pobreza y vivienda en los municipios de Colombia. Cuando no tengo un dato, te lo señalo.",
    ],
    "plat_municipios": [
        "La plataforma incluye casi todos los municipios del país, aunque no todos tienen dato en cada variable o año; en ese caso te lo indico.",
        "No tengo aquí el número exacto, pero la cobertura es casi total. Algunos municipios carecen de ciertas variables y la respuesta lo aclara.",
    ],
    "plat_deficit_tipos": [
        "El cuantitativo habla de cuántas viviendas faltan; el cualitativo, de cuántas viviendas existentes tienen problemas de estructura, espacio o servicios. Uno mide carencia de casa y el otro, mala calidad de la casa.",
        "Mientras el déficit cuantitativo cuenta hogares sin una vivienda adecuada, el cualitativo cuenta hogares con vivienda que necesita mejoras.",
    ],
    "plat_acueducto": [
        "Es el porcentaje de viviendas que cuentan con servicio de acueducto. Entre más alta, mejor, y por eso se invierte al calcular el Score de Privación.",
        "La cobertura de acueducto dice qué parte de las viviendas recibe agua por acueducto y es una de las tres piezas del Score de Privación.",
    ],
    "plat_alcantarillado": [
        "Dice qué proporción de las viviendas está conectada a alcantarillado; sirve para ver el acceso a saneamiento básico y es parte del Score de Privación.",
        "La cobertura de alcantarillado muestra qué tan extendido está el servicio de alcantarillado en un territorio.",
    ],
    "plat_densidad": [
        "Es el número de habitantes por kilómetro cuadrado. Se muestra como información de contexto del municipio y no cuenta para el score.",
        "La densidad poblacional compara cuánta gente vive en un territorio con su tamaño: habitantes por kilómetro cuadrado.",
    ],
    "plat_deficit": [
        "Es la forma de medir cuántos hogares carecen de una vivienda adecuada, ya sea porque no hay casa suficiente o porque la existente tiene problemas de calidad.",
        "El déficit habitacional agrupa dos problemas: faltan viviendas, que es el déficit cuantitativo, o las que hay necesitan mejoras, que es el cualitativo.",
    ],
    "plat_ipm": [
        "El IPM es el índice con el que el DANE mide la pobreza considerando varias dimensiones de la vida del hogar, como educación, trabajo, salud y vivienda, y no solo el ingreso.",
        "Índice de Pobreza Multidimensional: un hogar se considera pobre cuando acumula suficientes privaciones en varias dimensiones, y no solo por tener un ingreso bajo.",
    ],
}
for _tema, _extra in _C3_EXTRA.items():
    C3_TEMAS[_tema][1].extend(_extra)


def c3(tema: str, anio: int | None, el: Eleccion) -> tuple[str, str]:
    respuestas = C3_TEMAS[tema][1]
    i, r = el.de("C3:" + tema, respuestas)
    return (r.replace("{Y}", str(anio)) if anio else r), f"C3.{tema}.{i}"
