"""
Punto de entrada del pipeline de ingesta. Corre todos los adaptadores
disponibles, valida esquema antes de traer todo, aplica los criterios
de aceptacion a cada registro, y notifica al administrador si algo
falla -- en vez de fallar en silencio o insertar datos corruptos.

Uso:
    python -m ingestion.run
"""
import logging
from typing import Callable

from ingestion.base import DataSourceUnavailableError, GeometryRecord, IndicadorRecord
from ingestion.db import contar_distintos_por_variable_anio, guardar_indicador, reemplazar_vias
from ingestion.fna_adapter import FNAAdapter
from ingestion.igac_catastro_adapter import IGACCatastroAdapter
from ingestion.terridata_adapter import TerriDataAdapter
from ingestion.validacion import ValidacionError, validar_indicador
from ingestion.validacion_esquema import EsquemaCambiadoError, verificar_esquema
from ingestion.vias_adapter import ViasAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Variables de tipo "indicador numerico" -> que adaptador las provee.
PLAN_DE_VARIABLES_INDICADOR = {
    "deficit_habitacional_cuantitativo": TerriDataAdapter,
    "deficit_habitacional_cualitativo": TerriDataAdapter,
    "densidad_poblacional": TerriDataAdapter,
    "cobertura_acueducto": TerriDataAdapter,
    "cobertura_alcantarillado": TerriDataAdapter,
    # tenencia_informal_terrenos: IGAC Catastro, no TerriData -- se
    # confirmo con 15+ busquedas que TerriData/CNPV no exponen este
    # dato. IGAC si lo tiene, directo, via condicion_predio='Informal'.
    # Nota: mide PREDIOS, no hogares (decision consciente del equipo,
    # ver docstring de igac_catastro_adapter.py).
    "tenencia_informal_terrenos": IGACCatastroAdapter,
    "cercania_constructoras_vis": FNAAdapter,
}

# Variables de tipo "geometria" -- distintas, se procesan aparte.
PLAN_DE_VARIABLES_GEOMETRIA = {
    "conectividad_vial": ViasAdapter,
}

# Bbox aproximado de Colombia continental completa (sur, oeste, norte, este)
# -- usado como respaldo para Overpass/OSM cuando INVIAS no responde. No es
# precisamente el area de un municipio (el pipeline no filtra vias por
# municipio todavia, trae la red completa) -- ver la nota de diseño en
# el README sobre esta simplificacion.
BBOX_COLOMBIA = (-4.23, -79.0, 13.5, -66.8)

# Backfill historico 2008-2024 -- las UNICAS 3 variables del proyecto con
# nivel municipio (no departamento) Y disponibilidad continua en esa
# ventana, confirmado corriendo la consulta real año por año contra
# TerriData. Deficit habitacional (cuantitativo/cualitativo) queda
# explicitamente fuera: es nivel departamento, se queda fijo en 34 filas
# sin importar el año, no aporta al panel de municipio.
VARIABLES_BACKFILL_CONFIRMADAS = [
    "densidad_poblacional",
    "cobertura_acueducto",
    "cobertura_alcantarillado",
]
ANIO_INICIO_BACKFILL_DEFAULT = 2008
ANIO_FIN_BACKFILL_DEFAULT = 2024


