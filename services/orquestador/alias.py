"""Tablas de alias deterministas del orquestador (fase4-especificacion-1, 4.1). UN solo archivo.

Todo va normalizado (minusculas, sin tildes, sin puntuacion, espacios colapsados). La coincidencia es
por palabras completas y gana la mas larga ("deficit habitacional cuantitativo" antes que
"deficit habitacional").
"""

V_SCORE = "score_privacion"
V_ACUEDUCTO = "cobertura_acueducto"
V_ALCANTARILLADO = "cobertura_alcantarillado"
V_DEFICIT_CUANTITATIVO = "deficit_habitacional_cuantitativo"
V_DEFICIT_CUALITATIVO = "deficit_habitacional_cualitativo"
V_DENSIDAD = "densidad_poblacional"

VARIABLES = (V_SCORE, V_ACUEDUCTO, V_ALCANTARILLADO, V_DEFICIT_CUANTITATIVO, V_DEFICIT_CUALITATIVO, V_DENSIDAD)

# Nombre legible de cada variable (para las lineas del contexto).
NOMBRE_VARIABLE = {
    V_SCORE: "Score de privación",
    V_ACUEDUCTO: "Cobertura de acueducto",
    V_ALCANTARILLADO: "Cobertura de alcantarillado",
    V_DEFICIT_CUANTITATIVO: "Déficit habitacional cuantitativo",
    V_DEFICIT_CUALITATIVO: "Déficit habitacional cualitativo",
    V_DENSIDAD: "Densidad poblacional",
}

# frase normalizada -> variables que activa ("deficit habitacional" sin calificar activa ambos deficits).
ALIAS_VARIABLES = {
    "cobertura de acueducto": (V_ACUEDUCTO,),
    "cobertura acueducto": (V_ACUEDUCTO,),
    "acueducto": (V_ACUEDUCTO,),
    "cobertura de alcantarillado": (V_ALCANTARILLADO,),
    "cobertura alcantarillado": (V_ALCANTARILLADO,),
    "alcantarillado": (V_ALCANTARILLADO,),
    "deficit habitacional cuantitativo": (V_DEFICIT_CUANTITATIVO,),
    "deficit cuantitativo": (V_DEFICIT_CUANTITATIVO,),
    "deficit de vivienda cuantitativo": (V_DEFICIT_CUANTITATIVO,),
    "deficit habitacional cualitativo": (V_DEFICIT_CUALITATIVO,),
    "deficit cualitativo": (V_DEFICIT_CUALITATIVO,),
    "deficit de vivienda cualitativo": (V_DEFICIT_CUALITATIVO,),
    "deficit habitacional": (V_DEFICIT_CUANTITATIVO, V_DEFICIT_CUALITATIVO),
    "deficit de vivienda": (V_DEFICIT_CUANTITATIVO, V_DEFICIT_CUALITATIVO),
    "deficit": (V_DEFICIT_CUANTITATIVO, V_DEFICIT_CUALITATIVO),
    "densidad poblacional": (V_DENSIDAD,),
    "densidad de poblacion": (V_DENSIDAD,),
    "densidad": (V_DENSIDAD,),
    "score de privacion": (V_SCORE,),
    "indice de privacion": (V_SCORE,),
    "score": (V_SCORE,),
    "privacion": (V_SCORE,),
}

# Nombres con los que se conoce a un municipio y que no son su nombre oficial en zonas_municipio
# (p. ej. "Santiago De Cali"). Solo se aplican si el codigo existe en el catalogo cargado.
ALIAS_MUNICIPIOS = {
    "cali": "76001",
    "bogota": "11001",
    "bogota dc": "11001",
    "bogota d c": "11001",
    "cartagena": "13001",
    "cucuta": "54001",
}

# Nombre visible de un departamento cuando el de la tabla no sirve (Bogota trae una nota larga).
NOMBRE_VISIBLE_DEPARTAMENTO = {"11": "Bogotá, D.C."}

# Otros nombres con los que se conoce a un departamento (codigo -> alias normalizados).
ALIAS_DEPARTAMENTOS = {
    "11": ("bogota", "bogota d c", "bogota dc"),
    "76": ("valle",),
    "88": ("san andres y providencia", "san andres providencia y santa catalina"),
    "44": ("guajira",),
}

# Palabras que pueden ir entre un municipio y su departamento ("Rionegro de Santander", "en Antioquia: Rionegro").
CONECTORES = frozenset({"de", "del", "en", "la", "el", "departamento", "dpto", "depto", "municipio"})

# 4.1b: nombres de municipio que coinciden con una palabra comun del español. Solo cuentan como municipio si
# van capitalizados en el texto original, precedidos de "de/del/en" o acompañados de un departamento.
# Lista corta y ampliable (claves normalizadas).
NOMBRES_SENSIBLES = frozenset({
    "pesca", "florida", "granada", "piedras", "palmar", "hato", "toro", "venecia", "sucre", "caldas",
})
