import logging
import math
import time
import uuid
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from db.session import SessionLocal, engine
from models.mapcalor_user import MapCalorUser
from models.reporte import Reporte
from schemas.reporte import ReporteGenerarRequest
from services.reportes_cache import cache_reportes
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
                ADD COLUMN IF NOT EXISTS error TEXT,
                ADD COLUMN IF NOT EXISTS huella VARCHAR(64),
                ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
        """))
        conexion.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_reportes_usuario_actualizado
                ON public.reportes (usuario_id, actualizado_en DESC)
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
    variables: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """(zonas, series). `variables` por defecto son las del reporte; las gráficas pueden pedir más."""
    variables = list(variables) if variables is not None else list(solicitud.variables)
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
                SELECT zm.codigo_municipio AS codigo, zm.nombre,
                       zm.codigo_departamento, zd.nombre AS departamento
                FROM public.zonas_municipio zm
                LEFT JOIN public.zonas_departamento zd
                       ON zd.codigo_departamento = zm.codigo_departamento
                WHERE zm.codigo_municipio = ANY(:codigos)
                  AND zm.codigo_municipio != '00000'
                ORDER BY zm.nombre
            """),
            {"codigos": municipios_solicitados},
        ).mappings().all()
        if len(municipios) != len(municipios_solicitados):
            raise ValueError("Uno o más códigos de municipio no existen.")
        for municipio in municipios:
            departamento = municipio["departamento"]
            # "Bucaramanga, Santander"; Bogotá (11001) va sin departamento.
            sin_departamento = (
                not departamento
                or municipio["codigo"] == "11001"
                or municipio["codigo_departamento"] == "11"
            )
            zonas.append(
                {
                    "nivel": "municipio",
                    "codigo": municipio["codigo"],
                    "nombre": municipio["nombre"],
                    "etiqueta": (
                        municipio["nombre"]
                        if sin_departamento
                        else f"{municipio['nombre']}, {departamento}"
                    ),
                }
            )

    codigos_municipio = set(municipios_solicitados)
    for codigos in codigos_municipio_por_departamento.values():
        codigos_municipio.update(codigos)

    variables_datos = sorted(
        {
            variable
            for variable in variables
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
            for variable in variables:
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
                    f"{zona.get('etiqueta') or zona['nombre']} ({zona['codigo']})",
                    valores,
                )
            )
    return anios, series_grafica


PALETA_GRAFICA = [
    "#126782", "#E4572E", "#2E933C", "#F3A712", "#7B2CBF", "#17BEBB",
    "#D81159", "#6C757D", "#8D5B4C", "#3A86FF",
]
MAX_SERIES_LEYENDA = 10


def _crear_pdf(
    path: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
    autor: str | None = None,
    escenas: list | None = None,
) -> None:
    """PDF con la plantilla elegida (APA 7 o IEEE); ver services/reportes_pdf.py."""
    from services import reportes_pdf  # import tardío: reportes_pdf importa este módulo

    if solicitud.opciones.plantilla == "ieee":
        reportes_pdf.crear_pdf_ieee(path, solicitud, zonas, series, autor, escenas)
    else:
        reportes_pdf.crear_pdf_apa(path, solicitud, zonas, series, autor, escenas)


def _texto_seguro(valor: str) -> str:
    if valor.startswith(("=", "+", "-", "@")):
        return "'" + valor
    return valor


def _crear_excel_completo(
    path: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
    escenas: list | None = None,
) -> None:
    texto_seguro = _texto_seguro

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
                texto_seguro(zona.get("etiqueta") or zona["nombre"]),
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
                texto_seguro(fila.get("etiqueta") or fila["nombre"]),
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
    if solicitud.opciones.incluir_graficas and not solicitud.graficas:
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
    _hojas_graficas(libro, escenas)
    libro.save(path)