def run(
    anio: int | None = None,
    notificar_admin: Callable[[str, str], None] | None = None,
    valores_anio_anterior: dict[tuple[str, str], float] | None = None,
    database_url: str | None = None,
    variables: list[str] | None = None,
):
    """
    `database_url`: si se da, el paso 6 (guardado real) queda activo --
    cada IndicadorRecord validado se guarda en MapCalorDB, y las vias
    se reemplazan por completo en ViaDB. Si se omite (por defecto), el
    pipeline sigue funcionando en modo "solo validar y reportar" (util
    para pruebas, como se hizo hasta ahora).

    `notificar_admin(asunto, mensaje)`: callback opcional para alertar al
    administrador (ej. envolviendo email_service.send_alert). Se deja
    como inyeccion de dependencia a proposito -- este pipeline no
    depende directamente de FastAPI/Redis/el resto del backend, para
    poder probarse solo. Cuando se conecte al Backend Principal, se
    pasa algo como:
        notificar_admin=lambda asunto, msg: asyncio.run(
            email_service.send_alert(ADMIN_EMAIL, asunto, msg)
        )

    `valores_anio_anterior`: dict opcional {(municipio, variable): valor}
    del año anterior, para que la validacion pueda detectar cambios
    bruscos. Si no se da, esa comprobacion simplemente se omite.

    `variables`: si se da, restringe la corrida a esas variables
    especificas (de PLAN_DE_VARIABLES_INDICADOR y/o
    PLAN_DE_VARIABLES_GEOMETRIA) en vez de todas las del plan -- pensado
    para backfill_historico() mas abajo, que solo necesita las 3
    variables ya confirmadas, no las 7 originales del proyecto. Si se
    omite (default), corre TODAS como siempre -- no cambia el
    comportamiento del job semanal ni del uso manual existente.
    """
    valores_anio_anterior = valores_anio_anterior or {}
    alertas_ya_enviadas: set[tuple[str, str]] = set()

    def _notificar(asunto: str, mensaje: str):
        clave = (asunto,)  # dedup por asunto (ya incluye el nombre de la fuente)
        if clave in alertas_ya_enviadas:
            logger.info(f"(alerta repetida omitida, ya se avisó en esta corrida: {asunto})")
            return
        alertas_ya_enviadas.add(clave)
        logger.error(f"ALERTA -> {asunto}: {mensaje}")
        if notificar_admin is not None:
            notificar_admin(asunto, mensaje)

    resultados = {"ok": [], "rechazados": [], "advertencias": [], "bloqueadas": [], "errores": []}

    plan_indicador = (
        PLAN_DE_VARIABLES_INDICADOR if variables is None
        else {v: cls for v, cls in PLAN_DE_VARIABLES_INDICADOR.items() if v in variables}
    )
    plan_geometria = (
        PLAN_DE_VARIABLES_GEOMETRIA if variables is None
        else {v: cls for v, cls in PLAN_DE_VARIABLES_GEOMETRIA.items() if v in variables}
    )

    # ---------------- Variables de tipo indicador ----------------
    adaptadores_indicador = {}
    for variable, adapter_cls in plan_indicador.items():
        if adapter_cls not in adaptadores_indicador:
            adaptadores_indicador[adapter_cls] = adapter_cls()
        adapter = adaptadores_indicador[adapter_cls]

        if not adapter.disponible:
            logger.warning(f"[{adapter.nombre}] '{variable}' -- BLOQUEADA: {adapter.motivo_bloqueo}")
            resultados["bloqueadas"].append((variable, adapter.nombre, adapter.motivo_bloqueo))
            continue

        try:
            registros: list[IndicadorRecord] = adapter.fetch([variable], anio=anio)
            logger.info(f"[{adapter.nombre}] '{variable}' -- {len(registros)} registros obtenidos, validando...")

            aceptados = 0
            for r in registros:
                clave = (r.codigo_divipola or r.entidad_territorial, r.variable)
                valor_previo = valores_anio_anterior.get(clave)
                try:
                    resultado_val = validar_indicador(r, valor_anio_anterior=valor_previo)
                    if resultado_val.advertencia:
                        logger.warning(resultado_val.advertencia)
                        resultados["advertencias"].append(resultado_val.advertencia)
                    aceptados += 1
                    if database_url is not None:
                        try:
                            guardar_indicador(database_url, r)
                        except Exception as e:
                            _notificar(
                                f"Error guardando en BD ({adapter.nombre})",
                                f"Registro de '{r.variable}' en {r.entidad_territorial} paso "
                                f"validacion pero fallo al guardar: {e}",
                            )
                except ValidacionError as e:
                    logger.warning(f"Registro rechazado: {e}")
                    resultados["rechazados"].append(str(e))

            resultados["ok"].append((variable, adapter.nombre, aceptados, len(registros) - aceptados))

        except EsquemaCambiadoError as e:
            _notificar(f"Cambio de esquema en {adapter.nombre}", str(e))
            resultados["errores"].append((variable, adapter.nombre, str(e)))
        except DataSourceUnavailableError as e:
            logger.warning(f"[{adapter.nombre}] '{variable}' -- BLOQUEADA: {e}")
            resultados["bloqueadas"].append((variable, adapter.nombre, str(e)))
        except Exception as e:
            _notificar(f"Error inesperado en {adapter.nombre}", f"Variable '{variable}': {e}")
            resultados["errores"].append((variable, adapter.nombre, str(e)))

    # ---------------- Variables de tipo geometria ----------------
    adaptadores_geometria = {}
    for variable, adapter_cls in plan_geometria.items():
        if adapter_cls not in adaptadores_geometria:
            adaptadores_geometria[adapter_cls] = adapter_cls()
        adapter = adaptadores_geometria[adapter_cls]

        try:
            registros: list[GeometryRecord] = adapter.fetch(
                [variable], anio=anio, bbox_respaldo=BBOX_COLOMBIA
            )
            logger.info(f"[{adapter.nombre}] '{variable}' -- {len(registros)} segmentos de geometria obtenidos")
            resultados["ok"].append((variable, adapter.nombre, len(registros), 0))
            if database_url is not None and registros:
                try:
                    reemplazar_vias(database_url, registros)
                except Exception as e:
                    _notificar(
                        f"Error guardando vias en BD ({adapter.nombre})",
                        f"{len(registros)} segmentos obtenidos pero fallo el guardado: {e}",
                    )
        except Exception as e:
            _notificar(f"Error inesperado en {adapter.nombre}", f"Variable '{variable}': {e}")
            resultados["errores"].append((variable, adapter.nombre, str(e)))

    logger.info("=" * 60)
    logger.info(
        f"Resumen: {len(resultados['ok'])} fuentes OK, "
        f"{len(resultados['rechazados'])} registros rechazados, "
        f"{len(resultados['advertencias'])} advertencias, "
        f"{len(resultados['bloqueadas'])} bloqueadas, "
        f"{len(resultados['errores'])} con error"
    )
    return resultados


