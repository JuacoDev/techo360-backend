import logging
import math
import uuid
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font, PatternFill
from reportlab.graphics import renderPDF
from reportlab.graphics.charts.legends import LineLegend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from db.session import SessionLocal, engine
from models.reporte import Reporte
from schemas.reporte import ReporteGenerarRequest
from services.score_privacion import (
    CODIGO_AGREGADO_NACIONAL_TERRIDATA,
    VARIABLES_SCORE,
    calcular_score_privacion,
    normalizar_minmax,
)

logger = logging.getLogger(__name__)


def _finito(valor) -> float | None:
    """float(valor) o None si falta o no es finito. mapcalordb.valuevar es NUMERIC y admite
    'NaN': una gráfica con NaN/inf revienta al dibujarse (y un Excel lo guardaría corrupto),
    así que un valor no finito se trata como dato faltante."""
    if valor is None:
        return None
    numero = float(valor)
    return numero if math.isfinite(numero) else None

VARIABLES_REPORTE = {
    "score_privacion": ("Score de Privación", ""),
    "cobertura_acueducto": ("Cobertura de Acueducto", "%"),
    "cobertura_alcantarillado": ("Cobertura de Alcantarillado", "%"),
    "deficit_habitacional_cuantitativo": (
        "Déficit Habitacional Cuantitativo",
        "%",
    ),
    "deficit_habitacional_cualitativo": (
        "Déficit Habitacional Cualitativo",
        "%",
    ),
    "densidad_poblacional": ("Densidad Poblacional", "hab/km²"),
}
VARIABLES_SCORE_LABEL = "Score compuesto de privación"
ERROR_PUBLICO = "No se pudo generar el reporte. Contacta al administrador."
DIRECTORIO_REPORTES = Path(settings.reportes_dir)
if not DIRECTORIO_REPORTES.is_absolute():
    DIRECTORIO_REPORTES = Path(__file__).resolve().parents[1] / DIRECTORIO_REPORTES
DIRECTORIO_REPORTES = DIRECTORIO_REPORTES.resolve()


def preparar_esquema_reportes() -> None:
    """Agrega de forma idempotente los campos requeridos por los reportes asíncronos."""
    with engine.begin() as conexion:
        conexion.execute(text("""
            ALTER TABLE public.reportes
                ADD COLUMN IF NOT EXISTS estado VARCHAR(20) NOT NULL DEFAULT 'listo',
                ADD COLUMN IF NOT EXISTS formato VARCHAR(10),
                ADD COLUMN IF NOT EXISTS solicitud JSONB,
                ADD COLUMN IF NOT EXISTS archivo_nombre VARCHAR(255),
                ADD COLUMN IF NOT EXISTS error TEXT
        """))


def validar_zonas_existentes(db: Session, solicitud: ReporteGenerarRequest) -> None:
    """Falla rápido (antes de encolar) si algún código de zona no existe."""
    if solicitud.zonas.departamentos:
        existentes = {
            fila[0]
            for fila in db.execute(
                text("""
                    SELECT codigo_departamento FROM public.zonas_departamento
                    WHERE codigo_departamento = ANY(:codigos)
                      AND codigo_departamento != '00'
                """),
                {"codigos": solicitud.zonas.departamentos},
            )
        }
        faltantes = sorted(set(solicitud.zonas.departamentos) - existentes)
        if faltantes:
            raise ValueError(
                f"Departamentos inexistentes: {', '.join(faltantes)}."
            )
    if solicitud.zonas.municipios:
        existentes = {
            fila[0]
            for fila in db.execute(
                text("""
                    SELECT codigo_municipio FROM public.zonas_municipio
                    WHERE codigo_municipio = ANY(:codigos)
                      AND codigo_municipio != '00000'
                """),
                {"codigos": solicitud.zonas.municipios},
            )
        }
        faltantes = sorted(set(solicitud.zonas.municipios) - existentes)
        if faltantes:
            raise ValueError(
                f"Municipios inexistentes: {', '.join(faltantes[:5])}"
                + ("…" if len(faltantes) > 5 else ".")
            )


