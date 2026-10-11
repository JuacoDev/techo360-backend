"""Limpieza de entradas VIEJAS del cache de zonas de proyeccion (Redis).

Contexto (2026-10-10): el contrato de GET /api/proyecciones/zonas cambio (origen,
fiabilidad, componentes; es_real = "no es proyeccion-*"), y la clave paso de
`proyeccion:v2:zonas:{indicador}:{anio}` a `proyeccion:v3:zonas:...`
(services/proyeccion_cache.py::VERSION_ZONAS). El backend nuevo nunca lee la v2: sus
entradas quedan sin uso y expiran solas por TTL (36 h). Este script existe para borrarlas
antes si se quiere liberar la memoria ya, o si algo las lee por error.

NO toca: proyeccion:v2:limites_base:* (mismo contenido), proyeccion:serie:* ni zonas:v1:*
(cache de GET /api/zonas, cuyo contenido no cambio).

POR DEFECTO SOLO LISTA (dry-run). Para borrar hay que pasar --ejecutar. No se ejecuta
en produccion sin autorizacion de Juan.

Uso (dentro del contenedor del backend, que ya tiene REDIS_URL):
    docker exec techo360-backend python -m scripts.limpiar_cache_proyeccion            # lista
    docker exec techo360-backend python -m scripts.limpiar_cache_proyeccion --ejecutar # borra
"""
import argparse
import sys

VERSION_VIEJA_POR_DEFECTO = "v2"


def limpiar(cliente, version: str = VERSION_VIEJA_POR_DEFECTO, ejecutar: bool = False):
    """Devuelve (claves_encontradas, cantidad_borrada). Usa SCAN (no KEYS: no bloquea Redis)."""
    from services.proyeccion_cache import VERSION_ZONAS  # import tardio: solo para la guarda

    if version == VERSION_ZONAS:
        raise ValueError(f"'{version}' es la version VIGENTE de zonas; no se borra.")
    patron = f"proyeccion:{version}:zonas:*"
    encontradas = list(cliente.scan_iter(match=patron, count=100))
    borradas = 0
    if ejecutar:
        for clave in encontradas:
            borradas += int(cliente.delete(clave))
    return encontradas, borradas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", default=VERSION_VIEJA_POR_DEFECTO, help="version a limpiar (default: v2)")
    parser.add_argument("--ejecutar", action="store_true", help="borrar de verdad (sin esto solo lista)")
    args = parser.parse_args(argv)

    import redis

    from core.config import settings

    cliente = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    encontradas, borradas = limpiar(cliente, version=args.version, ejecutar=args.ejecutar)
    for clave in sorted(encontradas):
        ttl = cliente.ttl(clave)
        print(f"{'BORRADA' if args.ejecutar else 'encontrada'}: {clave} (ttl={ttl}s)")
    print(f"{len(encontradas)} claves de zonas {args.version}; borradas: {borradas}"
          + ("" if args.ejecutar else " (dry-run: usa --ejecutar para borrar)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