def backfill_historico(
    anio_inicio: int,
    anio_fin: int,
    variables: list[str],
    database_url: str,
    notificar_admin: Callable[[str, str], None] | None = None,
) -> dict[int, dict]:
    """Corre run() una vez POR CADA AÑO en [anio_inicio, anio_fin]
    (inclusive), restringido a `variables` -- reutiliza run() para la
    descarga+validacion+guardado de cada año, no duplica esa logica.

    Aisla fallos por año: si un año revienta con algo que run() mismo no
    pudo contener (deberia ser raro -- run() ya atrapa errores por
    variable internamente), se alerta por ESE año especificamente y se
    sigue con los demas, en vez de abortar todo el backfill.
    """
    resultados_por_anio: dict[int, dict] = {}
    for anio in range(anio_inicio, anio_fin + 1):
        logger.info("=" * 60)
        logger.info(f"Backfill historico -- año {anio} ({variables})")
        try:
            resultados_por_anio[anio] = run(
                anio=anio,
                variables=variables,
                database_url=database_url,
                notificar_admin=notificar_admin,
            )
        except Exception as e:
            mensaje = f"Backfill historico: fallo inesperado en el año {anio}, variables {variables}: {e}"
            logger.error(mensaje)
            if notificar_admin is not None:
                notificar_admin(f"Error en backfill historico (año {anio})", mensaje)
            resultados_por_anio[anio] = {"error": str(e)}
            # No se relanza -- se sigue con el resto de los años.

    return resultados_por_anio