def obtener_datos_reporte(
    db: Session,
    solicitud: ReporteGenerarRequest,
) -> tuple[list[dict], list[dict]]:
    departamentos_solicitados = solicitud.zonas.departamentos
    municipios_solicitados = solicitud.zonas.municipios
    zonas: list[dict] = []
    codigos_municipio_por_departamento: dict[str, list[str]] = {}

    if departamentos_solicitados:
        departamentos = db.execute(
            text("""
                SELECT codigo_departamento AS codigo, nombre
                FROM public.zonas_departamento
                WHERE codigo_departamento = ANY(:codigos)
                  AND codigo_departamento != '00'
                ORDER BY nombre
            """),
            {"codigos": departamentos_solicitados},
        ).mappings().all()
        if len(departamentos) != len(departamentos_solicitados):
            raise ValueError("Uno o más códigos de departamento no existen.")
        for departamento in departamentos:
            codigo = departamento["codigo"]
            zonas.append(
                {
                    "nivel": "departamento",
                    "codigo": codigo,
                    "nombre": departamento["nombre"],
                }
            )
        municipios_departamento = db.execute(
            text("""
                SELECT codigo_departamento, codigo_municipio
                FROM public.zonas_municipio
                WHERE codigo_departamento = ANY(:codigos)
                  AND codigo_municipio != '00000'
            """),
            {"codigos": departamentos_solicitados},
        ).mappings().all()
        for fila in municipios_departamento:
            codigos_municipio_por_departamento.setdefault(
                fila["codigo_departamento"], []
            ).append(fila["codigo_municipio"])

    if municipios_solicitados:
        municipios = db.execute(
            text("""
                SELECT codigo_municipio AS codigo, nombre
                FROM public.zonas_municipio
                WHERE codigo_municipio = ANY(:codigos)
                  AND codigo_municipio != '00000'
                ORDER BY nombre
            """),
            {"codigos": municipios_solicitados},
        ).mappings().all()
        if len(municipios) != len(municipios_solicitados):
            raise ValueError("Uno o más códigos de municipio no existen.")
        for municipio in municipios:
            zonas.append(
                {
                    "nivel": "municipio",
                    "codigo": municipio["codigo"],
                    "nombre": municipio["nombre"],
                }
            )

    codigos_municipio = set(municipios_solicitados)
    for codigos in codigos_municipio_por_departamento.values():
        codigos_municipio.update(codigos)

    variables_datos = sorted(
        {
            variable
            for variable in solicitud.variables
            if variable != "score_privacion"
        }
        | set(VARIABLES_SCORE)
    )
    anio_inicio = solicitud.periodo.anio_inicio
    anio_fin = solicitud.periodo.anio_fin

    filas_municipio = db.execute(
        text("""
            SELECT codigodivipola, namevar, yearm, valuevar, originvar
            FROM public.mapcalordb
            WHERE codigodivipola = ANY(:codigos)
              AND namevar = ANY(:variables)
              AND yearm BETWEEN :anio_inicio AND :anio_fin
              AND codigodivipola != :codigo_nacional
        """),
        {
            "codigos": sorted(codigos_municipio),
            "variables": variables_datos,
            "anio_inicio": anio_inicio,
            "anio_fin": anio_fin,
            "codigo_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
        },
    ).mappings().all()

    filas_score_global = db.execute(
        text("""
            SELECT namevar, yearm, valuevar
            FROM public.mapcalordb
            WHERE namevar = ANY(:variables)
              AND yearm BETWEEN :anio_inicio AND :anio_fin
              AND codigodivipola IS NOT NULL
              AND codigodivipola != :codigo_nacional
        """),
        {
            "variables": VARIABLES_SCORE,
            "anio_inicio": anio_inicio,
            "anio_fin": anio_fin,
            "codigo_nacional": CODIGO_AGREGADO_NACIONAL_TERRIDATA,
        },
    ).mappings().all()

    valores_score: dict[tuple[int, str], list[float]] = defaultdict(list)
    for fila in filas_score_global:
        valor_finito = _finito(fila["valuevar"])
        if valor_finito is not None:
            valores_score[(fila["yearm"], fila["namevar"])].append(valor_finito)
    limites_score = {
        clave: (min(valores), max(valores)) if valores else (None, None)
        for clave, valores in valores_score.items()
    }

    datos_municipio: dict[tuple[str, int, str], dict] = {}
    for fila in filas_municipio:
        clave = (fila["codigodivipola"], fila["yearm"], fila["namevar"])
        datos_municipio[clave] = {
            "valor": _finito(fila["valuevar"]),
            "origen": fila["originvar"],
        }

    score_por_municipio: dict[tuple[str, int], float | None] = {}
    for codigo in codigos_municipio:
        for anio in range(anio_inicio, anio_fin + 1):
            indicadores = {}
            for variable in VARIABLES_SCORE:
                dato = datos_municipio.get((codigo, anio, variable))
                minimo, maximo = limites_score.get(
                    (anio, variable), (None, None)
                )
                indicadores[variable] = {
                    "score_normalizado": normalizar_minmax(
                        dato["valor"] if dato else None,
                        minimo,
                        maximo,
                    )
                }
            score_por_municipio[(codigo, anio)] = calcular_score_privacion(
                indicadores
            )

    series: list[dict] = []
    for zona in zonas:
        if zona["nivel"] == "municipio":
            codigos_integrantes = [zona["codigo"]]
        else:
            codigos_integrantes = codigos_municipio_por_departamento.get(
                zona["codigo"], []
            )

        for anio in range(anio_inicio, anio_fin + 1):
            for variable in solicitud.variables:
                valores: list[float] = []
                origenes: set[str] = set()
                if variable == "score_privacion":
                    valores = [
                        valor
                        for codigo in codigos_integrantes
                        if (valor := score_por_municipio.get((codigo, anio)))
                        is not None
                    ]
                    origen = (
                        VARIABLES_SCORE_LABEL
                        if zona["nivel"] == "municipio"
                        else "Promedio simple del score de sus municipios"
                    )
                else:
                    for codigo in codigos_integrantes:
                        dato = datos_municipio.get((codigo, anio, variable))
                        if dato is not None:
                            if dato["origen"]:
                                origenes.add(str(dato["origen"]))
                            if dato["valor"] is not None:
                                valores.append(dato["valor"])
                    origen = (
                        "; ".join(sorted(origenes))
                        if zona["nivel"] == "municipio"
                        else "Promedio simple de municipios"
                        + (f"; fuentes: {', '.join(sorted(origenes))}" if origenes else "")
                    )

                series.append(
                    {
                        **zona,
                        "anio": anio,
                        "variable": variable,
                        "valor": (
                            valores[0]
                            if zona["nivel"] == "municipio" and valores
                            else (
                                sum(valores) / len(valores)
                                if zona["nivel"] == "departamento" and valores
                                else None
                            )
                        ),
                        "origen": origen or None,
                        "comentario": solicitud.opciones.comentarios.get(
                            zona["codigo"], ""
                        ),
                    }
                )

    return zonas, series