def _crear_excel_comparativo(
    path: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
    escenas: list | None = None,
) -> None:
    """Una hoja por variable con las zonas en filas y los años en columnas (más la variación del
    periodo y, si se pidió, una gráfica de líneas por hoja). Pensado para comparar zonas de un vistazo."""
    inicio = solicitud.periodo.anio_inicio
    fin = solicitud.periodo.anio_fin
    anios = list(range(inicio, fin + 1))
    libro = Workbook()
    resumen = libro.active
    resumen.title = "Resumen"
    resumen.append(["Título", _texto_seguro(solicitud.opciones.titulo)])
    resumen.append(["Periodo", f"{inicio} - {fin}"])
    resumen.append(["Plantilla", "Comparativo (zonas en filas, años en columnas)"])
    resumen.append(["Introducción", _texto_seguro(solicitud.opciones.introduccion)])
    resumen.append(["Conclusiones", _texto_seguro(solicitud.opciones.conclusiones)])
    resumen.append([])
    resumen.append(["Nivel", "Código", "Zona", "Comentario"])
    for zona in zonas:
        resumen.append(
            [
                zona["nivel"],
                _texto_seguro(zona["codigo"]),
                _texto_seguro(zona.get("etiqueta") or zona["nombre"]),
                _texto_seguro(solicitud.opciones.comentarios.get(zona["codigo"], "")),
            ]
        )
    valores_por_zona: dict[tuple[str, str, str], dict[int, float | None]] = defaultdict(dict)
    for fila in series:
        valores_por_zona[(fila["nivel"], fila["codigo"], fila["variable"])][fila["anio"]] = _finito(fila["valor"])

    hojas = [resumen]
    for variable in solicitud.variables:
        etiqueta, unidad = VARIABLES_REPORTE[variable]
        hoja = libro.create_sheet(etiqueta[:31])
        hoja.append(["Nivel", "Código", "Zona"] + anios + ["Variación del periodo"])
        for zona in zonas:
            por_anio = valores_por_zona.get((zona["nivel"], zona["codigo"], variable), {})
            fila_valores = [por_anio.get(anio) for anio in anios]
            presentes = [valor for valor in fila_valores if valor is not None]
            variacion = presentes[-1] - presentes[0] if len(presentes) >= 2 else None
            hoja.append(
                [zona["nivel"], _texto_seguro(zona["codigo"]), _texto_seguro(zona.get("etiqueta") or zona["nombre"])]
                + fila_valores
                + [variacion]
            )
        hojas.append(hoja)
        if solicitud.opciones.incluir_graficas and not solicitud.graficas and zonas:
            try:
                grafica = LineChart()
                grafica.title = etiqueta
                grafica.y_axis.title = unidad or "Valor"
                grafica.x_axis.title = "Año"
                grafica.height = 9
                grafica.width = 22
                ultima_columna = 3 + len(anios)
                datos = Reference(hoja, min_col=3, max_col=ultima_columna, min_row=2, max_row=1 + len(zonas))
                categorias = Reference(hoja, min_col=4, max_col=ultima_columna, min_row=1, max_row=1)
                grafica.add_data(datos, from_rows=True, titles_from_data=True)
                grafica.set_categories(categorias)
                hoja.add_chart(grafica, f"A{len(zonas) + 4}")
            except Exception:
                logger.exception("No se pudo crear la gráfica comparativa de %s.", variable)

    for hoja in hojas:
        hoja.freeze_panes = "A2"
        for celda in hoja[1]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="126782")
        for columna in hoja.columns:
            ancho = min(50, max(10, max(len(str(celda.value or "")) for celda in list(columna)[:1000]) + 2))
            hoja.column_dimensions[columna[0].column_letter].width = ancho
    _hojas_graficas(libro, escenas)
    libro.save(path)