def imprimir_resumen_real_backfill(
    database_url: str,
    variables: list[str],
    anio_inicio: int,
    anio_fin: int,
) -> None:
    """Consulta MapCalorDB DE VERDAD (no los contadores en memoria de
    run(), que solo miden cuantos registros se PROCESARON, no cuantos
    quedaron realmente distintos en la base) para cada (variable, año) e
    imprime un resumen legible desde la terminal, sin entrar a psql.

    Esto es la verificacion real contra el bug de julio: en esa ocasion
    el pipeline SI proceso ~1118 registros exitosamente (los contadores
    en memoria se veian bien), pero todos colapsaron en 1 sola fila de
    MapCalorDB porque compartian la misma llave unica (municipios vacio
    + nameVar + yearM) -- solo una consulta real a la base lo hubiera
    detectado en su momento."""
    print("\n" + "=" * 70)
    print("RESUMEN REAL DEL BACKFILL (consultado directo en MapCalorDB)")
    print("=" * 70)

    for variable in variables:
        print(f"\n{variable}:")
        anios_con_datos = 0
        for anio in range(anio_inicio, anio_fin + 1):
            total, distintos = contar_distintos_por_variable_anio(database_url, variable, anio)
            if total > 0:
                anios_con_datos += 1
            marca = ""
            if total > 0 and distintos < total:
                marca = "  <<< ADVERTENCIA: menos municipios distintos que filas -- posible colapso de llave unica (bug de julio)"
            print(f"  {anio}: {total} filas, {distintos} municipios distintos{marca}")

        total_anios = anio_fin - anio_inicio + 1
        print(f"  -> {anios_con_datos}/{total_anios} años con datos")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Corre el pipeline de ingesta de datos manualmente. "
                     "Sin argumentos, corre en modo SOLO-VALIDAR (no guarda nada)."
    )
    parser.add_argument(
        "--anio", type=int, default=None,
        help="Año a consultar en las fuentes (ej. 2023). Si se omite, "
             "cada fuente usa su propio comportamiento por defecto "
             "(TerriData/FNA devuelven todos los años disponibles sin filtrar). "
             "Incompatible con --anio-inicio/--anio-fin.",
    )
    parser.add_argument(
        "--anio-inicio", type=int, default=None,
        help=f"Backfill historico: primer año del rango (ej. {ANIO_INICIO_BACKFILL_DEFAULT}). "
             f"Requiere --anio-fin. Corre run() una vez por cada año del rango.",
    )
    parser.add_argument(
        "--anio-fin", type=int, default=None,
        help=f"Backfill historico: ultimo año del rango, inclusive (ej. {ANIO_FIN_BACKFILL_DEFAULT}). "
             f"Requiere --anio-inicio.",
    )
    parser.add_argument(
        "--variables", type=str, default=None,
        help="Solo con --anio-inicio/--anio-fin: lista de variables separadas "
             f"por coma. Por defecto, las 3 ya confirmadas para el backfill "
             f"2008-2024: {','.join(VARIABLES_BACKFILL_CONFIRMADAS)}.",
    )
    parser.add_argument(
        "--database-url", type=str, default=os.environ.get("DATABASE_URL"),
        help="URL de conexion a postgres-principal. Si se omite, se usa la "
             "variable de entorno DATABASE_URL (ya deberia estar seteada "
             "dentro del contenedor del backend). Si ninguna esta disponible, "
             "el pipeline corre en modo solo-validar.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fuerza el modo solo-validar aunque haya una URL de base de "
             "datos disponible -- no guarda nada, solo reporta que pasaria.",
    )
    args = parser.parse_args()

    if (args.anio_inicio is None) != (args.anio_fin is None):
        parser.error("--anio-inicio y --anio-fin deben darse juntos.")
    if args.anio is not None and args.anio_inicio is not None:
        parser.error("--anio no se puede combinar con --anio-inicio/--anio-fin.")
    if args.anio_inicio is not None and args.anio_inicio > args.anio_fin:
        parser.error(f"--anio-inicio ({args.anio_inicio}) no puede ser mayor que --anio-fin ({args.anio_fin}).")

    db_url = None if args.dry_run else args.database_url

    if db_url is None:
        print(
            "AVISO: corriendo en modo SOLO-VALIDAR -- no se va a guardar nada "
            "en la base. Usa --database-url (o la variable de entorno "
            "DATABASE_URL) para guardar de verdad.\n"
        )

    if args.anio_inicio is not None:
        variables_backfill = (
            [v.strip() for v in args.variables.split(",")] if args.variables
            else VARIABLES_BACKFILL_CONFIRMADAS
        )
        print(
            f"Backfill historico: años {args.anio_inicio}-{args.anio_fin}, "
            f"variables {variables_backfill}\n"
        )
        backfill_historico(
            args.anio_inicio, args.anio_fin, variables_backfill, database_url=db_url,
        )
        if db_url is not None:
            imprimir_resumen_real_backfill(
                db_url, variables_backfill, args.anio_inicio, args.anio_fin,
            )
        else:
            print(
                "AVISO: sin --database-url (o con --dry-run) no se guardo nada "
                "en la base -- se omite el resumen real (no hay nada que consultar)."
            )
    else:
        if args.anio is None:
            print(
                "AVISO: no se especifico --anio -- cada fuente trae su propio "
                "rango de años por defecto (revisa el resumen final para ver "
                "que años llegaron realmente).\n"
            )
        run(anio=args.anio, database_url=db_url)