def _valores_por_grafica(
    zonas: list[dict],
    series: list[dict],
    variable: str,
    anio_inicio: int,
    anio_fin: int,
) -> tuple[list[str], list[tuple[str, list[float | None]]]]:
    anios = [str(anio) for anio in range(anio_inicio, anio_fin + 1)]
    valores_por_zona: dict[tuple[str, str], dict[int, float | None]] = defaultdict(dict)
    for fila in series:
        if fila["variable"] == variable:
            valores_por_zona[(fila["nivel"], fila["codigo"])][fila["anio"]] = fila["valor"]

    series_grafica = []
    for zona in zonas:
        valores_por_anio = valores_por_zona.get((zona["nivel"], zona["codigo"]), {})
        valores = [_finito(valores_por_anio.get(int(anio))) for anio in anios]
        if any(valor is not None for valor in valores):
            series_grafica.append(
                (
                    f"{zona['nombre']} ({zona['codigo']})",
                    valores,
                )
            )
    return anios, series_grafica


PALETA_GRAFICA = [
    "#126782", "#E4572E", "#2E933C", "#F3A712", "#7B2CBF", "#17BEBB",
    "#D81159", "#6C757D", "#8D5B4C", "#3A86FF",
]
MAX_SERIES_LEYENDA = 10


