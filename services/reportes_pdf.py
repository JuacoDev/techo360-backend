"""Plantillas PDF del reporte: APA 7.ª edición e IEEE.

Cada plantilla replica las reglas de maquetación de su estilo:

APA 7 (informe en español)
    Carta, márgenes de 1 in, Times New Roman 12 pt, interlineado doble en todo el texto,
    alineación a la izquierda (sin justificar), sangría de primera línea de 0,5 in, número de
    página arriba a la derecha desde la portada, portada con el título en negrita centrado en la
    mitad superior, título repetido en la primera página del texto, introducción sin encabezado,
    encabezados de niveles 1 (centrado, negrita), 2 (izquierda, negrita) y 3 (izquierda, negrita
    cursiva), tablas con "Tabla n" en negrita y título en cursiva encima, solo líneas
    horizontales (superior, bajo el encabezado e inferior) y "Nota." debajo, figuras con
    "Figura n" en negrita y título en cursiva encima, citas autor-fecha y lista de Referencias en
    página aparte con sangría francesa de 0,5 in y orden alfabético.

IEEE
    Carta, márgenes de 0,625 in (laterales), 0,75 in (superior) y 1 in (inferior), dos columnas de
    3,5 in separadas 0,25 in, Times 10 pt justificado, título de 24 pt y autores de 11 pt a todo el
    ancho en la primera página, resumen y palabras clave en 9 pt negrita, encabezados de sección
    en versalitas con numeración romana ("I. INTRODUCCIÓN"), de subsección en cursiva con letra
    ("A. …"), tablas con "TABLA I" y título en versalitas encima y texto de 8 pt, figuras con
    "Fig. 1." debajo, citas numeradas entre corchetes en orden de aparición y REFERENCIAS
    numeradas. Sin número de página (lo asigna el editor al maquetar).

Límites (datos que la aplicación no tiene): la portada APA usa el nombre del usuario como autor y
"Techo 360" como afiliación (no hay curso ni docente); las URL de las fuentes son las de sus
portales (no hay fecha de publicación por conjunto de datos: "s. f.").
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics import renderPDF
from reportlab.graphics.charts.legends import LineLegend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.textlabels import Label
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.attrmap import AttrMap, AttrMapValue
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.validators import isAnything
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    FrameBreak,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageBreakIfNotEmpty,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from schemas.reporte import ReporteGenerarRequest
from services.origen import es_proyeccion
from services.reportes_escena_pdf import EscenaPDF
from services.reportes_service import (
    MAX_SERIES_LEYENDA,
    PALETA_GRAFICA,
    VARIABLES_REPORTE,
    _valores_por_grafica,
)

logger = logging.getLogger(__name__)

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
# Líneas distintas además del color: el reporte sigue siendo legible impreso en blanco y negro.
TRAZOS = [None, (5, 2), (1, 2), (5, 2, 1, 2), (2, 2), (8, 3), (3, 1), (6, 2, 2, 2), (1, 1), (4, 4)]
DECIMALES = {"score_privacion": 2, "densidad_poblacional": 1}  # el resto (porcentajes): 1
ABREVIATURAS_IEEE = {
    "score_privacion": "Score",
    "cobertura_acueducto": "Acued. (%)",
    "cobertura_alcantarillado": "Alcant. (%)",
    "deficit_habitacional_cuantitativo": "Déf. cuant. (%)",
    "deficit_habitacional_cualitativo": "Déf. cual. (%)",
    "densidad_poblacional": "Dens. (hab/km²)",
}


# ---------------------------------------------------------------- utilidades comunes
def fecha_larga(dia: date) -> str:
    return f"{dia.day} de {MESES[dia.month - 1]} de {dia.year}"


def fecha_corta(dia: date) -> str:
    return f"{dia.day} {MESES[dia.month - 1][:3]}. {dia.year}"


def formato_valor(valor: float | None, variable: str) -> str:
    """Decimales constantes por columna; coma decimal y punto de miles (como la interfaz); n.d. = sin dato."""
    if valor is None:
        return "n.d."
    decimales = DECIMALES.get(variable, 1)
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "\0").replace(".", ",").replace("\0", ".")


def romano(numero: int) -> str:
    valores = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    resultado = ""
    for valor, letra in valores:
        while numero >= valor:
            resultado += letra
            numero -= valor
    return resultado


def letra(indice: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA (las subsecciones IEEE llevan letra)."""
    texto = ""
    indice += 1
    while indice > 0:
        indice, resto = divmod(indice - 1, 26)
        texto = chr(65 + resto) + texto
    return texto


def versalitas(texto: str, grande: float, chico: float) -> str:
    """Marcado de versalitas: la inicial de cada palabra en `grande` y el resto en mayúsculas de `chico`."""
    partes = []
    for palabra in texto.split():
        mayus = palabra.upper()
        partes.append(f'<font size="{grande}">{escape(mayus[0])}</font><font size="{chico}">{escape(mayus[1:])}</font>')
    return " ".join(partes)


