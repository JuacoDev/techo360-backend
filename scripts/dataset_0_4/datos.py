"""Acceso de SOLO LECTURA a mapcalordb y a las funciones reales del orquestador (5.2).

Este modulo no escribe en la base: la sesion se abre con `default_transaction_read_only = on`, se
confirma con SHOW y todas las consultas son parametrizadas. El contexto de cada par NUNCA se escribe a
mano: `renderizar_usuario` llama a `construir_contexto` y `mensaje_usuario` del orquestador del backend.

Piezas inyectadas (el orquestador las acepta por parametro; en produccion son Redis/RPC):
  * leer_serie_fn  -> cache en memoria con el MISMO SELECT que `contexto.leer_serie` (una sola consulta
                      por variable en vez de una por municipio).
  * completar      -> `calcular_punto` de techo360-proyeccion (el mismo nucleo que atiende el RPC "puntos"),
                      con un repositorio que lee del cache. Sin Redis ni RabbitMQ.
  * limites del score -> `calcular_limites_base` del backend (misma funcion que usa produccion).
"""
import asyncio
import importlib
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

RAIZ_BACKEND = Path(__file__).resolve().parents[2]
if str(RAIZ_BACKEND) not in sys.path:
    sys.path.insert(0, str(RAIZ_BACKEND))

from services import proyeccion_service as pservicio  # noqa: E402
from services.orquestador import alias as al  # noqa: E402
from services.orquestador import contexto as cx  # noqa: E402
from services.orquestador import ensamblador as ens  # noqa: E402
from services.orquestador.resolutor import Resolucion, cargar_catalogo  # noqa: E402
from services.origen import es_proyeccion  # noqa: E402
from services.score_privacion import calcular_limites_base  # noqa: E402

HOY = date(2026, 10, 17)  # fija: el resolutor usa la fecha solo para «este año» / «el año pasado»
VARIABLES_BD = (al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DEFICIT_CUANTITATIVO, al.V_DEFICIT_CUALITATIVO, al.V_DENSIDAD)
META = {"usuario_id": 0, "consentimiento_entrenamiento": False}


def _cargar_proyeccion(directorio: Path):
    """Importa app.services.puntos de techo360-proyeccion (solo lectura; el modulo no importa nada de red)."""
    if not (directorio / "app" / "services" / "puntos.py").exists():
        raise FileNotFoundError(f"No encuentro app/services/puntos.py en {directorio}")
    if str(directorio) not in sys.path:
        sys.path.append(str(directorio))
    return importlib.import_module("app.services.puntos")


def _leer_database_url(ruta_env: Path) -> str:
    for linea in ruta_env.read_text(encoding="utf-8").splitlines():
        if linea.startswith("DATABASE_URL="):
            return linea.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError(f"DATABASE_URL no esta en {ruta_env}")