class ReporteGrafica(Flowable):
    """Línea por zona a lo largo de los años (años en X) con leyenda de colores.

    Si hay más zonas que colores, se grafican las primeras MAX_SERIES_LEYENDA
    (el resto sigue completo en las tablas) para que la leyenda siga legible.
    """

    def __init__(self, variable: str, anios: list[str], datos: list[tuple[str, list[float | None]]]):
        super().__init__()
        self.variable = variable
        self.anios = anios
        self.datos = datos[:MAX_SERIES_LEYENDA]
        self.recortada = len(datos) > MAX_SERIES_LEYENDA
        self.width = 9.3 * inch
        filas_leyenda = (len(self.datos) + 1) // 2
        self.alto_leyenda = 14 * filas_leyenda + 6
        self.height = 3.0 * inch + self.alto_leyenda

    def wrap(self, aviable_width, aviable_height):
        return self.width, self.height

    def draw(self):
        # Una gráfica que no se pueda dibujar no debe tumbar el reporte entero.
        try:
            self._dibujar()
        except Exception:
            logger.exception("No se pudo dibujar la gráfica de %s.", self.variable)
            self.canv.setFont("Helvetica-Oblique", 9)
            self.canv.setFillColor(colors.HexColor("#B3261E"))
            self.canv.drawString(0, self.height / 2, "No se pudo dibujar esta gráfica; los datos están en las tablas.")

    def _dibujar(self):
        drawing = Drawing(self.width, self.height)
        grafica = HorizontalLineChart()
        grafica.x = 48
        grafica.y = self.alto_leyenda + 35
        grafica.width = self.width - 65
        grafica.height = self.height - self.alto_leyenda - 55
        grafica.categoryAxis.categoryNames = self.anios
        grafica.categoryAxis.labels.fontSize = 8
        grafica.valueAxis.labels.fontSize = 8
        grafica.valueAxis.valueMin = None
        grafica.data = [
            tuple(float(valor) if valor is not None else None for valor in valores)
            for _, valores in self.datos
        ]
        for indice in range(len(self.datos)):
            grafica.lines[indice].strokeColor = colors.HexColor(PALETA_GRAFICA[indice])
            grafica.lines[indice].strokeWidth = 1.8
            grafica.lines[indice].symbol = None
        drawing.add(grafica)

        leyenda = LineLegend()
        leyenda.x = 48
        leyenda.y = self.alto_leyenda - 6
        leyenda.alignment = "right"
        leyenda.fontSize = 8
        leyenda.dx = 14
        leyenda.dy = 6
        leyenda.dxTextSpace = 5
        leyenda.deltax = (self.width - 65) / 2
        leyenda.deltay = 13
        leyenda.columnMaximum = (len(self.datos) + 1) // 2
        leyenda.colorNamePairs = [
            (colors.HexColor(PALETA_GRAFICA[indice]), nombre[:48])
            for indice, (nombre, _) in enumerate(self.datos)
        ]
        drawing.add(leyenda)
        renderPDF.draw(drawing, self.canv, 0, 0)


