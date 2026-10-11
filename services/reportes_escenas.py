"""Motor de escenas de las gráficas del reporte.

Cada gráfica se calcula UNA sola vez, aquí, como una escena vectorial (rectángulos, líneas,
polígonos, círculos y textos en un lienzo de `ancho` × `alto`). La vista previa la dibuja en SVG
(EscenaSVG.jsx) y el PDF la dibuja con ReportLab (reportes_pdf.py) a partir de esa misma escena, así
que la vista previa y el documento no pueden diferir. Funciones puras: sin BD ni red.

Reglas de visualización: comparar territorios con barras horizontales ordenadas, evolución con líneas
(solo con datos por año), composición con barras apiladas, donut solo de dos partes, sin 3D, un eje
por unidad (porcentaje, índice, hab/km² nunca juntos) y números en español (coma decimal).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from reportlab.pdfbase.pdfmetrics import stringWidth

from services.reportes_graficas_catalogo import COBERTURAS, DEFICITS, GRUPO_UNIDAD, TIPOS
from services.reportes_service import PALETA_GRAFICA, VARIABLES_REPORTE

FUENTE = "Helvetica"
TAM = 11
GRIS_EJE = "#8a847b"
GRIS_REJILLA = "#e6e1d8"
TEXTO = "#3d3935"
MAX_FILAS = 20
MAX_SERIES = 10
COLOR_NACIONAL = "#6c757d"
TRAZOS = [None, [5, 2], [1, 2], [5, 2, 1, 2], [2, 2], [8, 3], [3, 1], [6, 2, 2, 2], [1, 1], [4, 4]]


# ---------------------------------------------------------------- datos de entrada
@dataclass
class DatosGraficas:
    """Valores ya calculados que consumen las escenas (ver reportes_datos_graficas.py)."""

    anios: list[int]
    zonas: list[dict]  # zonas seleccionadas del reporte (en orden): nivel, codigo, nombre, etiqueta
    medias: dict[tuple[str, str], float | None]  # (codigo, variable) -> media del periodo
    anual: dict[tuple[str, str], list[float | None]]  # (codigo, variable) -> un valor por año
    nacional: dict[str, dict] = field(default_factory=dict)  # variable -> media/min/q1/mediana/q3/max/n
    geometrias: dict[str, dict] = field(default_factory=dict)  # codigo -> geometría GeoJSON

    @property
    def periodo(self) -> str:
        return f"{self.anios[0]}–{self.anios[-1]}" if len(self.anios) > 1 else str(self.anios[0])

    def zonas_de(self, spec) -> list[dict]:
        if spec.modo == "general":
            return list(self.zonas)
        por_codigo = {zona["codigo"]: zona for zona in self.zonas}
        return [por_codigo[codigo] for codigo in spec.zonas if codigo in por_codigo]

    def media(self, codigo: str, variable: str) -> float | None:
        return self.medias.get((codigo, variable))


@dataclass
class Resultado:
    titulo: str
    escena: dict
    tabla: dict  # {"encabezados": [...], "filas": [[...]]}  (para Excel y accesibilidad)
    nota: str = ""


# ---------------------------------------------------------------- utilidades
def etiqueta_zona(zona: dict) -> str:
    return zona.get("etiqueta") or zona["nombre"]


def nombre_variable(variable: str) -> str:
    return VARIABLES_REPORTE[variable][0]


CORTO = {
    "score_privacion": "Score de privación",
    "cobertura_acueducto": "Acueducto",
    "cobertura_alcantarillado": "Alcantarillado",
    "deficit_habitacional_cuantitativo": "Déficit cuant.",
    "deficit_habitacional_cualitativo": "Déficit cual.",
    "densidad_poblacional": "Densidad",
}


def unidad_variable(variable: str) -> str:
    return VARIABLES_REPORTE[variable][1]


def decimales_de(variable: str) -> int:
    return {"score_privacion": 2, "densidad_poblacional": 1}.get(variable, 1)


def num_es(valor: float, decimales: int) -> str:
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt(valor: float | None, variable: str, con_unidad: bool = False) -> str:
    if valor is None:
        return "n.d."
    texto = num_es(valor, decimales_de(variable))
    unidad = unidad_variable(variable)
    if con_unidad and unidad:
        return f"{texto} {unidad}"  # % con espacio, como en las tablas
    return texto


def ancho_texto(texto: str, tam: float = TAM, negrita: bool = False) -> float:
    return stringWidth(texto, "Helvetica-Bold" if negrita else FUENTE, tam)


def recortar(texto: str, maximo: float, tam: float = TAM) -> str:
    if ancho_texto(texto, tam) <= maximo:
        return texto
    while texto and ancho_texto(texto + "…", tam) > maximo:
        texto = texto[:-1]
    return texto.rstrip() + "…"


def numero_agradable(valor: float, redondear: bool) -> float:
    exponente = math.floor(math.log10(valor))
    fraccion = valor / 10**exponente
    if redondear:
        agradable = 1 if fraccion < 1.5 else 2 if fraccion < 3 else 5 if fraccion < 7 else 10
    else:
        agradable = 1 if fraccion <= 1 else 2 if fraccion <= 2 else 5 if fraccion <= 5 else 10
    return agradable * 10**exponente


def escala_agradable(minimo: float, maximo: float, marcas: int = 5, desde_cero: bool = False) -> tuple[float, float, list[float], int]:
    """(mínimo, máximo, marcas, decimales) con valores redondos que cubren [minimo, maximo]."""
    if desde_cero:
        minimo = min(0.0, minimo)
    if minimo == maximo:
        holgura = 1.0 if minimo == 0 else abs(minimo) * 0.1
        minimo, maximo = minimo - holgura, maximo + holgura
    paso = numero_agradable(numero_agradable(maximo - minimo, False) / (marcas - 1), True)
    inicio = math.floor(minimo / paso) * paso
    fin = math.ceil(maximo / paso) * paso
    lista = []
    valor = inicio
    while valor <= fin + paso / 2:
        lista.append(round(valor, 10))
        valor += paso
    decimales = min(4, max(0, -math.floor(math.log10(paso))))
    return lista[0], lista[-1], lista, decimales


def mezclar(color_a: str, color_b: str, t: float) -> str:
    a = [int(color_a[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def rampa(t: float) -> str:
    """Escala secuencial (claro -> oscuro, amarillo-naranja-rojo, como la escala de privación del mapa)."""
    t = max(0.0, min(1.0, t))
    paradas = [(0.0, "#fff4cc"), (0.5, "#f6a04b"), (1.0, "#9c2a0f")]
    for (t0, c0), (t1, c1) in zip(paradas, paradas[1:]):
        if t <= t1:
            return mezclar(c0, c1, (t - t0) / (t1 - t0))
    return paradas[-1][1]


def r1(valor: float) -> float:
    return round(valor, 1)


class Escena:
    def __init__(self, ancho: float, alto: float):
        self.ancho = ancho
        self.alto = alto
        self.elementos: list[dict] = []

    def rect(self, x, y, w, h, fill=None, stroke=None, sw=1, titulo=None):
        elemento = {"t": "rect", "x": r1(x), "y": r1(y), "w": r1(max(w, 0)), "h": r1(max(h, 0))}
        self._estilo(elemento, fill, stroke, sw, titulo)

    def linea(self, x1, y1, x2, y2, stroke=GRIS_EJE, sw=1, dash=None):
        elemento = {"t": "line", "x1": r1(x1), "y1": r1(y1), "x2": r1(x2), "y2": r1(y2), "stroke": stroke, "sw": sw}
        if dash:
            elemento["dash"] = dash
        self.elementos.append(elemento)

    def poli(self, puntos, cerrado=False, fill=None, stroke=None, sw=1, titulo=None, dash=None):
        elemento = {"t": "poly", "p": [[r1(x), r1(y)] for x, y in puntos], "cerrado": cerrado}
        if dash:
            elemento["dash"] = dash
        self._estilo(elemento, fill, stroke, sw, titulo)

    def circulo(self, cx, cy, r, fill=None, stroke=None, sw=1, titulo=None):
        elemento = {"t": "circle", "cx": r1(cx), "cy": r1(cy), "r": r1(r)}
        self._estilo(elemento, fill, stroke, sw, titulo)

    def texto(self, x, y, texto, tam=TAM, anchor="start", negrita=False, fill=TEXTO, rotar=0):
        elemento = {"t": "text", "x": r1(x), "y": r1(y), "s": texto, "size": tam, "anchor": anchor, "fill": fill}
        if negrita:
            elemento["bold"] = True
        if rotar:
            elemento["rot"] = rotar
        self.elementos.append(elemento)

    def _estilo(self, elemento, fill, stroke, sw, titulo):
        if fill:
            elemento["fill"] = fill
        if stroke:
            elemento["stroke"] = stroke
            elemento["sw"] = sw
        if titulo:
            elemento["title"] = titulo
        self.elementos.append(elemento)

    def como_dict(self) -> dict:
        return {"ancho": r1(self.ancho), "alto": r1(self.alto), "elementos": self.elementos}


def leyenda(escena: Escena, x: float, y: float, entradas: list[tuple[str, str, list | None]], ancho_max: float) -> float:
    """Leyenda en filas; devuelve el alto usado. entradas = (texto, color, trazo)."""
    cursor_x, cursor_y = x, y
    alto_fila = 16
    for texto, color, trazo in entradas:
        etiqueta = recortar(texto, ancho_max * 0.6)
        ancho_entrada = 20 + ancho_texto(etiqueta, TAM - 1) + 14
        if cursor_x + ancho_entrada > x + ancho_max and cursor_x > x:
            cursor_x = x
            cursor_y += alto_fila
        escena.linea(cursor_x, cursor_y + 6, cursor_x + 14, cursor_y + 6, color, 3 if trazo is None else 2, trazo)
        escena.texto(cursor_x + 19, cursor_y + 10, etiqueta, TAM - 1)
        cursor_x += ancho_entrada
    return cursor_y + alto_fila - y


def leyenda_cuadros(escena: Escena, x: float, y: float, entradas: list[tuple[str, str]], ancho_max: float) -> float:
    cursor_x, cursor_y = x, y
    alto_fila = 16
    for texto, color in entradas:
        etiqueta = recortar(texto, ancho_max * 0.6)
        ancho_entrada = 18 + ancho_texto(etiqueta, TAM - 1) + 14
        if cursor_x + ancho_entrada > x + ancho_max and cursor_x > x:
            cursor_x = x
            cursor_y += alto_fila
        escena.rect(cursor_x, cursor_y + 1, 10, 10, fill=color)
        escena.texto(cursor_x + 15, cursor_y + 10, etiqueta, TAM - 1)
        cursor_x += ancho_entrada
    return cursor_y + alto_fila - y


def eje_horizontal_valores(escena: Escena, x0: float, x1: float, y_arriba: float, y_abajo: float, minimo: float, maximo: float, marcas: list[float], decimales: int, unidad: str = ""):
    """Rejilla vertical y rótulos del eje X de valores (barras horizontales)."""
    for marca in marcas:
        x = x0 + (marca - minimo) / (maximo - minimo) * (x1 - x0)
        escena.linea(x, y_arriba, x, y_abajo, GRIS_REJILLA, 1)
        escena.texto(x, y_abajo + 13, num_es(marca, decimales), TAM - 1, "middle", fill=GRIS_EJE)
    escena.linea(x0, y_arriba, x0, y_abajo, GRIS_EJE, 1)
    if unidad:
        escena.texto(x1, y_abajo + 26, unidad, TAM - 1, "end", fill=GRIS_EJE)


def titulo_auto(spec, datos: DatosGraficas) -> str:
    if spec.titulo.strip():
        return spec.titulo.strip()
    nombres = [nombre_variable(v) for v in spec.variables]
    uno = nombres[0]
    union = " y ".join(nombres) if len(nombres) <= 2 else f"{len(nombres)} indicadores"
    periodo = datos.periodo
    por_tipo = {
        "coropletico": f"Mapa de {uno} por zona, promedio {periodo}",
        "barras_ranking": f"{uno} por zona, promedio {periodo}",
        "barras_agrupadas": f"{union} por zona, promedio {periodo}",
        "barras_apiladas": f"{union} (composición), promedio {periodo}",
        "lineas": f"Evolución de {uno}, {periodo}",
        "radar": f"Perfil de {len(nombres)} indicadores frente al promedio nacional",
        "dispersion": f"{nombres[1] if len(nombres) > 1 else uno} frente a {uno}",
        "histograma": f"Distribución de {uno}",
        "boxplot": f"Distribución de {uno} (diagrama de caja)",
        "bullet": f"{uno} frente al promedio nacional",
        "gauge": f"{uno}",
        "dumbbell": f"Brecha entre {union}",
        "donut": "Proporción entre el déficit cuantitativo y el cualitativo",
        "treemap": f"{uno} por zona (treemap)",
        "correlaciones": "Correlación entre indicadores",
        "paneles": f"Indicadores por zona, promedio {periodo}",
    }
    return por_tipo[spec.tipo]


def _valores(datos: DatosGraficas, zonas: list[dict], variable: str) -> list[tuple[dict, float]]:
    pares = []
    for zona in zonas:
        valor = datos.media(zona["codigo"], variable)
        if valor is not None:
            pares.append((zona, valor))
    return pares


def _nacional(datos: DatosGraficas, variable: str) -> dict | None:
    return datos.nacional.get(variable) or None


def _tabla_zonas(datos, zonas, variables) -> dict:
    return {
        "encabezados": ["Zona"] + [f"{nombre_variable(v)} ({unidad_variable(v)})" if unidad_variable(v) else nombre_variable(v) for v in variables],
        "filas": [[etiqueta_zona(z)] + [datos.media(z["codigo"], v) for v in variables] for z in zonas],
    }


def _sin_datos(ancho: float, mensaje: str = "Sin datos para las zonas y el periodo elegidos.") -> dict:
    escena = Escena(ancho, 70)
    escena.texto(ancho / 2, 38, mensaje, TAM, "middle", fill=GRIS_EJE)
    return escena.como_dict()


def _limitar(items: list, maximo: int, nota_base: str = "zonas") -> tuple[list, str]:
    if len(items) > maximo:
        return items[:maximo], f"Se muestran las primeras {maximo} de {len(items)} {nota_base}."
    return items, ""


# ================================================================= tipos
def _barras_horizontales(escena_ancho: float, filas: list[tuple[str, float, str | None]], variable: str, color: str, nacional: dict | None, ref_texto: str) -> Escena:
    """Barras horizontales (una por fila) con rótulos a la izquierda, valor al final y línea nacional."""
    ml = min(max(ancho_texto(etiqueta) for etiqueta, _, _ in filas) + 10, escena_ancho * 0.38)
    x0, x1 = ml + 8, escena_ancho - 62
    mt, alto_fila = 22, 20
    maximo_dato = max(valor for _, valor, _ in filas)
    if nacional:
        maximo_dato = max(maximo_dato, nacional["media"])
    _, maximo, marcas, dec = escala_agradable(0, maximo_dato, 5, desde_cero=True)
    alto = mt + len(filas) * alto_fila + 38
    escena = Escena(escena_ancho, alto)
    eje_horizontal_valores(escena, x0, x1, mt - 4, mt + len(filas) * alto_fila, 0, maximo, marcas, dec, unidad_variable(variable))
    for i, (etiqueta, valor, titulo) in enumerate(filas):
        y = mt + i * alto_fila
        escena.texto(ml, y + 13, recortar(etiqueta, ml - 6), TAM - 1, "end")
        escena.rect(x0, y + 3, (valor / maximo) * (x1 - x0), 13, fill=color, titulo=titulo or f"{etiqueta}: {fmt(valor, variable, True)}")
        escena.texto(x0 + (valor / maximo) * (x1 - x0) + 4, y + 13, fmt(valor, variable), TAM - 1, fill=TEXTO)
    if nacional:
        x = x0 + (nacional["media"] / maximo) * (x1 - x0)
        escena.linea(x, mt - 6, x, mt + len(filas) * alto_fila, COLOR_NACIONAL, 1.5, [4, 3])
        escena.texto(x, 10, f"{ref_texto} {fmt(nacional['media'], variable)}", TAM - 2, "middle", fill=COLOR_NACIONAL)
    return escena


def ranking(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    zonas = datos.zonas_de(spec)
    pares = sorted(_valores(datos, zonas, variable), key=lambda par: -par[1])
    titulo = titulo_auto(spec, datos)
    tabla = {"encabezados": ["Zona", f"{nombre_variable(variable)}"], "filas": [[etiqueta_zona(z), v] for z, v in pares]}
    if not pares:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    mostrados, nota = _limitar(pares, MAX_FILAS)
    filas = [(etiqueta_zona(z), v, None) for z, v in mostrados]
    escena = _barras_horizontales(ancho, filas, variable, PALETA_GRAFICA[0], _nacional(datos, variable), "Prom. nacional")
    return Resultado(titulo, escena.como_dict(), tabla, nota)


def barras_agrupadas(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variables = list(spec.variables)
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    tabla = _tabla_zonas(datos, zonas, variables)
    individual = spec.modo == "individual"
    # individual: un grupo por variable (una barra de la zona); resto: un grupo por zona (una barra por variable)
    if individual:
        zona = zonas[0]
        grupos = [(nombre_variable(v), [(0, datos.media(zona["codigo"], v))]) for v in variables]
        series = [(etiqueta_zona(zona), PALETA_GRAFICA[0])]
        grupos_color = {i: PALETA_GRAFICA[i % len(PALETA_GRAFICA)] for i in range(len(variables))}
    else:
        grupos_validos = [z for z in zonas if any(datos.media(z["codigo"], v) is not None for v in variables)]
        grupos_validos.sort(key=lambda z: -max((datos.media(z["codigo"], v) or 0) for v in variables))
        grupos_validos, nota = _limitar(grupos_validos, MAX_FILAS)
        grupos = [(etiqueta_zona(z), [(i, datos.media(z["codigo"], v)) for i, v in enumerate(variables)]) for z in grupos_validos]
        series = [(nombre_variable(v), PALETA_GRAFICA[i % len(PALETA_GRAFICA)]) for i, v in enumerate(variables)]
    nota = "" if individual else nota
    valores = [v for _, barras in grupos for _, v in barras if v is not None]
    if not valores:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    ml = min(max(ancho_texto(nombre) for nombre, _ in grupos) + 10, ancho * 0.36)
    x0, x1 = ml + 8, ancho - 58
    k = 1 if individual else len(variables)
    alto_barra, hueco = 11, 8
    alto_grupo = k * alto_barra + hueco
    mt = 12
    _, maximo, marcas, dec = escala_agradable(0, max(valores), 5, desde_cero=True)
    alto_grafica = len(grupos) * alto_grupo
    escena = Escena(ancho, mt + alto_grafica + 30 + 26)
    unidad = unidad_variable(variables[0])
    eje_horizontal_valores(escena, x0, x1, mt - 4, mt + alto_grafica, 0, maximo, marcas, dec, unidad)
    for g, (nombre, barras) in enumerate(grupos):
        y = mt + g * alto_grupo
        escena.texto(ml, y + (k * alto_barra) / 2 + 4, recortar(nombre, ml - 6), TAM - 1, "end")
        for b, (indice, valor) in enumerate(barras):
            if valor is None:
                continue
            color = grupos_color[g] if individual else PALETA_GRAFICA[indice % len(PALETA_GRAFICA)]
            escena.rect(x0, y + b * alto_barra, (valor / maximo) * (x1 - x0), alto_barra - 1, fill=color, titulo=f"{nombre}: {fmt(valor, variables[indice if not individual else g], True)}")
            escena.texto(x0 + (valor / maximo) * (x1 - x0) + 4, y + b * alto_barra + alto_barra - 2, fmt(valor, variables[indice if not individual else g]), TAM - 2)
    y_ley = mt + alto_grafica + 30
    if not individual:
        usado = leyenda_cuadros(escena, 0, y_ley, [(n, c) for n, c in series], ancho)
        escena.alto = y_ley + usado + 4
    else:
        escena.alto = mt + alto_grafica + 30
    return Resultado(titulo, escena.como_dict(), tabla, nota)


def barras_apiladas(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variables = list(spec.variables)
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    cobertura = len(variables) == 1  # cubierto + sin cobertura = 100 %
    if cobertura:
        v = variables[0]
        segmentos_def = [(f"{nombre_variable(v)} (cubierto)", PALETA_GRAFICA[2]), ("Sin cobertura", "#d9d4ca")]
    else:
        segmentos_def = [(nombre_variable(DEFICITS[0]), PALETA_GRAFICA[1]), (nombre_variable(DEFICITS[1]), PALETA_GRAFICA[3])]
    filas = []
    for zona in zonas:
        if cobertura:
            valor = datos.media(zona["codigo"], variables[0])
            if valor is None:
                continue
            partes = [max(0.0, min(100.0, valor)), max(0.0, 100.0 - valor)]
        else:
            cuant, cual = datos.media(zona["codigo"], DEFICITS[0]), datos.media(zona["codigo"], DEFICITS[1])
            if cuant is None and cual is None:
                continue
            partes = [cuant or 0.0, cual or 0.0]
        filas.append((etiqueta_zona(zona), partes))
    tabla = {"encabezados": ["Zona"] + [n for n, _ in segmentos_def] + (["Total"] if not cobertura else []), "filas": [[n] + p + ([sum(p)] if not cobertura else []) for n, p in filas]}
    if not filas:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    if not cobertura:
        filas.sort(key=lambda f: -sum(f[1]))
    filas, nota = _limitar(filas, MAX_FILAS)
    ml = min(max(ancho_texto(n) for n, _ in filas) + 10, ancho * 0.36)
    x0, x1 = ml + 8, ancho - 50
    mt, alto_fila = 12, 22
    tope = 100.0 if cobertura else max(sum(p) for _, p in filas)
    _, maximo, marcas, dec = escala_agradable(0, tope, 5, desde_cero=True)
    alto_grafica = len(filas) * alto_fila
    escena = Escena(ancho, mt + alto_grafica + 30 + 20)
    eje_horizontal_valores(escena, x0, x1, mt - 4, mt + alto_grafica, 0, maximo, marcas, dec, "%")
    for i, (nombre, partes) in enumerate(filas):
        y = mt + i * alto_fila
        escena.texto(ml, y + 14, recortar(nombre, ml - 6), TAM - 1, "end")
        acumulado = 0.0
        for j, parte in enumerate(partes):
            ancho_seg = (parte / maximo) * (x1 - x0)
            escena.rect(x0 + (acumulado / maximo) * (x1 - x0), y + 3, ancho_seg, 15, fill=segmentos_def[j][1], titulo=f"{nombre} · {segmentos_def[j][0]}: {num_es(parte, 1)} %")
            if ancho_seg > 30:
                escena.texto(x0 + (acumulado / maximo) * (x1 - x0) + ancho_seg / 2, y + 14, num_es(parte, 1), TAM - 2, "middle", fill="#ffffff" if j == 0 or not cobertura else TEXTO)
            acumulado += parte
        if not cobertura:
            escena.texto(x0 + (acumulado / maximo) * (x1 - x0) + 4, y + 14, num_es(acumulado, 1), TAM - 1, fill=TEXTO, negrita=True)
    y_ley = mt + alto_grafica + 30
    usado = leyenda_cuadros(escena, 0, y_ley, segmentos_def, ancho)
    escena.alto = y_ley + usado + 4
    return Resultado(titulo, escena.como_dict(), tabla, nota)


def lineas(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    zonas = [z for z in datos.zonas_de(spec) if any(v is not None for v in datos.anual.get((z["codigo"], variable), []))]
    titulo = titulo_auto(spec, datos)
    zonas_mostradas, nota = _limitar(zonas, MAX_SERIES)
    tabla = {"encabezados": ["Año"] + [etiqueta_zona(z) for z in zonas_mostradas], "filas": [[a] + [datos.anual[(z["codigo"], variable)][i] for z in zonas_mostradas] for i, a in enumerate(datos.anios)]}
    if not zonas_mostradas:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    todos = [v for z in zonas_mostradas for v in datos.anual[(z["codigo"], variable)] if v is not None]
    minimo, maximo, marcas, dec = escala_agradable(min(todos), max(todos), 5)
    ml, mr, mt = 52, 14, 12
    alto_grafica = 190
    x0, x1 = ml, ancho - mr
    escena = Escena(ancho, mt + alto_grafica + 28)
    for marca in marcas:
        y = mt + alto_grafica - (marca - minimo) / (maximo - minimo) * alto_grafica
        escena.linea(x0, y, x1, y, GRIS_REJILLA, 1)
        escena.texto(x0 - 6, y + 4, num_es(marca, dec), TAM - 1, "end", fill=GRIS_EJE)
    n = len(datos.anios)
    salto = max(1, math.ceil(n / 10))

    def px(i):
        return x0 + (i / (n - 1) * (x1 - x0) if n > 1 else (x1 - x0) / 2)

    for i in range(0, n, salto):
        escena.texto(px(i), mt + alto_grafica + 15, str(datos.anios[i]), TAM - 1, "middle", fill=GRIS_EJE)
    escena.linea(x0, mt + alto_grafica, x1, mt + alto_grafica, GRIS_EJE, 1)
    entradas = []
    for indice, zona in enumerate(zonas_mostradas):
        color = PALETA_GRAFICA[indice % len(PALETA_GRAFICA)]
        trazo = TRAZOS[indice % len(TRAZOS)]
        valores = datos.anual[(zona["codigo"], variable)]
        tramo: list[tuple[float, float]] = []

        def cerrar_tramo():
            if len(tramo) > 1:
                escena.poli(list(tramo), fill=None, stroke=color, sw=2, dash=trazo)

        for i, valor in enumerate(valores):
            if valor is None:
                cerrar_tramo()
                tramo.clear()
                continue
            y = mt + alto_grafica - (valor - minimo) / (maximo - minimo) * alto_grafica
            tramo.append((px(i), y))
            escena.circulo(px(i), y, 2.6, fill=color, titulo=f"{etiqueta_zona(zona)} · {datos.anios[i]}: {fmt(valor, variable, True)}")
        cerrar_tramo()
        entradas.append((etiqueta_zona(zona), color, trazo))
    usado = leyenda(escena, 0, mt + alto_grafica + 26, entradas, ancho)
    escena.alto = mt + alto_grafica + 26 + usado + 4
    return Resultado(titulo, escena.como_dict(), tabla, nota)


# ---- coroplético
def _proyectar(geometrias: list[dict]):
    """Proyección equirrectangular escalada por cos(lat); devuelve (anillos por geometría, caja)."""
    anillos_por_geom = []
    lons, lats = [], []
    for geometria in geometrias:
        poligonos = geometria["coordinates"] if geometria["type"] == "MultiPolygon" else [geometria["coordinates"]]
        anillos = []
        for poligono in poligonos:
            for anillo in poligono[:1]:  # solo el contorno exterior (los huecos no se dibujan)
                anillos.append(anillo)
                for lon, lat in anillo:
                    lons.append(lon)
                    lats.append(lat)
        anillos_por_geom.append(anillos)
    return anillos_por_geom, (min(lons), max(lons), min(lats), max(lats))


def coropletico(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    zonas = [z for z in datos.zonas_de(spec) if z["codigo"] in datos.geometrias]
    titulo = titulo_auto(spec, datos)
    tabla = {"encabezados": ["Zona", nombre_variable(variable)], "filas": [[etiqueta_zona(z), datos.media(z["codigo"], variable)] for z in datos.zonas_de(spec)]}
    if not zonas:
        return Resultado(titulo, _sin_datos(ancho, "Sin geometrías para dibujar el mapa."), tabla)
    valores = [v for v in (datos.media(z["codigo"], variable) for z in zonas) if v is not None]
    minimo, maximo = (min(valores), max(valores)) if valores else (0.0, 1.0)
    anillos_por_geom, (lon0, lon1, lat0, lat1) = _proyectar([datos.geometrias[z["codigo"]] for z in zonas])
    factor = math.cos(math.radians((lat0 + lat1) / 2))
    ancho_geo = (lon1 - lon0) * factor or 1e-6
    alto_geo = (lat1 - lat0) or 1e-6
    ancho_mapa = ancho - 130
    alto_mapa = min(ancho_mapa * alto_geo / ancho_geo, ancho * 0.9)
    escala = min(ancho_mapa / ancho_geo, alto_mapa / alto_geo)
    ox = (ancho_mapa - ancho_geo * escala) / 2
    escena = Escena(ancho, max(alto_mapa, 150) + 10)
    for zona, anillos in zip(zonas, anillos_por_geom):
        valor = datos.media(zona["codigo"], variable)
        t = 0.5 if maximo == minimo else (valor - minimo) / (maximo - minimo) if valor is not None else None
        color = rampa(t) if t is not None else "#d0cbc0"
        for anillo in anillos:
            puntos = [(ox + (lon - lon0) * factor * escala, 5 + (lat1 - lat) * escala) for lon, lat in anillo]
            escena.poli(puntos, cerrado=True, fill=color, stroke="#ffffff", sw=0.6, titulo=f"{etiqueta_zona(zona)}: {fmt(valor, variable, True)}")
    # barra de color y límites a la derecha
    bx, by = ancho - 100, 20
    pasos = 24
    for i in range(pasos):
        escena.rect(bx, by + i * 6, 14, 6.4, fill=rampa(1 - i / (pasos - 1)))
    escena.texto(bx + 20, by + 8, fmt(maximo, variable), TAM - 1)
    escena.texto(bx + 20, by + pasos * 6, fmt(minimo, variable), TAM - 1)
    unidad = unidad_variable(variable)
    escena.texto(bx, by - 8, f"{nombre_variable(variable)}"[:18] + (f" ({unidad})" if unidad else ""), TAM - 2)
    if any(datos.media(z["codigo"], variable) is None for z in zonas):
        escena.rect(bx, by + pasos * 6 + 18, 10, 10, fill="#d0cbc0")
        escena.texto(bx + 15, by + pasos * 6 + 27, "Sin dato", TAM - 1)
    nota = "Colores relativos a los valores de las zonas mostradas."
    if len({z["nivel"] for z in zonas}) > 1:
        nota += " Los municipios se dibujan sobre su departamento."
    return Resultado(titulo, escena.como_dict(), tabla, nota)


# ---- radar
def _normalizar_0_100(valor: float, variable: str, nacional: dict | None, extremos: tuple[float, float]) -> float:
    minimo, maximo = (nacional["min"], nacional["max"]) if nacional else extremos
    if maximo == minimo:
        return 50.0
    return max(0.0, min(100.0, (valor - minimo) / (maximo - minimo) * 100))


def radar(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variables = list(spec.variables)
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    extremos = {v: (min([m for z in zonas if (m := datos.media(z["codigo"], v)) is not None] or [0.0]), max([m for z in zonas if (m := datos.media(z["codigo"], v)) is not None] or [1.0])) for v in variables}
    tabla = {"encabezados": ["Zona"] + [nombre_variable(v) for v in variables], "filas": [[etiqueta_zona(z)] + [datos.media(z["codigo"], v) for v in variables] for z in zonas] + [["Promedio nacional"] + [(_nacional(datos, v) or {}).get("media") for v in variables]]}
    radio = min(ancho / 2 - 96, 112)
    cx, cy = ancho / 2, radio + 26
    escena = Escena(ancho, cy + radio + 28)
    n = len(variables)
    for anillo in (25, 50, 75, 100):
        puntos = [(cx + radio * anillo / 100 * math.sin(2 * math.pi * i / n), cy - radio * anillo / 100 * math.cos(2 * math.pi * i / n)) for i in range(n)]
        escena.poli(puntos, cerrado=True, stroke=GRIS_REJILLA, sw=1)
        escena.texto(cx + 3, cy - radio * anillo / 100 + 10, str(anillo), TAM - 3, fill=GRIS_EJE)
    for i, v in enumerate(variables):
        ang = 2 * math.pi * i / n
        escena.linea(cx, cy, cx + radio * math.sin(ang), cy - radio * math.cos(ang), GRIS_REJILLA, 1)
        lx, ly = cx + (radio + 10) * math.sin(ang), cy - (radio + 10) * math.cos(ang)
        ancla = "middle" if abs(math.sin(ang)) < 0.2 else ("start" if math.sin(ang) > 0 else "end")
        escena.texto(lx, ly + (-2 if math.cos(ang) > 0.3 else 11 if math.cos(ang) < -0.3 else 4), recortar(CORTO[v], 92, TAM - 1), TAM - 1, ancla)
    entradas = []

    def poligono(valores_0_100, color, relleno, trazo, nombre):
        puntos = [(cx + radio * valores_0_100[i] / 100 * math.sin(2 * math.pi * i / n), cy - radio * valores_0_100[i] / 100 * math.cos(2 * math.pi * i / n)) for i in range(n)]
        escena.poli(puntos, cerrado=True, fill=(relleno if relleno else None), stroke=color, sw=2, dash=trazo, titulo=nombre)

    nac = [(_normalizar_0_100((_nacional(datos, v) or {}).get("media", extremos[v][0]), v, _nacional(datos, v), extremos[v])) for v in variables]
    poligono(nac, COLOR_NACIONAL, None, [5, 3], "Promedio nacional")
    entradas.append(("Promedio nacional", COLOR_NACIONAL, [5, 3]))
    for indice, zona in enumerate(zonas):
        valores = []
        for v in variables:
            media = datos.media(zona["codigo"], v)
            valores.append(0.0 if media is None else _normalizar_0_100(media, v, _nacional(datos, v), extremos[v]))
        color = PALETA_GRAFICA[indice % len(PALETA_GRAFICA)]
        poligono(valores, color, None, TRAZOS[indice % len(TRAZOS)] if indice else None, etiqueta_zona(zona))
        entradas.append((etiqueta_zona(zona), color, TRAZOS[indice % len(TRAZOS)] if indice else None))
    usado = leyenda(escena, 0, cy + radio + 22, entradas, ancho)
    escena.alto = cy + radio + 22 + usado + 4
    return Resultado(titulo, escena.como_dict(), tabla, "Valores normalizados de 0 a 100 con el mínimo y el máximo de los municipios del país; a mayor distancia del centro, mayor valor.")


# ---- dispersión
def _escala_eje(valores: list[float], variable: str) -> dict:
    positivos = [v for v in valores if v > 0]
    usar_log = GRUPO_UNIDAD[variable] == "densidad" and len(positivos) == len(valores) and min(valores) > 0 and max(valores) / min(valores) > 100
    if usar_log:
        e0, e1 = math.floor(math.log10(min(valores))), math.ceil(math.log10(max(valores)))
        marcas = [10**e for e in range(e0, e1 + 1)]
        return {"log": True, "min": 10**e0, "max": 10**e1, "marcas": marcas, "dec": 0}
    minimo, maximo, marcas, dec = escala_agradable(min(valores), max(valores), 5)
    return {"log": False, "min": minimo, "max": maximo, "marcas": marcas, "dec": dec}


def _pos(valor: float, escala: dict) -> float:
    if escala["log"]:
        return (math.log10(valor) - math.log10(escala["min"])) / (math.log10(escala["max"]) - math.log10(escala["min"]))
    return (valor - escala["min"]) / (escala["max"] - escala["min"])


def dispersion(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    vx, vy = spec.variables[0], spec.variables[1]
    vt = spec.variables[2] if len(spec.variables) > 2 else None
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    puntos = []
    for zona in zonas:
        x, y = datos.media(zona["codigo"], vx), datos.media(zona["codigo"], vy)
        t = datos.media(zona["codigo"], vt) if vt else None
        if x is not None and y is not None:
            puntos.append((zona, x, y, t))
    tabla = {"encabezados": ["Zona", nombre_variable(vx), nombre_variable(vy)] + ([nombre_variable(vt)] if vt else []), "filas": [[etiqueta_zona(z), x, y] + ([t] if vt else []) for z, x, y, t in puntos]}
    if len(puntos) < 2:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    ex, ey = _escala_eje([p[1] for p in puntos], vx), _escala_eje([p[2] for p in puntos], vy)
    ml, mr, mt, alto_g = 56, 16, 12, 220
    x0, x1 = ml, ancho - mr
    escena = Escena(ancho, mt + alto_g + 44)
    for marca in ey["marcas"]:
        y = mt + alto_g - _pos(marca, ey) * alto_g
        escena.linea(x0, y, x1, y, GRIS_REJILLA, 1)
        escena.texto(x0 - 6, y + 4, num_es(marca, ey["dec"]), TAM - 1, "end", fill=GRIS_EJE)
    for marca in ex["marcas"]:
        x = x0 + _pos(marca, ex) * (x1 - x0)
        escena.linea(x, mt, x, mt + alto_g, GRIS_REJILLA, 1)
        escena.texto(x, mt + alto_g + 14, num_es(marca, ex["dec"]), TAM - 1, "middle", fill=GRIS_EJE)
    escena.linea(x0, mt + alto_g, x1, mt + alto_g, GRIS_EJE, 1)
    ux, uy = unidad_variable(vx), unidad_variable(vy)
    escena.texto((x0 + x1) / 2, mt + alto_g + 32, f"{nombre_variable(vx)}" + (f" ({ux})" if ux else "") + (" · escala log" if ex["log"] else ""), TAM - 1, "middle", fill=GRIS_EJE)
    escena.texto(10, mt + alto_g / 2, f"{nombre_variable(vy)}" + (f" ({uy})" if uy else "") + (" · log" if ey["log"] else ""), TAM - 1, "middle", fill=GRIS_EJE, rotar=-90)
    tams = [p[3] for p in puntos if p[3] is not None]
    for i, (zona, x, y, t) in enumerate(puntos):
        radio = 5.0
        if vt and t is not None and tams:
            radio = 4 + 10 * ((t - min(tams)) / (max(tams) - min(tams)) if max(tams) > min(tams) else 0.5)
        px, py = x0 + _pos(x, ex) * (x1 - x0), mt + alto_g - _pos(y, ey) * alto_g
        color = PALETA_GRAFICA[i % len(PALETA_GRAFICA)] if spec.modo == "comparar" else PALETA_GRAFICA[0]
        escena.circulo(px, py, radio, fill=mezclar(color, "#ffffff", 0.15), stroke=color, sw=1.2, titulo=f"{etiqueta_zona(zona)}: {fmt(x, vx, True)} · {fmt(y, vy, True)}" + (f" · {fmt(t, vt, True)}" if vt and t is not None else ""))
        if len(puntos) <= 12:
            escena.texto(px + radio + 3, py + 4, recortar(etiqueta_zona(zona), 90, TAM - 2), TAM - 2)
    nota = f"El tamaño del punto es proporcional a {nombre_variable(vt)}." if vt else ""
    return Resultado(titulo, escena.como_dict(), tabla, nota)


# ---- histograma y boxplot
def histograma(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    valores = [v for _, v in _valores(datos, datos.zonas_de(spec), variable)]
    titulo = titulo_auto(spec, datos)
    if len(valores) < 3:
        return Resultado(titulo, _sin_datos(ancho), {"encabezados": ["Rango", "Zonas"], "filas": []})
    k = max(3, min(10, math.ceil(1 + math.log2(len(valores)))))
    minimo, maximo, marcas, dec = escala_agradable(min(valores), max(valores), k + 1)
    paso = (maximo - minimo) / k
    conteos = [0] * k
    for valor in valores:
        conteos[min(k - 1, int((valor - minimo) / paso))] += 1
    tabla = {"encabezados": ["Desde", "Hasta", "Zonas"], "filas": [[minimo + i * paso, minimo + (i + 1) * paso, c] for i, c in enumerate(conteos)]}
    ml, mr, mt, alto_g = 40, 14, 14, 170
    x0, x1 = ml, ancho - mr
    tope = max(conteos)
    _, ymax, ymarcas, _ = escala_agradable(0, tope, 5, desde_cero=True)
    escena = Escena(ancho, mt + alto_g + 44)
    for marca in ymarcas:
        y = mt + alto_g - marca / ymax * alto_g
        escena.linea(x0, y, x1, y, GRIS_REJILLA, 1)
        escena.texto(x0 - 6, y + 4, num_es(marca, 0), TAM - 1, "end", fill=GRIS_EJE)
    ancho_bin = (x1 - x0) / k
    for i, c in enumerate(conteos):
        escena.rect(x0 + i * ancho_bin + 1, mt + alto_g - c / ymax * alto_g, ancho_bin - 2, c / ymax * alto_g, fill=PALETA_GRAFICA[0], titulo=f"{num_es(minimo + i * paso, dec)} a {num_es(minimo + (i + 1) * paso, dec)}: {c} zonas")
        if c:
            escena.texto(x0 + i * ancho_bin + ancho_bin / 2, mt + alto_g - c / ymax * alto_g - 3, str(c), TAM - 2, "middle")
    for i in range(k + 1):
        if k <= 6 or i % 2 == 0:
            escena.texto(x0 + i * ancho_bin, mt + alto_g + 14, num_es(minimo + i * paso, dec), TAM - 2, "middle", fill=GRIS_EJE)
    escena.linea(x0, mt + alto_g, x1, mt + alto_g, GRIS_EJE, 1)
    unidad = unidad_variable(variable)
    escena.texto((x0 + x1) / 2, mt + alto_g + 32, nombre_variable(variable) + (f" ({unidad})" if unidad else ""), TAM - 1, "middle", fill=GRIS_EJE)
    nacional = _nacional(datos, variable)
    if nacional and minimo <= nacional["media"] <= maximo:
        x = x0 + (nacional["media"] - minimo) / (maximo - minimo) * (x1 - x0)
        escena.linea(x, mt, x, mt + alto_g, COLOR_NACIONAL, 1.5, [4, 3])
        escena.texto(x, mt - 3, f"Prom. nacional {fmt(nacional['media'], variable)}", TAM - 2, "middle", fill=COLOR_NACIONAL)
    return Resultado(titulo, escena.como_dict(), tabla, f"Distribución entre las {len(valores)} zonas seleccionadas con dato.")


def boxplot(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    valores = sorted(v for _, v in _valores(datos, datos.zonas_de(spec), variable))
    titulo = titulo_auto(spec, datos)
    if len(valores) < 3:
        return Resultado(titulo, _sin_datos(ancho), {"encabezados": ["Estadístico", "Valor"], "filas": []})
    q1, mediana, q3 = statistics.quantiles(valores, n=4, method="inclusive")
    iqr = q3 - q1
    bajo, alto = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    dentro = [v for v in valores if bajo <= v <= alto]
    bigote_min, bigote_max = min(dentro), max(dentro)
    atipicos = [v for v in valores if v < bajo or v > alto]
    tabla = {"encabezados": ["Estadístico", "Valor"], "filas": [["Mínimo", valores[0]], ["Q1", q1], ["Mediana", mediana], ["Q3", q3], ["Máximo", valores[-1]], ["Promedio", statistics.fmean(valores)], ["Zonas", len(valores)]]}
    nacional = _nacional(datos, variable)
    candidatos = valores + ([nacional["media"]] if nacional else [])
    minimo, maximo, marcas, dec = escala_agradable(min(candidatos), max(candidatos), 6)
    ml, mr = 20, 20
    x0, x1 = ml, ancho - mr
    escena = Escena(ancho, 130)

    def px(v):
        return x0 + (v - minimo) / (maximo - minimo) * (x1 - x0)

    for marca in marcas:
        escena.linea(px(marca), 20, px(marca), 78, GRIS_REJILLA, 1)
        escena.texto(px(marca), 92, num_es(marca, dec), TAM - 1, "middle", fill=GRIS_EJE)
    escena.linea(px(bigote_min), 49, px(q1), 49, PALETA_GRAFICA[0], 1.5)
    escena.linea(px(q3), 49, px(bigote_max), 49, PALETA_GRAFICA[0], 1.5)
    escena.linea(px(bigote_min), 40, px(bigote_min), 58, PALETA_GRAFICA[0], 1.5)
    escena.linea(px(bigote_max), 40, px(bigote_max), 58, PALETA_GRAFICA[0], 1.5)
    escena.rect(px(q1), 32, px(q3) - px(q1), 34, fill=mezclar(PALETA_GRAFICA[0], "#ffffff", 0.7), stroke=PALETA_GRAFICA[0], sw=1.5, titulo=f"Q1 {fmt(q1, variable)} · Q3 {fmt(q3, variable)}")
    escena.linea(px(mediana), 32, px(mediana), 66, "#262524", 2.5)
    for atipico in atipicos:
        escena.circulo(px(atipico), 49, 3.2, stroke=PALETA_GRAFICA[1], sw=1.3, fill="#ffffff", titulo=f"Atípico: {fmt(atipico, variable, True)}")
    if nacional:
        escena.linea(px(nacional["media"]), 22, px(nacional["media"]), 76, COLOR_NACIONAL, 1.5, [4, 3])
        escena.texto(px(nacional["media"]), 14, f"Prom. nacional {fmt(nacional['media'], variable)}", TAM - 2, "middle", fill=COLOR_NACIONAL)
    unidad = unidad_variable(variable)
    escena.texto((x0 + x1) / 2, 114, f"{nombre_variable(variable)}" + (f" ({unidad})" if unidad else "") + f" · mediana {fmt(mediana, variable)}", TAM - 1, "middle", fill=GRIS_EJE)
    return Resultado(titulo, escena.como_dict(), tabla, f"Caja: Q1–Q3; línea gruesa: mediana; bigotes hasta 1,5 IQR; círculos: atípicos. {len(valores)} zonas.")


# ---- bullet y gauge
def _mejor(variable: str) -> str:
    return "alto" if variable in COBERTURAS else "bajo"


def bullet(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    zona = datos.zonas_de(spec)[0]
    valor = datos.media(zona["codigo"], variable)
    nacional = _nacional(datos, variable)
    titulo = titulo_auto(spec, datos)
    tabla = {"encabezados": ["Indicador", etiqueta_zona(zona), "Promedio nacional"], "filas": [[nombre_variable(variable), valor, (nacional or {}).get("media")]]}
    if valor is None:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    tope_candidatos = [valor] + ([nacional["media"], nacional["q3"], nacional["max"]] if nacional else [])
    base = 100.0 if GRUPO_UNIDAD[variable] == "porcentaje" else 1.0 if GRUPO_UNIDAD[variable] == "indice" else max(tope_candidatos)
    _, maximo, marcas, dec = escala_agradable(0, base if GRUPO_UNIDAD[variable] != "densidad" else min(max(tope_candidatos), (nacional or {}).get("q3", max(tope_candidatos)) * 3 if nacional else max(tope_candidatos)), 6, desde_cero=True)
    x0, x1 = 24, ancho - 24
    escena = Escena(ancho, 112)

    def px(v):
        return x0 + max(0.0, min(1.0, v / maximo)) * (x1 - x0)

    if nacional:
        cortes = [0.0, nacional["q1"], nacional["q3"], maximo]
        tonos = ["#d9d4ca", "#e8e4dc", "#f3f0ea"] if _mejor(variable) == "bajo" else ["#f3f0ea", "#e8e4dc", "#d9d4ca"]
        for i in range(3):
            escena.rect(px(cortes[i]), 30, px(cortes[i + 1]) - px(cortes[i]), 30, fill=tonos[i], titulo=["Primer cuartil (municipios)", "Rango central", "Último cuartil"][i])
    else:
        escena.rect(x0, 30, x1 - x0, 30, fill="#ece8e0")
    escena.rect(x0, 38, px(valor) - x0, 14, fill=PALETA_GRAFICA[0], titulo=f"{etiqueta_zona(zona)}: {fmt(valor, variable, True)}")
    if nacional:
        escena.linea(px(nacional["media"]), 32, px(nacional["media"]), 58, "#262524", 2.5)
        escena.texto(px(nacional["media"]), 24, f"Prom. nacional {fmt(nacional['media'], variable)}", TAM - 2, "middle")
    for marca in marcas:
        escena.texto(px(marca), 76, num_es(marca, dec), TAM - 1, "middle", fill=GRIS_EJE)
    unidad = unidad_variable(variable)
    escena.texto(x0, 14, f"{etiqueta_zona(zona)}: {fmt(valor, variable, True)}", TAM, "start", negrita=True)
    escena.texto(x1, 100, nombre_variable(variable) + (f" ({unidad})" if unidad else ""), TAM - 1, "end", fill=GRIS_EJE)
    return Resultado(titulo, escena.como_dict(), tabla, "Barra: valor de la zona; marca negra: promedio nacional; fondo: cuartiles de los municipios del país.")


def gauge(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    zona = datos.zonas_de(spec)[0]
    valor = datos.media(zona["codigo"], variable)
    nacional = _nacional(datos, variable)
    titulo = titulo_auto(spec, datos)
    tabla = {"encabezados": ["Indicador", etiqueta_zona(zona), "Promedio nacional"], "filas": [[nombre_variable(variable), valor, (nacional or {}).get("media")]]}
    if valor is None:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    maximo = 1.0 if GRUPO_UNIDAD[variable] == "indice" else 100.0
    radio = min(ancho / 2 - 20, 130)
    cx, cy = ancho / 2, radio + 18
    escena = Escena(ancho, cy + 54)
    colores = ["#2e933c", "#f3a712", "#d81159"] if _mejor(variable) == "bajo" else ["#d81159", "#f3a712", "#2e933c"]
    r_ext, r_int = radio, radio * 0.62
    for i in range(3):
        a0, a1 = math.pi + i * math.pi / 3, math.pi + (i + 1) * math.pi / 3
        pasos = 14
        externo = [(cx + r_ext * math.cos(a0 + (a1 - a0) * j / pasos), cy + r_ext * math.sin(a0 + (a1 - a0) * j / pasos)) for j in range(pasos + 1)]
        interno = [(cx + r_int * math.cos(a1 - (a1 - a0) * j / pasos), cy + r_int * math.sin(a1 - (a1 - a0) * j / pasos)) for j in range(pasos + 1)]
        escena.poli(externo + interno, cerrado=True, fill=colores[i], stroke="#ffffff", sw=1.5)
    angulo = math.pi + max(0.0, min(1.0, valor / maximo)) * math.pi
    escena.linea(cx, cy, cx + (r_ext - 6) * math.cos(angulo), cy + (r_ext - 6) * math.sin(angulo), "#262524", 3)
    escena.circulo(cx, cy, 6, fill="#262524")
    if nacional:
        an = math.pi + max(0.0, min(1.0, nacional["media"] / maximo)) * math.pi
        escena.linea(cx + (r_int - 4) * math.cos(an), cy + (r_int - 4) * math.sin(an), cx + (r_ext + 5) * math.cos(an), cy + (r_ext + 5) * math.sin(an), "#262524", 1.5, [3, 2])
    escena.texto(cx, cy + 22, fmt(valor, variable, True), 17, "middle", negrita=True)
    escena.texto(cx - r_ext, cy + 14, "0", TAM - 1, "middle", fill=GRIS_EJE)
    escena.texto(cx + r_ext, cy + 14, num_es(maximo, 0), TAM - 1, "middle", fill=GRIS_EJE)
    if nacional:
        escena.texto(cx, cy + 40, f"Promedio nacional: {fmt(nacional['media'], variable, True)}", TAM - 1, "middle", fill=COLOR_NACIONAL)
    return Resultado(titulo, escena.como_dict(), tabla, f"{etiqueta_zona(zona)}. " + ("Verde = mejor (valores bajos)." if _mejor(variable) == "bajo" else "Verde = mejor (valores altos)."))


# ---- dumbbell
def dumbbell(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    va, vb = spec.variables
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    filas = []
    for zona in zonas:
        a, b = datos.media(zona["codigo"], va), datos.media(zona["codigo"], vb)
        if a is not None and b is not None:
            filas.append((zona, a, b))
    filas.sort(key=lambda f: -abs(f[1] - f[2]))
    tabla = {"encabezados": ["Zona", nombre_variable(va), nombre_variable(vb), "Brecha"], "filas": [[etiqueta_zona(z), a, b, a - b] for z, a, b in filas]}
    if not filas:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    filas, nota = _limitar(filas, MAX_FILAS)
    ml = min(max(ancho_texto(etiqueta_zona(z)) for z, _, _ in filas) + 10, ancho * 0.36)
    x0, x1 = ml + 8, ancho - 64
    todos = [v for _, a, b in filas for v in (a, b)]
    minimo, maximo, marcas, dec = escala_agradable(min(todos), max(todos), 5)
    mt, alto_fila = 12, 22
    alto_g = len(filas) * alto_fila
    escena = Escena(ancho, mt + alto_g + 30 + 24)

    def px(v):
        return x0 + (v - minimo) / (maximo - minimo) * (x1 - x0)

    for marca in marcas:
        escena.linea(px(marca), mt - 4, px(marca), mt + alto_g, GRIS_REJILLA, 1)
        escena.texto(px(marca), mt + alto_g + 13, num_es(marca, dec), TAM - 1, "middle", fill=GRIS_EJE)
    for i, (zona, a, b) in enumerate(filas):
        y = mt + i * alto_fila + 11
        escena.texto(ml, y + 4, recortar(etiqueta_zona(zona), ml - 6), TAM - 1, "end")
        escena.linea(px(a), y, px(b), y, "#b9b2a6", 3)
        escena.circulo(px(a), y, 5.5, fill=PALETA_GRAFICA[0], titulo=f"{etiqueta_zona(zona)} · {nombre_variable(va)}: {fmt(a, va, True)}")
        escena.circulo(px(b), y, 5.5, fill=PALETA_GRAFICA[1], titulo=f"{etiqueta_zona(zona)} · {nombre_variable(vb)}: {fmt(b, vb, True)}")
        escena.texto(max(px(a), px(b)) + 10, y + 4, f"{'+' if a - b >= 0 else '−'}{num_es(abs(a - b), decimales_de(va))}", TAM - 2, fill=GRIS_EJE)
    y_ley = mt + alto_g + 28
    usado = leyenda_cuadros(escena, 0, y_ley, [(nombre_variable(va), PALETA_GRAFICA[0]), (nombre_variable(vb), PALETA_GRAFICA[1])], ancho)
    escena.alto = y_ley + usado + 4
    return Resultado(titulo, escena.como_dict(), tabla, nota or "Ordenado por tamaño de la brecha (primera variable menos segunda).")


# ---- donut
def donut(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    zona = datos.zonas_de(spec)[0]
    cuant, cual = datos.media(zona["codigo"], DEFICITS[0]), datos.media(zona["codigo"], DEFICITS[1])
    titulo = titulo_auto(spec, datos)
    tabla = {"encabezados": ["Componente", "Valor (%)", "Proporción (%)"], "filas": []}
    if cuant is None or cual is None or (cuant + cual) <= 0:
        return Resultado(titulo, _sin_datos(ancho, "Faltan datos de alguno de los dos déficits."), tabla)
    total = cuant + cual
    partes = [(nombre_variable(DEFICITS[0]), cuant, PALETA_GRAFICA[1]), (nombre_variable(DEFICITS[1]), cual, PALETA_GRAFICA[3])]
    tabla["filas"] = [[n, v, v / total * 100] for n, v, _ in partes]
    radio = min(ancho / 4, 90)
    cx, cy = radio + 30, radio + 16
    escena = Escena(ancho, 2 * radio + 32)
    inicio = -math.pi / 2
    for nombre, valor, color in partes:
        barrido = valor / total * 2 * math.pi
        pasos = max(8, int(barrido / (2 * math.pi) * 60))
        externo = [(cx + radio * math.cos(inicio + barrido * j / pasos), cy + radio * math.sin(inicio + barrido * j / pasos)) for j in range(pasos + 1)]
        interno = [(cx + radio * 0.58 * math.cos(inicio + barrido * (1 - j / pasos)), cy + radio * 0.58 * math.sin(inicio + barrido * (1 - j / pasos))) for j in range(pasos + 1)]
        escena.poli(externo + interno, cerrado=True, fill=color, stroke="#ffffff", sw=1.5, titulo=f"{nombre}: {num_es(valor, 1)} % ({num_es(valor / total * 100, 1)} % del total)")
        inicio += barrido
    escena.texto(cx, cy - 2, num_es(total, 1) + " %", 16, "middle", negrita=True)
    escena.texto(cx, cy + 14, "déficit total", TAM - 1, "middle", fill=GRIS_EJE)
    lx = cx + radio + 34
    for i, (nombre, valor, color) in enumerate(partes):
        y = cy - 24 + i * 44
        escena.rect(lx, y, 12, 12, fill=color)
        escena.texto(lx + 18, y + 10, recortar(nombre, ancho - lx - 24), TAM)
        escena.texto(lx + 18, y + 26, f"{num_es(valor, 1)} % · {num_es(valor / total * 100, 1)} % del total", TAM - 1, fill=GRIS_EJE)
    return Resultado(titulo, escena.como_dict(), tabla, f"{etiqueta_zona(zona)}. El total es la suma de ambos déficits.")


# ---- treemap
def _squarify(valores: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Treemap 'squarified' (Bruls et al.); `valores` ordenados de mayor a menor y > 0."""
    total = sum(valores)
    areas = [v / total * w * h for v in valores]
    rects: list[tuple[float, float, float, float]] = []

    def peor(fila, lado):
        s = sum(fila)
        return max(max(lado * lado * a / (s * s), (s * s) / (lado * lado * a)) for a in fila)

    def colocar(fila, x, y, w, h):
        s = sum(fila)
        if w >= h:
            ancho_col = s / h
            cursor = y
            for a in fila:
                alto = a / ancho_col
                rects.append((x, cursor, ancho_col, alto))
                cursor += alto
            return x + ancho_col, y, w - ancho_col, h
        alto_fila = s / w
        cursor = x
        for a in fila:
            ancho_celda = a / alto_fila
            rects.append((cursor, y, ancho_celda, alto_fila))
            cursor += ancho_celda
        return x, y + alto_fila, w, h - alto_fila

    fila: list[float] = []
    restantes = list(areas)
    while restantes:
        lado = min(w, h)
        candidato = restantes[0]
        if not fila or peor(fila + [candidato], lado) <= peor(fila, lado):
            fila.append(restantes.pop(0))
        else:
            x, y, w, h = colocar(fila, x, y, w, h)
            fila = []
    if fila:
        colocar(fila, x, y, w, h)
    return rects


