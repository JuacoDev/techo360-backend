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
from ingestion.db import guardar_indicador, reemplazar_vias
from ingestion.fna_adapter import FNAAdapter
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
    "tenencia_informal_terrenos": TerriDataAdapter,
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


def run(
    anio: int | None = None,
    notificar_admin: Callable[[str, str], None] | None = None,
    valores_anio_anterior: dict[tuple[str, str], float] | None = None,
    database_url: str | None = None,
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

    # ---------------- Variables de tipo indicador ----------------
    adaptadores_indicador = {}
    for variable, adapter_cls in PLAN_DE_VARIABLES_INDICADOR.items():
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
    for variable, adapter_cls in PLAN_DE_VARIABLES_GEOMETRIA.items():
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
             "(TerriData/FNA devuelven todos los años disponibles sin filtrar).",
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

    db_url = None if args.dry_run else args.database_url

    if db_url is None:
        print(
            "AVISO: corriendo en modo SOLO-VALIDAR -- no se va a guardar nada "
            "en la base. Usa --database-url (o la variable de entorno "
            "DATABASE_URL) para guardar de verdad.\n"
        )
    if args.anio is None:
        print(
            "AVISO: no se especifico --anio -- cada fuente trae su propio "
            "rango de años por defecto (revisa el resumen final para ver "
            "que años llegaron realmente).\n"
        )

    run(anio=args.anio, database_url=db_url)