def _crear_pdf(
    path: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
) -> None:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(letter),
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=solicitud.opciones.titulo,
    )
    estilos = getSampleStyleSheet()
    estilos.add(
        ParagraphStyle(
            name="TituloReporte",
            parent=estilos["Title"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#126782"),
        )
    )
    historia: list[Flowable] = [
        Paragraph(escape(solicitud.opciones.titulo), estilos["TituloReporte"]),
        Spacer(1, 0.15 * inch),
        Paragraph(
            f"Periodo: {solicitud.periodo.anio_inicio} - {solicitud.periodo.anio_fin}",
            estilos["Normal"],
        ),
    ]
    if solicitud.opciones.introduccion:
        historia.extend(
            [
                Spacer(1, 0.1 * inch),
                Paragraph("Introducción", estilos["Heading2"]),
                Paragraph(escape(solicitud.opciones.introduccion).replace("\n", "<br/>"), estilos["BodyText"]),
            ]
        )
    historia.extend(
        [
            Spacer(1, 0.2 * inch),
            Paragraph("Resultados", estilos["Heading1"]),
            Paragraph(
                "Los valores departamentales son promedios simples de los municipios con dato disponible. "
                "El score de privación usa la normalización oficial del backend por año.",
                estilos["BodyText"],
            ),
        ]
    )

    encabezados = ["Año"] + [
        VARIABLES_REPORTE[variable][0]
        for variable in solicitud.variables
    ]
    for zona in zonas:
        filas_zona = [
            fila for fila in series
            if fila["codigo"] == zona["codigo"] and fila["nivel"] == zona["nivel"]
        ]
        historia.extend(
            [
                Spacer(1, 0.15 * inch),
                Paragraph(
                    f"{escape(zona['nombre'])} ({zona['nivel']}, {zona['codigo']})",
                    estilos["Heading2"],
                ),
            ]
        )
        comentario = solicitud.opciones.comentarios.get(zona["codigo"], "")
        if comentario:
            historia.append(
                Paragraph(
                    f"Comentario: {escape(comentario).replace(chr(10), '<br/>')}",
                    estilos["BodyText"],
                )
            )
        por_anio: dict[int, dict[str, dict]] = defaultdict(dict)
        for fila in filas_zona:
            por_anio[fila["anio"]][fila["variable"]] = fila
        datos_tabla: list[list] = [encabezados]
        for anio in range(solicitud.periodo.anio_inicio, solicitud.periodo.anio_fin + 1):
            fila_tabla: list[str] = [str(anio)]
            for variable in solicitud.variables:
                dato = por_anio[anio].get(variable)
                valor = dato["valor"] if dato else None
                unidad = VARIABLES_REPORTE[variable][1]
                fila_tabla.append(
                    "Sin dato"
                    if valor is None
                    else f"{valor:.4f}{unidad}"
                )
            datos_tabla.append(fila_tabla)
        ancho_columna = doc.width / len(encabezados)
        encabezados_tabla = [
            Paragraph(escape(str(valor)), estilos["BodyText"])
            for valor in encabezados
        ]
        datos_tabla[0] = encabezados_tabla
        tabla = Table(
            datos_tabla,
            colWidths=[ancho_columna] * len(encabezados),
            repeatRows=1,
            hAlign="LEFT",
        )
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#126782")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7C9D1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F6F8")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        historia.append(tabla)

    if solicitud.opciones.incluir_graficas:
        historia.extend([PageBreak(), Paragraph("Gráficas", estilos["Heading1"])])
        for variable in solicitud.variables:
            anios, valores = _valores_por_grafica(
                zonas,
                series,
                variable,
                solicitud.periodo.anio_inicio,
                solicitud.periodo.anio_fin,
            )
            if not valores:
                continue
            grafica = ReporteGrafica(variable, anios, valores)
            nota = (
                f"Cada línea representa una zona (se muestran las primeras {MAX_SERIES_LEYENDA} de {len(valores)}; "
                "los datos completos están en las tablas)."
                if grafica.recortada
                else "Cada línea representa una zona incluida en los resultados."
            )
            historia.append(
                KeepTogether(
                    [
                        Paragraph(VARIABLES_REPORTE[variable][0], estilos["Heading2"]),
                        Paragraph(nota, estilos["BodyText"]),
                        grafica,
                    ]
                )
            )

    if solicitud.opciones.conclusiones:
        historia.extend(
            [
                PageBreak(),
                Paragraph("Conclusiones", estilos["Heading1"]),
                Paragraph(
                    escape(solicitud.opciones.conclusiones).replace("\n", "<br/>"),
                    estilos["BodyText"],
                ),
            ]
        )
    doc.build(historia)


def _crear_excel(
    path: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
) -> None:
    def texto_seguro(valor: str) -> str:
        if valor.startswith(("=", "+", "-", "@")):
            return "'" + valor
        return valor

    libro = Workbook()
    resumen = libro.active
    resumen.title = "Resumen"
    resumen.append(["Título", texto_seguro(solicitud.opciones.titulo)])
    resumen.append(["Periodo", f"{solicitud.periodo.anio_inicio} - {solicitud.periodo.anio_fin}"])
    resumen.append(["Formato", solicitud.formato])
    resumen.append(["Introducción", texto_seguro(solicitud.opciones.introduccion)])
    resumen.append(["Conclusiones", texto_seguro(solicitud.opciones.conclusiones)])
    resumen.append(["Incluye gráficas", "Sí" if solicitud.opciones.incluir_graficas else "No"])
    resumen.append([])
    resumen.append(["Nivel", "Código", "Zona", "Comentario"])
    for zona in zonas:
        resumen.append(
            [
                zona["nivel"],
                texto_seguro(zona["codigo"]),
                texto_seguro(zona["nombre"]),
                texto_seguro(solicitud.opciones.comentarios.get(zona["codigo"], "")),
            ]
        )
    hoja_datos = libro.create_sheet("Datos")
    hoja_datos.append(
        ["Nivel", "Código", "Zona", "Año", "Variable", "Valor", "Unidad", "Origen", "Comentario"]
    )
    for fila in series:
        unidad = VARIABLES_REPORTE[fila["variable"]][1]
        hoja_datos.append(
            [
                fila["nivel"],
                texto_seguro(fila["codigo"]),
                texto_seguro(fila["nombre"]),
                fila["anio"],
                texto_seguro(VARIABLES_REPORTE[fila["variable"]][0]),
                fila["valor"],
                texto_seguro(unidad),
                texto_seguro(fila["origen"] or ""),
                texto_seguro(fila["comentario"]),
            ]
        )

    for hoja in (resumen, hoja_datos):
        hoja.freeze_panes = "A2"
        for celda in hoja[1]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="126782")
        for columna in hoja.columns:
            ancho = min(
                50,
                max(
                    12,
                    max(
                        len(str(celda.value or ""))
                        for celda in list(columna)[:1000]
                    ) + 2,
                ),
            )
            hoja.column_dimensions[columna[0].column_letter].width = ancho
    hoja_datos.auto_filter.ref = hoja_datos.dimensions
    if solicitud.opciones.incluir_graficas:
        for indice, variable in enumerate(solicitud.variables, start=1):
            anios, valores = _valores_por_grafica(
                zonas,
                series,
                variable,
                solicitud.periodo.anio_inicio,
                solicitud.periodo.anio_fin,
            )
            if not valores:
                continue
            hoja = libro.create_sheet(f"Grafica {indice}")
            try:
                hoja.append(
                    ["Año"] + [texto_seguro(nombre) for nombre, _ in valores]
                )
                for fila_indice, anio in enumerate(anios):
                    hoja.append(
                        [int(anio)]
                        + [
                            valores_serie[fila_indice]
                            for _, valores_serie in valores
                        ]
                    )
                grafica = LineChart()
                grafica.title = VARIABLES_REPORTE[variable][0]
                grafica.y_axis.title = VARIABLES_REPORTE[variable][1] or "Valor"
                grafica.x_axis.title = "Año"
                grafica.height = 9
                grafica.width = 18
                datos = Reference(
                    hoja,
                    min_col=2,
                    max_col=len(valores) + 1,
                    min_row=1,
                    max_row=len(anios) + 1,
                )
                categorias = Reference(hoja, min_col=1, min_row=2, max_row=len(anios) + 1)
                grafica.add_data(datos, titles_from_data=True)
                grafica.set_categories(categorias)
                hoja.add_chart(grafica, f"A{len(anios) + 4}")
            except Exception:
                logger.exception("No se pudo crear la gráfica de %s.", variable)
                libro.remove(hoja)
    libro.save(path)