def parrafos(texto: str) -> list[str]:
    return [linea.strip() for linea in texto.replace("\r\n", "\n").split("\n") if linea.strip()]


def etiqueta_de(zona: dict) -> str:
    return zona.get("etiqueta") or zona["nombre"]


def plural(n: int, singular: str, plural_: str) -> str:
    return singular if n == 1 else plural_


def indexar_series(series: list[dict]) -> dict[tuple[str, str], dict[tuple[int, str], float | None]]:
    indice: dict = defaultdict(dict)
    for fila in series:
        indice[(fila["nivel"], fila["codigo"])][(fila["anio"], fila["variable"])] = fila["valor"]
    return indice


# Fuentes de datos citables, detectadas por el origen de las filas del reporte.
FUENTES = {
    "terridata": {
        "autor": "Departamento Nacional de Planeación",
        "abrev": "DNP",
        "titulo": "TerriData: Sistema de estadísticas territoriales",
        "url": "https://terridata.dnp.gov.co/",
        "tipo": "externa",
        "descripcion": "TerriData",
    },
    "dane": {
        "autor": "Departamento Administrativo Nacional de Estadística",
        "abrev": "DANE",
        "titulo": "Proyecciones de población",
        "url": "https://www.dane.gov.co/",
        "tipo": "externa",
        "descripcion": "las proyecciones de población del DANE",
    },
    "techo360": {
        "autor": "Techo 360",
        "abrev": None,
        "titulo": "Proyecciones de indicadores de vivienda y territorio",
        "url": None,
        "tipo": "propia",
        "descripcion": "las proyecciones de Techo 360",
    },
}


def detectar_fuentes(series: list[dict]) -> list[str]:
    """Claves de FUENTES presentes en los orígenes de las series (orden fijo, sin repetir)."""
    textos = {str(fila.get("origen") or "") for fila in series}
    presentes: set[str] = set()
    for texto in textos:
        minuscula = texto.lower()
        if "terridata" in minuscula and not es_proyeccion(texto.strip()):
            presentes.add("terridata")
        if "dane" in minuscula:
            presentes.add("dane")
        if "proyeccion" in minuscula or "ultimo-valor" in minuscula:
            presentes.add("techo360")
    return [clave for clave in ("terridata", "dane", "techo360") if clave in presentes]


def grupos_de_zonas(zonas: list[dict]) -> tuple[list[dict], list[dict]]:
    return (
        [z for z in zonas if z["nivel"] == "departamento"],
        [z for z in zonas if z["nivel"] == "municipio"],
    )


# ---------------------------------------------------------------- gráfica
class LeyendaConTrazos(LineLegend):
    """LineLegend cuyas muestras repiten el trazo (continuo, rayas, puntos…) de cada línea del gráfico,
    para distinguir las zonas también en una impresión en blanco y negro."""

    _attrMap = AttrMap(BASE=LineLegend, trazos_por_color=AttrMapValue(isAnything, desc="trazo por color de línea"))
    trazos_por_color: dict = {}

    def _defaultSwatch(self, x, thisy, dx, dy, fillColor, strokeWidth, strokeColor):
        muestra = super()._defaultSwatch(x, thisy, dx, dy, fillColor, strokeWidth, strokeColor)
        muestra.strokeDashArray = self.trazos_por_color.get(fillColor.hexval())
        return muestra