def _hojas_graficas(libro, escenas: list | None) -> None:
    """Una hoja por gráfica elegida: su tabla de datos y, cuando Excel tiene un equivalente nativo
    (barras, apiladas, líneas, dispersión, donut, histograma), la gráfica editable."""
    from openpyxl.chart import BarChart, DoughnutChart, ScatterChart, Series

    for numero, (spec, resultado) in enumerate(escenas or [], start=1):
        hoja = libro.create_sheet(f"Gráfica {numero}"[:31])
        hoja.append([_texto_seguro(resultado.titulo)])
        hoja["A1"].font = Font(bold=True, size=13)
        hoja.append([])
        encabezados = resultado.tabla["encabezados"]
        filas = resultado.tabla["filas"]
        if encabezados:
            hoja.append([_texto_seguro(str(e)) for e in encabezados])
            for celda in hoja[3]:
                celda.font = Font(bold=True, color="FFFFFF")
                celda.fill = PatternFill("solid", fgColor="126782")
            for fila in filas:
                hoja.append([_texto_seguro(v) if isinstance(v, str) else (_finito(v) if isinstance(v, float) else v) for v in fila])
        if resultado.nota:
            hoja.append([])
            hoja.append([_texto_seguro(resultado.nota)])
        for columna in hoja.columns:
            ancho = min(46, max(12, max(len(str(c.value or "")) for c in list(columna)[:200]) + 2))
            hoja.column_dimensions[columna[0].column_letter].width = ancho
        n = len(filas)
        if not encabezados or n == 0:
            continue
        columnas = len(encabezados)
        ancla = f"{get_column_letter(columnas + 2)}3"
        try:
            grafica = None
            tipo = spec.tipo
            if tipo in ("barras_ranking", "barras_agrupadas", "barras_apiladas"):
                grafica = BarChart()
                grafica.type = "bar"
                ultima = 2 if tipo == "barras_ranking" else (columnas - 1 if tipo == "barras_apiladas" and encabezados[-1] == "Total" else columnas)
                grafica.add_data(Reference(hoja, min_col=2, max_col=ultima, min_row=3, max_row=3 + n), titles_from_data=True)
                grafica.set_categories(Reference(hoja, min_col=1, min_row=4, max_row=3 + n))
                if tipo == "barras_apiladas":
                    grafica.grouping = "stacked"
                    grafica.overlap = 100
            elif tipo == "lineas":
                grafica = LineChart()
                grafica.add_data(Reference(hoja, min_col=2, max_col=columnas, min_row=3, max_row=3 + n), titles_from_data=True)
                grafica.set_categories(Reference(hoja, min_col=1, min_row=4, max_row=3 + n))
            elif tipo == "histograma":
                grafica = BarChart()
                grafica.add_data(Reference(hoja, min_col=3, min_row=3, max_row=3 + n), titles_from_data=True)
                grafica.set_categories(Reference(hoja, min_col=1, min_row=4, max_row=3 + n))
            elif tipo == "donut":
                grafica = DoughnutChart()
                grafica.add_data(Reference(hoja, min_col=2, min_row=3, max_row=3 + n), titles_from_data=True)
                grafica.set_categories(Reference(hoja, min_col=1, min_row=4, max_row=3 + n))
            elif tipo == "dispersion" and columnas >= 3:
                grafica = ScatterChart()
                grafica.style = 13
                serie = Series(
                    Reference(hoja, min_col=3, min_row=4, max_row=3 + n),
                    Reference(hoja, min_col=2, min_row=4, max_row=3 + n),
                    title="Zonas",
                )
                serie.marker.symbol = "circle"
                serie.graphicalProperties.line.noFill = True
                grafica.series.append(serie)
                grafica.x_axis.title = str(encabezados[1])
                grafica.y_axis.title = str(encabezados[2])
            if grafica is not None:
                grafica.title = resultado.titulo[:80]
                grafica.height = 9
                grafica.width = 18
                hoja.add_chart(grafica, ancla)
            else:
                hoja.cell(row=5 + n + (2 if resultado.nota else 0), column=1, value="Esta gráfica no tiene equivalente nativo en Excel: su versión visual está en la vista previa y en el PDF.")
        except Exception:
            logger.exception("No se pudo crear la gráfica %s de la hoja %s.", spec.tipo, numero)