def construir_previa_graficas(solicitud: ReporteGenerarRequest, db: Session) -> dict:
    """Series por año de cada variable, con las mismas reglas que las gráficas del reporte
    (mismos datos, mismo tope de zonas por gráfica), para mostrarlas antes de generarlo."""
    zonas, series = obtener_datos_reporte(db, solicitud)
    inicio = solicitud.periodo.anio_inicio
    fin = solicitud.periodo.anio_fin
    graficas = []
    for variable in solicitud.variables:
        _, datos = _valores_por_grafica(zonas, series, variable, inicio, fin)
        nombre, unidad = VARIABLES_REPORTE[variable]
        graficas.append(
            {
                "variable": variable,
                "nombre": nombre,
                "unidad": unidad,
                "series": [
                    {"nombre": nombre_zona, "valores": valores}
                    for nombre_zona, valores in datos[:MAX_SERIES_LEYENDA]
                ],
                "total_series": len(datos),
            }
        )
    return {"anios": list(range(inicio, fin + 1)), "graficas": graficas}


def generar_archivo_reporte(
    solicitud: ReporteGenerarRequest,
    db: Session,
) -> tuple[str, int]:
    zonas, series = obtener_datos_reporte(db, solicitud)
    DIRECTORIO_REPORTES.mkdir(parents=True, exist_ok=True)
    nombre_archivo = f"{uuid.uuid4().hex}.{solicitud.formato}"
    destino = DIRECTORIO_REPORTES / nombre_archivo
    try:
        if solicitud.formato == "pdf":
            _crear_pdf(destino, solicitud, zonas, series)
        else:
            _crear_excel(destino, solicitud, zonas, series)
    except Exception:
        destino.unlink(missing_ok=True)
        raise
    return nombre_archivo, len(series)