class GraficaLineasPDF(Flowable):
    """Líneas por zona a lo largo de los años: ejes titulados, un color y un trazo por zona y leyenda."""

    def __init__(
        self,
        variable: str,
        anios: list[str],
        datos: list[tuple[str, list[float | None]]],
        ancho: float,
        alto_grafica: float,
        fuente: str = "Times-Roman",
        tamano: float = 8,
        columnas_leyenda: int = 2,
        largo_nombre: int = 48,
    ):
        super().__init__()
        self.variable = variable
        self.anios = anios
        self.datos = datos[:MAX_SERIES_LEYENDA]
        self.recortada = len(datos) > MAX_SERIES_LEYENDA
        self.fuente = fuente
        self.tamano = tamano
        self.columnas = max(1, columnas_leyenda)
        self.largo_nombre = largo_nombre
        filas = math.ceil(len(self.datos) / self.columnas)
        self.alto_leyenda = (tamano + 5) * filas + 8
        self.width = ancho
        self.height = alto_grafica + self.alto_leyenda + 48

    def wrap(self, ancho_disponible, alto_disponible):
        return self.width, self.height

    def draw(self):
        # Una gráfica que no se pueda dibujar no debe tumbar el reporte entero.
        try:
            self._dibujar()
        except Exception:
            logger.exception("No se pudo dibujar la gráfica de %s.", self.variable)
            self.canv.setFont("Helvetica-Oblique", 9)
            self.canv.setFillColor(colors.HexColor("#B3261E"))
            self.canv.drawString(0, self.height / 2, "No se pudo dibujar esta figura; los datos están en las tablas.")

    def _dibujar(self):
        etiqueta, unidad = VARIABLES_REPORTE[self.variable]
        dibujo = Drawing(self.width, self.height)
        grafica = HorizontalLineChart()
        grafica.x = 52
        grafica.y = self.alto_leyenda + 42
        grafica.width = self.width - 66
        grafica.height = self.height - self.alto_leyenda - 56
        grafica.categoryAxis.categoryNames = self.anios
        grafica.categoryAxis.labels.fontName = self.fuente
        grafica.categoryAxis.labels.fontSize = self.tamano
        grafica.valueAxis.labels.fontName = self.fuente
        grafica.valueAxis.labels.fontSize = self.tamano
        grafica.valueAxis.valueMin = None
        grafica.data = [
            tuple(float(valor) if valor is not None else None for valor in valores)
            for _, valores in self.datos
        ]
        for indice in range(len(self.datos)):
            linea = grafica.lines[indice]
            linea.strokeColor = colors.HexColor(PALETA_GRAFICA[indice % len(PALETA_GRAFICA)])
            linea.strokeWidth = 1.5
            linea.strokeDashArray = TRAZOS[indice % len(TRAZOS)]
            linea.symbol = None
        dibujo.add(grafica)

        eje_x = Label()
        eje_x.setOrigin(grafica.x + grafica.width / 2, self.alto_leyenda + 26)
        eje_x.boxAnchor = "n"
        eje_x.fontName = self.fuente
        eje_x.fontSize = self.tamano
        eje_x.setText("Año")
        dibujo.add(eje_x)

        eje_y = Label()
        eje_y.setOrigin(10, grafica.y + grafica.height / 2)
        eje_y.angle = 90
        eje_y.boxAnchor = "c"
        eje_y.fontName = self.fuente
        eje_y.fontSize = self.tamano
        eje_y.setText(f"{etiqueta}" + (f" ({unidad})" if unidad else ""))
        dibujo.add(eje_y)

        leyenda = LeyendaConTrazos()
        leyenda.trazos_por_color = {
            colors.HexColor(PALETA_GRAFICA[i % len(PALETA_GRAFICA)]).hexval(): TRAZOS[i % len(TRAZOS)]
            for i in range(len(self.datos))
        }
        leyenda.x = 6
        leyenda.y = self.alto_leyenda - 4
        leyenda.alignment = "right"
        leyenda.fontName = self.fuente
        leyenda.fontSize = self.tamano
        leyenda.dx = 16
        leyenda.dy = 2
        leyenda.dxTextSpace = 4
        leyenda.deltax = (self.width - 12) / self.columnas
        leyenda.deltay = self.tamano + 4
        leyenda.columnMaximum = math.ceil(len(self.datos) / self.columnas)
        leyenda.colorNamePairs = [
            (colors.HexColor(PALETA_GRAFICA[indice % len(PALETA_GRAFICA)]), nombre[: self.largo_nombre])
            for indice, (nombre, _) in enumerate(self.datos)
        ]
        dibujo.add(leyenda)
        renderPDF.draw(dibujo, self.canv, 0, 0)


def _datos_de_figura(zonas, series, variable, solicitud):
    anios, datos = _valores_por_grafica(
        zonas, series, variable, solicitud.periodo.anio_inicio, solicitud.periodo.anio_fin
    )
    return anios, datos