def treemap(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variable = spec.variables[0]
    pares = sorted([(z, v) for z, v in _valores(datos, datos.zonas_de(spec), variable) if v > 0], key=lambda p: -p[1])
    titulo = titulo_auto(spec, datos)
    tabla = {"encabezados": ["Zona", nombre_variable(variable)], "filas": [[etiqueta_zona(z), v] for z, v in pares]}
    if not pares:
        return Resultado(titulo, _sin_datos(ancho), tabla)
    mostrados, nota = _limitar(pares, 40)
    alto = min(ancho * 0.62, 300)
    escena = Escena(ancho, alto + 28)
    rects = _squarify([v for _, v in mostrados], 0, 0, ancho, alto)
    maximo = max(v for _, v in mostrados)
    minimo = min(v for _, v in mostrados)
    for (zona, valor), (x, y, w, h) in zip(mostrados, rects):
        t = 0.5 if maximo == minimo else (valor - minimo) / (maximo - minimo)
        color = rampa(t)
        escena.rect(x, y, w, h, fill=color, stroke="#ffffff", sw=1.5, titulo=f"{etiqueta_zona(zona)}: {fmt(valor, variable, True)}")
        if w > 54 and h > 30:
            claro = t < 0.55
            escena.texto(x + 5, y + 14, recortar(etiqueta_zona(zona), w - 10, TAM - 1), TAM - 1, negrita=True, fill="#262524" if claro else "#ffffff")
            escena.texto(x + 5, y + 28, fmt(valor, variable, True), TAM - 2, fill="#262524" if claro else "#ffffff")
    unidad = unidad_variable(variable)
    escena.texto(ancho, alto + 20, f"Área y color proporcionales a {nombre_variable(variable)}" + (f" ({unidad})" if unidad else ""), TAM - 2, "end", fill=GRIS_EJE)
    return Resultado(titulo, escena.como_dict(), tabla, nota)


# ---- correlaciones
def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _color_divergente(r: float) -> str:
    return mezclar("#ffffff", "#c8302a", r) if r >= 0 else mezclar("#ffffff", "#2b6cb0", -r)


def correlaciones(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variables = list(spec.variables)
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    n = len(variables)
    matriz: list[list[float | None]] = []
    for va in variables:
        fila = []
        for vb in variables:
            pares = [(datos.media(z["codigo"], va), datos.media(z["codigo"], vb)) for z in zonas]
            pares = [(a, b) for a, b in pares if a is not None and b is not None]
            fila.append(1.0 if va == vb else pearson([a for a, _ in pares], [b for _, b in pares]))
        matriz.append(fila)
    tabla = {"encabezados": [""] + [nombre_variable(v) for v in variables], "filas": [[nombre_variable(variables[i])] + matriz[i] for i in range(n)]}
    ml = min(max(ancho_texto(CORTO[v]) for v in variables) + 10, ancho * 0.34)
    lado = min((ancho - ml - 60) / n, 70)
    escena = Escena(ancho, 40 + n * lado + 30)
    for i in range(n):
        escena.texto(ml, 40 + i * lado + lado / 2 + 4, recortar(CORTO[variables[i]], ml - 6), TAM - 1, "end")
        escena.texto(ml + i * lado + lado / 2, 32, recortar(CORTO[variables[i]], lado, TAM - 2), TAM - 2, "middle", fill=GRIS_EJE)
        for j in range(n):
            r = matriz[i][j]
            x, y = ml + j * lado, 40 + i * lado
            if r is None:
                escena.rect(x, y, lado - 1, lado - 1, fill="#ece8e0", titulo="Sin correlación calculable")
                continue
            escena.rect(x, y, lado - 1, lado - 1, fill=_color_divergente(r), titulo=f"{nombre_variable(variables[i])} · {nombre_variable(variables[j])}: r = {num_es(r, 2)}")
            escena.texto(x + lado / 2, y + lado / 2 + 4, num_es(r, 2), TAM - 1, "middle", fill="#ffffff" if abs(r) > 0.65 else TEXTO)
    bx = ml + n * lado + 14
    for k in range(20):
        escena.rect(bx, 40 + k * (n * lado) / 20, 10, (n * lado) / 20 + 0.5, fill=_color_divergente(1 - k / 19 * 2))
    escena.texto(bx + 14, 48, "+1", TAM - 2)
    escena.texto(bx + 14, 40 + n * lado, "−1", TAM - 2)
    return Resultado(titulo, escena.como_dict(), tabla, f"Correlación de Pearson entre los valores de las {len(zonas)} zonas seleccionadas (promedio del periodo).")


# ---- paneles (small multiples)
def paneles(spec, datos: DatosGraficas, ancho: float) -> Resultado:
    variables = list(spec.variables)
    zonas = datos.zonas_de(spec)
    titulo = titulo_auto(spec, datos)
    tabla = _tabla_zonas(datos, zonas, variables)
    columnas = 2 if ancho >= 420 else 1
    ancho_panel = (ancho - 16 * (columnas - 1)) / columnas
    maximo_filas = 8
    alto_fila = 17
    filas_paneles = []
    for v in variables:
        pares = sorted(_valores(datos, zonas, v), key=lambda p: -p[1])[:maximo_filas]
        filas_paneles.append(pares)
    alto_panel = 24 + maximo_filas * alto_fila + 24
    n_filas = math.ceil(len(variables) / columnas)
    escena = Escena(ancho, n_filas * alto_panel)
    for indice, (v, pares) in enumerate(zip(variables, filas_paneles)):
        px0 = (indice % columnas) * (ancho_panel + 16)
        py0 = (indice // columnas) * alto_panel
        unidad = unidad_variable(v)
        escena.texto(px0, py0 + 12, nombre_variable(v) + (f" ({unidad})" if unidad else ""), TAM - 1, negrita=True)
        if not pares:
            escena.texto(px0, py0 + 40, "Sin datos", TAM - 1, fill=GRIS_EJE)
            continue
        ml = min(max(ancho_texto(etiqueta_zona(z), TAM - 2) for z, _ in pares) + 6, ancho_panel * 0.42)
        x0, x1 = px0 + ml + 4, px0 + ancho_panel - 38
        _, maximo, _, _ = escala_agradable(0, max(valor for _, valor in pares), 4, desde_cero=True)
        for i, (zona, valor) in enumerate(pares):
            y = py0 + 22 + i * alto_fila
            escena.texto(px0 + ml, y + 11, recortar(etiqueta_zona(zona), ml - 4, TAM - 2), TAM - 2, "end")
            escena.rect(x0, y + 2, valor / maximo * (x1 - x0), 11, fill=PALETA_GRAFICA[indice % len(PALETA_GRAFICA)], titulo=f"{etiqueta_zona(zona)}: {fmt(valor, v, True)}")
            escena.texto(x0 + valor / maximo * (x1 - x0) + 3, y + 11, fmt(valor, v), TAM - 3)
        escena.linea(x0, py0 + 20, x0, py0 + 22 + len(pares) * alto_fila, GRIS_EJE, 1)
    nota = f"Cada panel tiene su propia escala. Se muestran hasta {maximo_filas} zonas por panel." if any(len(_valores(datos, zonas, v)) > maximo_filas for v in variables) else "Cada panel tiene su propia escala."
    return Resultado(titulo, escena.como_dict(), tabla, nota)


CONSTRUCTORES = {
    "coropletico": coropletico,
    "barras_ranking": ranking,
    "barras_agrupadas": barras_agrupadas,
    "barras_apiladas": barras_apiladas,
    "lineas": lineas,
    "radar": radar,
    "dispersion": dispersion,
    "histograma": histograma,
    "boxplot": boxplot,
    "bullet": bullet,
    "gauge": gauge,
    "dumbbell": dumbbell,
    "donut": donut,
    "treemap": treemap,
    "correlaciones": correlaciones,
    "paneles": paneles,
}
assert set(CONSTRUCTORES) == set(TIPOS), "cada tipo del catálogo necesita su constructor"


def construir_escena(spec, datos: DatosGraficas, ancho: float = 560) -> Resultado:
    """Escena de una gráfica; un fallo del constructor produce una escena de aviso (no tumba el reporte)."""
    try:
        return CONSTRUCTORES[spec.tipo](spec, datos, ancho)
    except Exception:  # noqa: BLE001 - una gráfica mala no debe impedir generar el reporte
        import logging

        logging.getLogger(__name__).exception("No se pudo construir la gráfica %s (%s).", spec.id, spec.tipo)
        return Resultado(titulo_auto(spec, datos), _sin_datos(ancho, "No se pudo construir esta gráfica."), {"encabezados": [], "filas": []})