def procesar_reporte(reporte_id: int) -> None:
    db = SessionLocal()
    archivo_generado: Path | None = None
    try:
        reporte = db.query(Reporte).filter(Reporte.id == reporte_id).first()
        if reporte is None or reporte.estado in {"listo", "error"}:
            return
        reporte.estado = "procesando"
        reporte.error = None
        db.commit()

        solicitud = ReporteGenerarRequest.model_validate(reporte.solicitud)
        nombre_archivo, cantidad_registros = generar_archivo_reporte(solicitud, db)
        archivo_generado = DIRECTORIO_REPORTES / nombre_archivo
        reporte = db.query(Reporte).filter(Reporte.id == reporte_id).first()
        if reporte is None:
            raise RuntimeError("El reporte dejó de existir durante el procesamiento.")
        reporte.estado = "listo"
        reporte.archivo_nombre = nombre_archivo
        reporte.error = None
        reporte.contenido = (
            f"Reporte generado correctamente con {cantidad_registros} registros."
        )
        db.commit()
        logger.info("Reporte %s generado correctamente.", reporte_id)
    except Exception:
        db.rollback()
        if archivo_generado is not None:
            try:
                archivo_generado.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "No se pudo limpiar el archivo parcial del reporte %s.",
                    reporte_id,
                )
        logger.exception("Falló la generación del reporte %s.", reporte_id)
        reporte = db.query(Reporte).filter(Reporte.id == reporte_id).first()
        if reporte is not None:
            reporte.estado = "error"
            reporte.error = ERROR_PUBLICO
            reporte.archivo_nombre = None
            reporte.contenido = "Falló la generación del reporte."
            db.commit()
    finally:
        db.close()


def obtener_ruta_archivo(reporte: Reporte) -> Path | None:
    if not reporte.archivo_nombre or Path(reporte.archivo_nombre).name != reporte.archivo_nombre:
        return None
    ruta = (DIRECTORIO_REPORTES / reporte.archivo_nombre).resolve()
    if ruta.parent != DIRECTORIO_REPORTES:
        return None
    return ruta