# ================================================================= APA 7
def crear_pdf_apa(
    ruta: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
    autor: str | None = None,
    escenas: list | None = None,
) -> None:
    ancho_texto = letter[0] - 2 * inch
    base = dict(fontName="Times-Roman", fontSize=12, leading=24, alignment=TA_LEFT, textColor=colors.black)
    estilo = {
        "cuerpo": ParagraphStyle("apa_cuerpo", firstLineIndent=0.5 * inch, **base),
        "plano": ParagraphStyle("apa_plano", **base),
        "centrado": ParagraphStyle("apa_centrado", **{**base, "alignment": TA_CENTER}),
        "titulo": ParagraphStyle("apa_titulo", **{**base, "fontName": "Times-Bold", "alignment": TA_CENTER}),
        "h1": ParagraphStyle("apa_h1", keepWithNext=1, **{**base, "fontName": "Times-Bold", "alignment": TA_CENTER}),
        "h2": ParagraphStyle("apa_h2", keepWithNext=1, **{**base, "fontName": "Times-Bold"}),
        "h3": ParagraphStyle("apa_h3", keepWithNext=1, **{**base, "fontName": "Times-BoldItalic"}),
        "rotulo": ParagraphStyle("apa_rotulo", keepWithNext=1, **{**base, "fontName": "Times-Bold"}),
        "rotulo_titulo": ParagraphStyle("apa_rotulo_titulo", keepWithNext=1, **{**base, "fontName": "Times-Italic"}),
        "nota": ParagraphStyle("apa_nota", **{**base, "fontSize": 10, "leading": 14}),
        "celda": ParagraphStyle("apa_celda", **{**base, "fontSize": 10, "leading": 12, "alignment": TA_CENTER}),
        "celda_encabezado": ParagraphStyle("apa_celda_enc", **{**base, "fontName": "Times-Bold", "fontSize": 9, "leading": 11, "alignment": TA_CENTER}),
        "referencia": ParagraphStyle("apa_ref", leftIndent=0.5 * inch, firstLineIndent=-0.5 * inch, **base),
    }
    anio_i, anio_f = solicitud.periodo.anio_inicio, solicitud.periodo.anio_fin
    periodo = f"{anio_i}–{anio_f}" if anio_i != anio_f else str(anio_i)
    hoy = date.today()
    fuentes = detectar_fuentes(series)
    citados: set[str] = set()

    def cita(clave: str) -> str:
        """Cita parentética autor-fecha; la primera vez lleva la abreviatura entre corchetes."""
        fuente = FUENTES[clave]
        if clave == "techo360":
            return f"(Techo 360, {hoy.year})"
        if clave in citados:
            return f"({fuente['abrev']}, s. f.)"
        citados.add(clave)
        return f"({fuente['autor']} [{fuente['abrev']}], s. f.)"

    def cita_corta(clave: str) -> str:
        fuente = FUENTES[clave]
        if clave == "techo360":
            return f"(Techo 360, {hoy.year})"
        return f"({fuente['abrev']}, s. f.)" if clave in citados else cita(clave)

    def p(texto: str, nombre: str = "cuerpo") -> Paragraph:
        return Paragraph(texto, estilo[nombre])

    titulo = solicitud.opciones.titulo
    historia: list[Flowable] = []

    # --- Portada: título en negrita centrado en la mitad superior, autor, afiliación y fecha
    historia.append(Spacer(1, 3 * 24))
    historia.append(p(escape(titulo), "titulo"))
    historia.append(Spacer(1, 24))
    historia.append(p(escape(autor or "Techo 360"), "centrado"))
    if autor:
        historia.append(p("Techo 360", "centrado"))
    historia.append(p(fecha_larga(hoy), "centrado"))
    historia.append(PageBreak())

    # --- Primera página del texto: título repetido y, sin encabezado, la introducción
    historia.append(p(escape(titulo), "titulo"))
    for parrafo in parrafos(solicitud.opciones.introduccion):
        historia.append(p(escape(parrafo)))

    # --- Método
    n_dep = sum(1 for z in zonas if z["nivel"] == "departamento")
    n_mun = len(zonas) - n_dep
    nombres_variables = [VARIABLES_REPORTE[v][0].lower() for v in solicitud.variables]
    historia.append(p("Método", "h1"))
    historia.append(
        p(
            escape(
                f"Se analizaron {len(nombres_variables)} {plural(len(nombres_variables), 'indicador', 'indicadores')} "
                f"({', '.join(nombres_variables)}) para {len(zonas)} {plural(len(zonas), 'zona', 'zonas')} de Colombia "
                f"({n_dep} {plural(n_dep, 'departamento', 'departamentos')} y {n_mun} {plural(n_mun, 'municipio', 'municipios')}) "
                f"entre {anio_i} y {anio_f}."
            )
        )
    )
    externas = [c for c in fuentes if FUENTES[c]["tipo"] == "externa"]
    if externas:
        origen = " y ".join(f"{FUENTES[c]['descripcion']} {cita(c)}" for c in externas)
        texto = f"Los datos observados provienen de {origen}."
        if "techo360" in fuentes:
            texto += f" Los valores sin dato observado corresponden a proyecciones propias {cita('techo360')}."
        historia.append(p(escape(texto)))
    elif "techo360" in fuentes:
        historia.append(p(escape(f"Los valores provienen de proyecciones propias {cita('techo360')}.")))
    cuerpo_metodo = "Los valores departamentales son el promedio simple de los municipios con dato disponible."
    if "score_privacion" in solicitud.variables:
        cuerpo_metodo += (
            " El score de privación es el promedio de tres indicadores normalizados por año "
            "(cobertura de acueducto y de alcantarillado invertidas, y déficit habitacional cuantitativo) "
            "y varía entre 0 (menor privación) y 1 (mayor privación)."
        )
    historia.append(p(escape(cuerpo_metodo)))

    # --- Resultados: una tabla por zona
    indice = indexar_series(series)
    deps, muns = grupos_de_zonas(zonas)
    contador_tablas = 0
    historia.append(p("Resultados", "h1"))
    if zonas:
        rango = "La Tabla 1" if len(zonas) == 1 else f"Las Tablas 1 a {len(zonas)}"
        historia.append(
            p(escape(f"{rango} {plural(len(zonas), 'presenta', 'presentan')} el valor anual de cada indicador por zona."))
        )

    encabezados = ["Año"] + [
        VARIABLES_REPORTE[v][0] + (f" ({VARIABLES_REPORTE[v][1]})" if VARIABLES_REPORTE[v][1] else "")
        for v in solicitud.variables
    ]
    columnas = len(encabezados)
    anchos = [0.55 * inch] + [(ancho_texto - 0.55 * inch) / (columnas - 1)] * (columnas - 1)

    def bloque_zona(zona: dict, nivel_encabezado: str, previos: list[Flowable] | None = None) -> list[Flowable]:
        nonlocal contador_tablas
        contador_tablas += 1
        etiqueta = etiqueta_de(zona)
        valores = indice.get((zona["nivel"], zona["codigo"]), {})
        filas = [[Paragraph(escape(e), estilo["celda_encabezado"]) for e in encabezados]]
        for anio in range(anio_i, anio_f + 1):
            filas.append(
                [Paragraph(str(anio), estilo["celda"])]
                + [Paragraph(formato_valor(valores.get((anio, v)), v), estilo["celda"]) for v in solicitud.variables]
            )
        tabla = Table(filas, colWidths=anchos, repeatRows=1, hAlign="LEFT")
        tabla.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 0.75, colors.black),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
                    ("LINEBELOW", (0, -1), (-1, -1), 0.75, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        nota = "n.d. = no hay dato disponible."
        if zona["nivel"] == "departamento":
            nota += " Los valores son el promedio simple de los municipios con dato."
        referencias = [cita_corta(c) for c in fuentes if c != "techo360"]
        if "techo360" in fuentes:
            referencias.append(cita_corta("techo360"))
        if referencias:
            nota += " Fuente: " + "; ".join(r.strip("()") for r in referencias) + "."
        comentario = solicitud.opciones.comentarios.get(zona["codigo"], "")
        encabezado: list[Flowable] = list(previos or []) + [p(escape(etiqueta), nivel_encabezado)]
        cuerpo_comentario = [p(escape(parrafo)) for parrafo in parrafos(comentario)]
        bloque_tabla: list[Flowable] = [
            p(f"Tabla {contador_tablas}", "rotulo"),
            p(escape(f"Indicadores de {etiqueta}, {periodo}"), "rotulo_titulo"),
            tabla,
            Spacer(1, 4),
            Paragraph("<i>Nota.</i> " + escape(nota), estilo["nota"]),
        ]
        if cuerpo_comentario:
            # el comentario puede ser largo: los encabezados se quedan con su primer párrafo
            return [KeepTogether(encabezado + cuerpo_comentario[:1]), *cuerpo_comentario[1:], KeepTogether(bloque_tabla)]
        return [KeepTogether(encabezado + bloque_tabla)]

    if deps and muns:
        # Dos grupos: nivel 2 por grupo y nivel 3 por zona (APA exige al menos dos subtítulos por nivel).
        for grupo, titulo_grupo in ((deps, "Departamentos"), (muns, "Municipios")):
            for orden, zona in enumerate(grupo):
                previos = [p(titulo_grupo, "h2")] if orden == 0 else None
                historia.extend(bloque_zona(zona, "h3", previos))
    else:
        for zona in deps or muns:
            historia.extend(bloque_zona(zona, "h2"))

    # --- Figuras: las gráficas que eligió el usuario, en su orden (escenas de reportes_escenas.py);
    # sin lista, y con `incluir_graficas`, queda el comportamiento anterior (una línea por variable).
    if escenas:
        figuras = []
        referencias_figura = [cita_corta(c).strip("()") for c in fuentes if FUENTES[c]["tipo"] == "externa"]
        for numero, (_, resultado) in enumerate(escenas, start=1):
            partes = [resultado.nota] if resultado.nota else []
            if referencias_figura:
                partes.append("Fuente: " + "; ".join(referencias_figura) + ".")
            bloque = [
                p(f"Figura {numero}", "rotulo"),
                p(escape(resultado.titulo), "rotulo_titulo"),
                EscenaPDF(resultado.escena, ancho_texto, serif=True),
            ]
            if partes:
                bloque += [Spacer(1, 4), Paragraph("<i>Nota.</i> " + escape(" ".join(partes)), estilo["nota"])]
            bloque.append(Spacer(1, 12))
            figuras.append(KeepTogether(bloque))
        historia.append(p("Gráficas", "h2"))
        historia.extend(figuras)
    elif solicitud.opciones.incluir_graficas and zonas:
        figuras: list[Flowable] = []
        numero_figura = 0
        for variable in solicitud.variables:
            anios, datos = _datos_de_figura(zonas, series, variable, solicitud)
            if not datos:
                continue
            numero_figura += 1
            grafica = GraficaLineasPDF(variable, anios, datos, ancho_texto, 2.2 * inch, "Times-Roman", 9)
            nota = "<i>Nota.</i> Cada línea representa una zona; los valores ausentes interrumpen la línea."
            if grafica.recortada:
                nota += f" Se muestran las primeras {MAX_SERIES_LEYENDA} de {len(datos)} zonas; los datos completos están en las tablas."
            figuras.append(
                KeepTogether(
                    [
                        p(f"Figura {numero_figura}", "rotulo"),
                        p(escape(f"{VARIABLES_REPORTE[variable][0]} por zona, {periodo}"), "rotulo_titulo"),
                        grafica,
                        Spacer(1, 4),
                        Paragraph(nota, estilo["nota"]),
                        Spacer(1, 12),
                    ]
                )
            )
        if figuras:
            historia.append(p("Evolución de los Indicadores", "h2"))
            historia.extend(figuras)

    # --- Conclusiones
    if solicitud.opciones.conclusiones.strip():
        historia.append(p("Conclusiones", "h1"))
        for parrafo in parrafos(solicitud.opciones.conclusiones):
            historia.append(p(escape(parrafo)))

    # --- Referencias: página aparte, orden alfabético, sangría francesa
    if fuentes:
        historia.append(PageBreakIfNotEmpty())  # evita una página en blanco si el texto terminó justo al pie
        historia.append(p("Referencias", "h1"))
        entradas = []
        for clave in fuentes:
            f = FUENTES[clave]
            anio = hoy.year if clave == "techo360" else "s. f."
            texto = f"{escape(f['autor'])}. ({anio}). <i>{escape(f['titulo'])}</i> [Conjunto de datos]."
            if f["url"]:
                texto += f" {f['url']}"
            entradas.append((f["autor"], texto))
        for _, texto in sorted(entradas, key=lambda e: e[0]):
            historia.append(p(texto, "referencia"))

    def numero_de_pagina(lienzo, documento):
        lienzo.saveState()
        lienzo.setFont("Times-Roman", 12)
        lienzo.drawRightString(letter[0] - inch, letter[1] - 0.5 * inch, str(documento.page))
        lienzo.restoreState()

    documento = BaseDocTemplate(
        str(ruta),
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        title=titulo,
        author=autor or "Techo 360",
    )
    marco = Frame(inch, inch, ancho_texto, letter[1] - 2 * inch, id="apa", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    documento.addPageTemplates([PageTemplate(id="apa", frames=[marco], onPage=numero_de_pagina)])
    documento.build(historia)


# ================================================================= IEEE
def crear_pdf_ieee(
    ruta: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
    autor: str | None = None,
    escenas: list | None = None,
) -> None:
    ancho_pagina, alto_pagina = letter
    margen_lat = 0.625 * inch
    margen_sup = 0.75 * inch
    margen_inf = 1.0 * inch
    ancho_col = 3.5 * inch
    separacion = 0.25 * inch
    ancho_total = ancho_pagina - 2 * margen_lat

    base = dict(fontName="Times-Roman", fontSize=10, leading=12, alignment=TA_JUSTIFY, textColor=colors.black)
    estilo = {
        "cuerpo": ParagraphStyle("ieee_cuerpo", firstLineIndent=0.17 * inch, **base),
        "titulo": ParagraphStyle("ieee_titulo", **{**base, "fontSize": 24, "leading": 27, "alignment": TA_CENTER}),
        "autor": ParagraphStyle("ieee_autor", **{**base, "fontSize": 11, "leading": 14, "alignment": TA_CENTER}),
        "afiliacion": ParagraphStyle("ieee_afil", **{**base, "fontName": "Times-Italic", "alignment": TA_CENTER}),
        "resumen": ParagraphStyle("ieee_resumen", **{**base, "fontName": "Times-Bold", "fontSize": 9, "leading": 11}),
        "h1": ParagraphStyle("ieee_h1", keepWithNext=1, spaceBefore=10, spaceAfter=4, **{**base, "alignment": TA_CENTER}),
        "h2": ParagraphStyle("ieee_h2", keepWithNext=1, spaceBefore=6, spaceAfter=3, **{**base, "fontName": "Times-Italic", "alignment": TA_LEFT}),
        "rotulo": ParagraphStyle("ieee_rotulo", keepWithNext=1, **{**base, "fontSize": 8, "leading": 10, "alignment": TA_CENTER}),
        "pie": ParagraphStyle("ieee_pie", spaceBefore=3, spaceAfter=8, **{**base, "fontSize": 8, "leading": 10, "alignment": TA_CENTER}),
        "nota": ParagraphStyle("ieee_nota", spaceBefore=3, spaceAfter=8, **{**base, "fontSize": 8, "leading": 10, "alignment": TA_LEFT}),
        "celda": ParagraphStyle("ieee_celda", **{**base, "fontSize": 8, "leading": 9.5, "alignment": TA_CENTER}),
        "celda_encabezado": ParagraphStyle("ieee_celda_enc", **{**base, "fontSize": 7, "leading": 8.5, "alignment": TA_CENTER}),
        "referencia": ParagraphStyle("ieee_ref", leftIndent=0.3 * inch, firstLineIndent=-0.3 * inch, **{**base, "fontSize": 8, "leading": 10, "alignment": TA_LEFT}),
    }
    anio_i, anio_f = solicitud.periodo.anio_inicio, solicitud.periodo.anio_fin
    periodo = f"{anio_i}–{anio_f}" if anio_i != anio_f else str(anio_i)
    hoy = date.today()
    fuentes = detectar_fuentes(series)
    orden_citas: dict[str, int] = {}

    def cita(clave: str) -> str:
        """[n]: numeradas en orden de primera aparición."""
        if clave not in orden_citas:
            orden_citas[clave] = len(orden_citas) + 1
        return f"[{orden_citas[clave]}]"

    def p(texto: str, nombre: str = "cuerpo") -> Paragraph:
        return Paragraph(texto, estilo[nombre])

    titulo = solicitud.opciones.titulo
    n_dep = sum(1 for z in zonas if z["nivel"] == "departamento")
    n_mun = len(zonas) - n_dep
    nombres_variables = [VARIABLES_REPORTE[v][0].lower() for v in solicitud.variables]

    # --- Bloque de título (a todo el ancho, solo primera página)
    bloque_titulo: list[Flowable] = [p(escape(titulo), "titulo"), Spacer(1, 10), p(escape(autor or "Techo 360"), "autor")]
    if autor:
        bloque_titulo.append(p("Techo 360", "afiliacion"))
    bloque_titulo.append(Spacer(1, 8))
    alto_titulo = 0.0
    for elemento in bloque_titulo:
        _, alto = elemento.wrap(ancho_total, alto_pagina)
        alto_titulo += alto
    alto_titulo += 6

    historia: list[Flowable] = list(bloque_titulo)
    historia.append(NextPageTemplate("resto"))
    historia.append(FrameBreak())

    # --- Resumen y palabras clave (9 pt negrita)
    resumen = (
        f"Este documento presenta la evolución de {len(nombres_variables)} "
        f"{plural(len(nombres_variables), 'indicador', 'indicadores')} de vivienda y territorio para {len(zonas)} "
        f"{plural(len(zonas), 'zona', 'zonas')} de Colombia ({n_dep} {plural(n_dep, 'departamento', 'departamentos')} y "
        f"{n_mun} {plural(n_mun, 'municipio', 'municipios')}) entre {anio_i} y {anio_f}, "
        "con tablas anuales por zona" + (" y figuras de su evolución." if solicitud.opciones.incluir_graficas else ".")
    )
    historia.append(p(f"<i>Resumen</i>—{escape(resumen)}", "resumen"))
    historia.append(Spacer(1, 4))
    claves = list(dict.fromkeys(nombres_variables + ["indicadores territoriales", "Colombia"]))
    historia.append(p(f"<i>Palabras clave</i>—{escape(', '.join(claves))}.", "resumen"))

    secciones = 0

    def seccion(texto: str) -> Paragraph:
        nonlocal secciones
        secciones += 1
        return p(f"{romano(secciones)}. {versalitas(texto, 10, 8)}", "h1")

    # --- I. Introducción
    historia.append(seccion("Introducción"))
    introduccion = parrafos(solicitud.opciones.introduccion)
    if introduccion:
        for parrafo in introduccion:
            historia.append(p(escape(parrafo)))
    else:
        historia.append(
            p(escape("Este documento resume indicadores de vivienda y territorio de las zonas seleccionadas en el periodo indicado."))
        )

    # --- II. Método y fuentes de datos
    historia.append(seccion("Método y fuentes de datos"))
    externas = [c for c in fuentes if FUENTES[c]["tipo"] == "externa"]
    if externas:
        origen = " y ".join(f"{FUENTES[c]['descripcion']} {cita(c)}" for c in externas)
        texto = f"Los datos observados provienen de {origen}."
        if "techo360" in fuentes:
            texto += f" Los valores sin dato observado son proyecciones propias {cita('techo360')}."
        historia.append(p(escape(texto)))
    elif "techo360" in fuentes:
        historia.append(p(escape(f"Los valores provienen de proyecciones propias {cita('techo360')}.")))
    metodo = "Los valores departamentales son el promedio simple de los municipios con dato disponible."
    if "score_privacion" in solicitud.variables:
        metodo += (
            " El score de privación es el promedio de tres indicadores normalizados por año "
            "(cobertura de acueducto y de alcantarillado invertidas, y déficit habitacional cuantitativo) "
            "y varía entre 0 y 1; a mayor valor, mayor privación."
        )
    historia.append(p(escape(metodo)))

    # --- III. Resultados: una tabla por zona
    indice = indexar_series(series)
    historia.append(seccion("Resultados"))
    contador_tablas = 0
    columnas = 1 + len(solicitud.variables)
    ancho_anio = 0.34 * inch
    anchos = [ancho_anio] + [(ancho_col - ancho_anio) / (columnas - 1)] * (columnas - 1)

    for orden, zona in enumerate(zonas):
        contador_tablas += 1
        etiqueta = etiqueta_de(zona)
        valores = indice.get((zona["nivel"], zona["codigo"]), {})
        filas = [[Paragraph("Año", estilo["celda_encabezado"])] + [Paragraph(escape(ABREVIATURAS_IEEE[v]), estilo["celda_encabezado"]) for v in solicitud.variables]]
        for anio in range(anio_i, anio_f + 1):
            filas.append(
                [Paragraph(str(anio), estilo["celda"])]
                + [Paragraph(formato_valor(valores.get((anio, v)), v), estilo["celda"]) for v in solicitud.variables]
            )
        tabla = Table(filas, colWidths=anchos, repeatRows=1, hAlign="CENTER")
        tabla.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 0.75, colors.black),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                    ("LINEBELOW", (0, -1), (-1, -1), 0.75, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ]
            )
        )
        encabezado = p(f"{letra(orden)}. {escape(etiqueta)}", "h2")
        comentarios = [p(escape(parrafo)) for parrafo in parrafos(solicitud.opciones.comentarios.get(zona["codigo"], ""))]
        bloque_tabla = [
            p(versalitas(f"Tabla {romano(contador_tablas)}", 8, 6.5), "rotulo"),
            p(versalitas(f"Indicadores de {etiqueta}, {periodo}", 8, 6.5), "rotulo"),
            Spacer(1, 3),
            tabla,
            p("n.d.: sin dato.", "nota"),
        ]
        # el encabezado de subsección viaja con lo que sigue (no queda huérfano al pie de la columna)
        if comentarios:
            historia.extend([KeepTogether([encabezado, comentarios[0]]), *comentarios[1:], KeepTogether(bloque_tabla)])
        else:
            historia.append(KeepTogether([encabezado, *bloque_tabla]))

    # --- IV. Gráficas (las que eligió el usuario, en su orden; sin lista, comportamiento anterior)
    if escenas:
        historia.append(seccion("Gráficas"))
        for numero, (_, resultado) in enumerate(escenas, start=1):
            pie = f"Fig. {numero}. {resultado.titulo}."
            if resultado.nota:
                pie += f" {resultado.nota}"
            historia.append(KeepTogether([EscenaPDF(resultado.escena, ancho_col, serif=True), p(escape(pie), "pie")]))
    elif solicitud.opciones.incluir_graficas and zonas:
        figuras: list[Flowable] = []
        numero_figura = 0
        for variable in solicitud.variables:
            anios, datos = _datos_de_figura(zonas, series, variable, solicitud)
            if not datos:
                continue
            numero_figura += 1
            grafica = GraficaLineasPDF(variable, anios, datos, ancho_col, 1.7 * inch, "Times-Roman", 7, columnas_leyenda=1, largo_nombre=52)
            pie = f"Fig. {numero_figura}. {VARIABLES_REPORTE[variable][0]} por zona, {periodo}."
            if grafica.recortada:
                pie += f" Se muestran las primeras {MAX_SERIES_LEYENDA} de {len(datos)} zonas."
            figuras.append(KeepTogether([grafica, p(escape(pie), "pie")]))
        if figuras:
            historia.append(seccion("Evolución de los indicadores"))
            historia.extend(figuras)

    # --- V. Conclusiones
    if solicitud.opciones.conclusiones.strip():
        historia.append(seccion("Conclusiones"))
        for parrafo in parrafos(solicitud.opciones.conclusiones):
            historia.append(p(escape(parrafo)))

    # --- Referencias: numeradas por orden de cita
    if orden_citas:
        historia.append(p(versalitas("Referencias", 10, 8), "h1"))
        for clave, numero in orden_citas.items():
            f = FUENTES[clave]
            if f["tipo"] == "propia":
                texto = f'{escape(f["autor"])}, “{escape(f["titulo"])},” conjunto de datos, {hoy.year}.'
            else:
                texto = (
                    f'{escape(f["autor"])}, “{escape(f["titulo"])},” [En línea]. Disponible: {f["url"]} '
                    f"(consultado: {fecha_corta(hoy)})."
                )
            historia.append(p(f"[{numero}]&nbsp;&nbsp;{texto}", "referencia"))

    def marco_columna(x, y, alto, ident):
        return Frame(x, y, ancho_col, alto, id=ident, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    x1 = margen_lat
    x2 = margen_lat + ancho_col + separacion
    y_base = margen_inf
    alto_completo = alto_pagina - margen_sup - margen_inf
    alto_resto = alto_completo - alto_titulo
    marco_titulo = Frame(
        margen_lat, alto_pagina - margen_sup - alto_titulo, ancho_total, alto_titulo,
        id="titulo", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    documento = BaseDocTemplate(
        str(ruta),
        pagesize=letter,
        leftMargin=margen_lat,
        rightMargin=margen_lat,
        topMargin=margen_sup,
        bottomMargin=margen_inf,
        title=titulo,
        author=autor or "Techo 360",
    )
    documento.addPageTemplates(
        [
            PageTemplate(id="primera", frames=[marco_titulo, marco_columna(x1, y_base, alto_resto, "c1p"), marco_columna(x2, y_base, alto_resto, "c2p")]),
            PageTemplate(id="resto", frames=[marco_columna(x1, y_base, alto_completo, "c1"), marco_columna(x2, y_base, alto_completo, "c2")]),
        ]
    )
    documento.build(historia)