class Datos:
    """Catalogo, series y limites del score cargados una vez; `renderizar_usuario` arma el mensaje real."""

    def __init__(self, directorio_proyeccion: Path, ruta_env: Path | None = None):
        self.puntos = _cargar_proyeccion(directorio_proyeccion)
        url = _leer_database_url(ruta_env or RAIZ_BACKEND / ".env")
        engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on", "connect_timeout": 20})
        self.db = sessionmaker(bind=engine)()
        valor = self.db.execute(text("SHOW default_transaction_read_only")).scalar()
        if valor != "on":
            raise RuntimeError("La sesion NO esta en solo lectura (default_transaction_read_only != on): se aborta.")
        self.solo_lectura = valor
        self.catalogo = cargar_catalogo(self.db)
        self.series: dict[tuple[str, str], list[dict]] = {}
        self._cargar_series()
        self.limites = calcular_limites_base(self.db, pservicio.ANIO_BASE_SCORE)
        self.universo = set(self.catalogo.por_codigo)

        async def _limites(_db):  # sustituye al cache Redis de los limites; el calculo es el mismo
            return self.limites

        pservicio._obtener_limites_base_cacheados = _limites

    # ------------------------------------------------------------------ lectura
    def _cargar_series(self) -> None:
        filas = self.db.execute(
            text("""
                SELECT codigodivipola, namevar, yearm, valuevar, originvar, intervaloconfianza
                FROM public.mapcalordb
                WHERE namevar = ANY(:variables)
                ORDER BY codigodivipola, namevar, yearm
            """),
            {"variables": list(VARIABLES_BD)},
        ).mappings().all()
        for f in filas:
            self.series.setdefault((f["codigodivipola"], f["namevar"]), []).append({
                "anio": int(f["yearm"]), "valor": None if f["valuevar"] is None else float(f["valuevar"]),
                "origen": f["originvar"], "intervalo": f["intervaloconfianza"],
            })

    def serie(self, codigo: str, variable: str) -> list[dict]:
        return self.series.get((codigo, variable), [])

    def municipio(self, codigo: str) -> dict:
        m = self.catalogo.por_codigo[codigo]
        return {"divipola": m.divipola, "nombre": m.nombre, "departamento": self.catalogo.nombre_departamento[m.cod_departamento]}

    # ------------------------------------------------------------ dobles de produccion
    def _repo(self):
        datos = self

        class Repo:
            @staticmethod
            def en_universo(codigo):
                return codigo in datos.universo

            @staticmethod
            def obtener_serie(indicador, codigo):
                return [{"anio": f["anio"], "valor": f["valor"], "origen": f["origen"]} for f in datos.serie(codigo, indicador)]

        return Repo()

    def _fabrica(self, huecos: dict | None):
        """-> (leer_serie_fn, completar, score_fn). `huecos` {(variable, codigo, anio): motivo|None}:
        escenarios que la BD no da de forma natural. La fila se oculta (hueco) y, si hay motivo, el
        punto sale como «sin_dato» con ese motivo; sin motivo se calcula con el nucleo real."""
        huecos = huecos or {}
        repo = self._repo()
        datos = self

        async def leer(codigo, variable):
            serie = datos.serie(codigo, variable)
            ocultos = {a for (v, c, a) in huecos if v == variable and c == codigo}
            return [f for f in serie if f["anio"] not in ocultos] if ocultos else serie

        async def completar(pedidos):
            salida = []
            for p in pedidos:
                clave = (p["indicador"], p["codigo"], int(p["anio"]))
                if clave in huecos and huecos[clave] is not None:
                    salida.append(datos.puntos._sin_dato(p["indicador"], p["codigo"], int(p["anio"]), huecos[clave]))
                    continue
                if clave in huecos:  # hueco sin motivo: serie sin esa fila
                    class RepoHueco:
                        en_universo = staticmethod(repo.en_universo)

                        @staticmethod
                        def obtener_serie(indicador, codigo):
                            ocultos = {a for (v, c, a) in huecos if v == indicador and c == codigo}
                            return [f for f in repo.obtener_serie(indicador, codigo) if f["anio"] not in ocultos]

                    salida.append(datos.puntos.completar_puntos([p], repo=RepoHueco())[0])
                else:
                    salida.append(datos.puntos.completar_puntos([p], repo=repo)[0])
            return salida

        async def leer_fila(variable, codigo, anio):
            if (variable, codigo, anio) in huecos:
                return None
            for f in datos.serie(codigo, variable):
                if f["anio"] == anio and f["valor"] is not None:
                    return {"valuevar": f["valor"], "originvar": f["origen"], "intervaloconfianza": f["intervalo"]}
            return None

        async def score_fn(codigo, anio):
            return await pservicio.calcular_score_punto(
                codigo, anio, None,
                leer_fila=lambda variable: leer_fila(variable, codigo, anio),
                completar=completar, limites=datos.limites,
            )

        return leer, completar, score_fn

    # ------------------------------------------------------------------ render
    def resolucion(self, entrada: dict) -> Resolucion:
        return Resolucion(
            municipios=[self.municipio(c) for c in entrada["municipios"]],
            variables=list(entrada["variables"]),
            anios=list(entrada["anios"]),
            rango=None,
            anio_implicito=bool(entrada["anio_implicito"]),
        )

    def contexto(self, entrada: dict) -> cx.ContextoConstruido:
        huecos = {(h["variable"], h["divipola"], int(h["anio"])): h.get("motivo") for h in entrada.get("huecos", [])}
        leer, completar, score_fn = self._fabrica(huecos)
        return asyncio.run(cx.construir_contexto(
            self.resolucion(entrada), self.db, hoy_anio=HOY.year,
            leer_serie_fn=leer, completar=completar, score_fn=score_fn,
        ))

    def renderizar(self, entrada: dict) -> dict:
        """-> {"user": mensaje real, "items": [...], "lineas": [...], "omitidos": n}. Usa `ensamblar`
        del backend (la misma funcion que arma la peticion en produccion)."""
        contexto = self.contexto(entrada)
        paquete = ens.ensamblar(entrada["pregunta"], contexto, dict(META))
        user = paquete["peticion"]["mensajes"][1]["contenido"]
        lineas_usuario = user.split("\n\nPREGUNTA DEL USUARIO\n")[0].split("\n")[1:]
        return {"user": user, "items": paquete["peticion"]["contexto"], "lineas": lineas_usuario,
                "omitidos": paquete["notas"]["omitidos"], "notas": paquete["notas"]}


    # ------------------------------------------------------------------ indices de apoyo (solo lectura)
    def valores(self, codigo: str, variable: str) -> dict[int, dict]:
        return {f["anio"]: f for f in self.serie(codigo, variable) if f["valor"] is not None}

    def anios_reales(self, codigo: str, variable: str) -> list[int]:
        """Anios con fila REAL. Para el score: anios en que sus tres componentes son reales."""
        if variable == al.V_SCORE:
            comp = [set(self.anios_reales(codigo, v)) for v in (al.V_ACUEDUCTO, al.V_ALCANTARILLADO, al.V_DEFICIT_CUANTITATIVO)]
            return sorted(set.intersection(*comp))
        return sorted(a for a, f in self.valores(codigo, variable).items() if not es_proyeccion(f["origen"]))

    def anios_proyeccion(self, codigo: str, variable: str) -> list[int]:
        """Anios con fila de PROYECCION en la base."""
        if variable == al.V_SCORE:
            return list(range(2019, 2043))
        return sorted(a for a, f in self.valores(codigo, variable).items() if es_proyeccion(f["origen"]))

    def huecos_naturales(self) -> dict[tuple[str, str], list[tuple[int, str]]]:
        """{(variable, codigo): [(anio, resultado)]} de los anios del rango de la variable SIN fila, con lo que
        devuelve el nucleo real de techo360-proyeccion: «calculado» o el motivo de «sin_dato»."""
        if getattr(self, "_huecos", None) is not None:
            return self._huecos
        repo = self._repo()
        huecos: dict[tuple[str, str], list[tuple[int, str]]] = {}
        for variable in VARIABLES_BD:
            desde, hasta = self.puntos.RANGO_ANIOS[variable]
            for codigo in sorted(self.universo):
                presentes = self.valores(codigo, variable)
                serie = repo.obtener_serie(variable, codigo)
                for anio in range(desde, hasta + 1):
                    if anio in presentes:
                        continue
                    r = self.puntos.calcular_punto(variable, codigo, anio, serie, True)
                    huecos.setdefault((variable, codigo), []).append((anio, "calculado" if r["estado"] == "calculado" else r["motivo"]))
        self._huecos = huecos
        return huecos