def _crear_excel(
    path: Path,
    solicitud: ReporteGenerarRequest,
    zonas: list[dict],
    series: list[dict],
    escenas: list | None = None,
) -> None:
    if solicitud.opciones.plantilla == "comparativo":
        _crear_excel_comparativo(path, solicitud, zonas, series, escenas)
    else:
        _crear_excel_completo(path, solicitud, zonas, series, escenas)


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
    autor: str | None = None,
) -> tuple[str, int]:
    # Import tardío: estos módulos importan este.
    from services.reportes_datos_graficas import escenas_de_solicitud, variables_de_graficas

    todas_las_variables = variables_de_graficas(solicitud)
    zonas, series_todas = obtener_datos_reporte(db, solicitud, todas_las_variables)
    propias = set(solicitud.variables)
    series = [fila for fila in series_todas if fila["variable"] in propias]
    # ancho del lienzo de las escenas segun la plantilla (IEEE: una columna de 3,5 in)
    ancho_escena = 360 if solicitud.opciones.plantilla == "ieee" else 560
    escenas = escenas_de_solicitud(db, solicitud, zonas, series_todas, ancho_escena)
    DIRECTORIO_REPORTES.mkdir(parents=True, exist_ok=True)
    nombre_archivo = f"{uuid.uuid4().hex}.{solicitud.formato}"
    destino = DIRECTORIO_REPORTES / nombre_archivo
    try:
        if solicitud.formato == "pdf":
            _crear_pdf(destino, solicitud, zonas, series, autor, escenas)
        else:
            _crear_excel(destino, solicitud, zonas, series, escenas)
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
        usuario = db.query(MapCalorUser).filter(MapCalorUser.id == reporte.usuario_id).first()
        autor = usuario.namevar if usuario is not None else None
        nombre_archivo, cantidad_registros = generar_archivo_reporte(solicitud, db, autor)
        archivo_generado = DIRECTORIO_REPORTES / nombre_archivo
        # Caché de 24 h en Redis (si cabe); si no, el archivo queda en disco como antes.
        guardado_en_cache = False
        if reporte.huella:
            guardado_en_cache = cache_reportes.guardar(reporte.huella, reporte.usuario_id, archivo_generado.read_bytes())
        reporte = db.query(Reporte).filter(Reporte.id == reporte_id).first()
        if reporte is None:
            raise RuntimeError("El reporte dejó de existir durante el procesamiento.")
        reporte.estado = "listo"
        reporte.archivo_nombre = None if guardado_en_cache else nombre_archivo
        reporte.error = None
        reporte.contenido = (
            f"Reporte generado correctamente con {cantidad_registros} registros."
        )
        db.commit()
        if guardado_en_cache:
            archivo_generado.unlink(missing_ok=True)
            archivo_generado = None
        limpiar_archivos_vencidos()
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


def limpiar_archivos_vencidos(horas: float = 24.0) -> int:
    """Borra del disco los archivos generados hace más de `horas` (el disco es solo el respaldo de lo que no cupo
    en la caché de Redis; la configuración sigue en la BD y el reporte puede regenerarse)."""
    limite = time.time() - horas * 3600
    borrados = 0
    try:
        for archivo in DIRECTORIO_REPORTES.iterdir():
            if archivo.is_file() and archivo.stat().st_mtime < limite:
                archivo.unlink(missing_ok=True)
                borrados += 1
    except OSError:
        logger.warning("No se pudo limpiar el directorio de reportes.", exc_info=True)
    return borrados


def obtener_ruta_archivo(reporte: Reporte) -> Path | None:
    if not reporte.archivo_nombre or Path(reporte.archivo_nombre).name != reporte.archivo_nombre:
        return None
    ruta = (DIRECTORIO_REPORTES / reporte.archivo_nombre).resolve()
    if ruta.parent != DIRECTORIO_REPORTES:
        return None
    return ruta
