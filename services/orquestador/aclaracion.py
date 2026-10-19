"""4.13 Aclaracion de homonimos CON MEMORIA en el backend. Sin modelo, sin historial al modelo, sin migracion.

Cuando el orquestador aclara un homonimo (o un departamento inconsistente) se guarda en Redis, por usuario y por
conversacion, que opciones se ofrecieron. En el mensaje siguiente de ESA conversacion, antes del resolutor normal,
una respuesta corta ("el de Antioquia", "el segundo", "Santander") se convierte en una eleccion inequivoca y la
pregunta original sigue su flujo con ese municipio fijado por codigo DIVIPOLA.

Estado (JSON, clave versionada aclaracion:v1:{usuario_id}:{conversacion_id}, TTL de 10 minutos):
    {"pregunta_original", "tipo", "opciones": [{"divipola", "nombre", "departamento"}], "fijados": {clave: divipola},
     "creado_en"}
`fijados` acumula las elecciones previas de la misma pregunta (dos homonimos en una pregunta se aclaran en dos
turnos). El estado NO guarda el texto de la respuesta del usuario: solo la pregunta original (recortada a
MAX_PREGUNTA y sin caracteres de control, la misma que ya quedo guardada como mensaje) y codigos.

Si Redis falla la funcion se degrada: no se guarda ni se lee nada y el flujo sigue como antes de la 4.13.
Todo vive tras CHATBOT_ORQUESTADOR_ACTIVO (solo lo invoca pipeline.responder).
"""
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date

import redis

from core.config import settings
from services.orquestador.resolutor import normalizar, resolver_anios, resolver_variables

logger = logging.getLogger(__name__)

VERSION = "v1"
TTL_SEGUNDOS = 10 * 60
MAX_RESPUESTA = 60          # caracteres: una respuesta mas larga es una pregunta nueva
MAX_PREGUNTA = 1000
MAX_OPCIONES = 10
TIPOS_CON_ESTADO = ("homonimo", "inconsistente")
TIMEOUT_REDIS_SEGUNDOS = 1
CIRCUITO_SEGUNDOS = 30      # tras un fallo de Redis no se reintenta durante este tiempo

_RELLENO = frozenset({
    "el", "la", "los", "las", "de", "del", "en", "es", "al", "a", "por", "favor", "municipio", "departamento",
    "dpto", "depto", "que", "esta", "queda", "quiero", "me", "refiero", "ese", "esa", "pues", "si", "ah", "ok",
    "claro", "gracias", "seria", "sera", "o", "y", "u",
})
_ORDINALES = {
    "primero": 1, "primera": 1, "1": 1, "1ro": 1, "1o": 1, "uno": 1,
    "segundo": 2, "segunda": 2, "2": 2, "2do": 2, "2o": 2, "dos": 2,
    "tercero": 3, "tercera": 3, "3": 3, "3ro": 3, "3o": 3, "tres": 3,
    "cuarto": 4, "cuarta": 4, "4": 4, "4to": 4, "4o": 4, "cuatro": 4,
    "quinto": 5, "quinta": 5, "5": 5, "5to": 5, "5o": 5, "cinco": 5,
    "sexto": 6, "sexta": 6, "6": 6, "6to": 6, "6o": 6, "seis": 6,
    "septimo": 7, "septima": 7, "7": 7, "7mo": 7, "7o": 7, "siete": 7,
    "octavo": 8, "octava": 8, "8": 8, "8vo": 8, "8o": 8, "ocho": 8,
    "noveno": 9, "novena": 9, "9": 9, "9no": 9, "9o": 9, "nueve": 9,
    "decimo": 10, "decima": 10, "10": 10, "10mo": 10, "10o": 10, "diez": 10,
}


# ----------------------------------------------------------------------------------------- almacen
def clave_de(usuario_id, conversacion_id) -> str:
    return f"aclaracion:{VERSION}:{int(usuario_id)}:{int(conversacion_id)}"


class AlmacenAclaraciones:
    """Redis sincrono (se invoca con asyncio.to_thread). Falla en silencio hacia 'sin estado'."""

    def __init__(self, fabrica_cliente=None, reloj=time.monotonic):
        self._fabrica = fabrica_cliente or self._cliente_real
        self._cliente = None
        self._reloj = reloj
        self._abierto_hasta = 0.0

    @staticmethod
    def _cliente_real():
        return redis.Redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_timeout=TIMEOUT_REDIS_SEGUNDOS, socket_connect_timeout=TIMEOUT_REDIS_SEGUNDOS,
        )

    def _cliente_listo(self):
        if self._reloj() < self._abierto_hasta:
            return None
        if self._cliente is None:
            self._cliente = self._fabrica()
        return self._cliente

    def _fallo(self, accion: str, exc: Exception) -> None:
        self._abierto_hasta = self._reloj() + CIRCUITO_SEGUNDOS
        logger.warning("Aclaracion con memoria: Redis fallo al %s (%s); se sigue sin estado.", accion, type(exc).__name__)

    def guardar(self, usuario_id, conversacion_id, estado: dict) -> bool:
        if usuario_id is None or conversacion_id is None:
            return False
        try:
            cliente = self._cliente_listo()
            if cliente is None:
                return False
            cliente.set(clave_de(usuario_id, conversacion_id), json.dumps(estado, ensure_ascii=False, separators=(",", ":")),
                        ex=TTL_SEGUNDOS)
            return True
        except Exception as exc:  # noqa: BLE001 -- Redis caido: degradar
            self._fallo("guardar", exc)
            return False

    def leer(self, usuario_id, conversacion_id) -> dict | None:
        if usuario_id is None or conversacion_id is None:
            return None
        try:
            cliente = self._cliente_listo()
            if cliente is None:
                return None
            crudo = cliente.get(clave_de(usuario_id, conversacion_id))
            estado = json.loads(crudo) if crudo else None
            return estado if _estado_valido(estado) else None
        except Exception as exc:  # noqa: BLE001
            self._fallo("leer", exc)
            return None

    def borrar(self, usuario_id, conversacion_id) -> None:
        if usuario_id is None or conversacion_id is None:
            return
        try:
            cliente = self._cliente_listo()
            if cliente is not None:
                cliente.delete(clave_de(usuario_id, conversacion_id))
        except Exception as exc:  # noqa: BLE001
            self._fallo("borrar", exc)


_almacen_por_defecto: AlmacenAclaraciones | None = None


def almacen_por_defecto() -> AlmacenAclaraciones:
    global _almacen_por_defecto
    if _almacen_por_defecto is None:
        _almacen_por_defecto = AlmacenAclaraciones()
    return _almacen_por_defecto


def _estado_valido(e) -> bool:
    return (
        isinstance(e, dict) and isinstance(e.get("pregunta_original"), str) and e.get("tipo") in TIPOS_CON_ESTADO
        and isinstance(e.get("opciones"), list) and 1 < len(e["opciones"]) <= MAX_OPCIONES
        and all(isinstance(o, dict) and re.fullmatch(r"\d{5}", str(o.get("divipola", ""))) and o.get("nombre") for o in e["opciones"])
        and isinstance(e.get("fijados", {}), dict)
    )


# ---------------------------------------------------------------------------------------- construir
def _limpiar(pregunta: str) -> str:
    s = "".join(c for c in (pregunta or "") if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cs", "Co"))
    return re.sub(r"\s+", " ", s).strip()[:MAX_PREGUNTA]


def construir_estado(pregunta_original: str, aclaracion: dict, fijados: dict | None, *, ahora=None) -> dict | None:
    """Estado a guardar para una aclaracion de resolver(); None si no corresponde guardarla (p. ej. 'demasiados')."""
    opciones = aclaracion.get("opciones") or []
    if aclaracion.get("tipo") not in TIPOS_CON_ESTADO or not 1 < len(opciones) <= MAX_OPCIONES:
        return None
    return {
        "pregunta_original": _limpiar(pregunta_original),
        "tipo": aclaracion["tipo"],
        "opciones": [{"divipola": o["divipola"], "nombre": o["nombre"], "departamento": o["departamento"]} for o in opciones],
        "fijados": dict(fijados or {}),
        "creado_en": int(time.time() if ahora is None else ahora),
    }


# ----------------------------------------------------------------------------------------- resolver
@dataclass
class Eleccion:
    """resultado: 'elegida' (opcion) | 'ambigua' (opciones) | 'repetir' (se entendio la forma pero no corresponde a
    ninguna opcion) | 'nueva' (no es una respuesta: pregunta nueva)."""
    resultado: str
    opcion: dict | None = None
    opciones: list | None = None


def interpretar_respuesta(texto: str, estado: dict, catalogo) -> Eleccion:
    nueva = Eleccion("nueva")
    if not texto or len(texto.strip()) > MAX_RESPUESTA:
        return nueva
    norm = normalizar(texto)
    if not norm:
        return nueva
    tokens = norm.split()
    # Una respuesta no trae variable ni anio propios: si los trae es una pregunta nueva.
    if resolver_variables(tokens, set()) or resolver_anios(texto, date.today())[0]:
        return nueva
    opciones = estado["opciones"]
    nombre = normalizar(opciones[0]["nombre"])
    palabras_nombre = set(nombre.split())
    sin_nombre = " ".join(t for t in tokens if t not in palabras_nombre)
    resto = " ".join(t for t in sin_nombre.split() if t not in _RELLENO)
    if not resto:
        return nueva  # "el de", "gracias": nada que elegir
    # ordinales ("el segundo", "la 2")
    partes = resto.split()
    if len(partes) == 1 and partes[0] in _ORDINALES:
        n = _ORDINALES[partes[0]]
        return Eleccion("elegida", opciones[n - 1]) if n <= len(opciones) else Eleccion("repetir")
    # departamento (nombre o alias del catalogo)
    codigo = catalogo.departamentos.get(resto)
    if codigo is None:
        return nueva
    coinciden = [o for o in opciones if o["divipola"][:2] == codigo]
    if len(coinciden) == 1:
        return Eleccion("elegida", coinciden[0])
    if len(coinciden) > 1:
        return Eleccion("ambigua", opciones=coinciden)
    return Eleccion("repetir")


def texto_homonimo(opciones: list[dict]) -> str:
    """Misma plantilla fija que resolutor.resolver (homonimo); tambien al repetir una aclaracion acotada."""
    lista = ", ".join(f"{o['nombre']} ({o['departamento']})" for o in opciones)
    return f"Hay varios municipios llamados {opciones[0]['nombre']}: {lista}. ¿A cuál te refieres?"


def clave_fijado(opcion: dict) -> str:
    return normalizar(opcion["nombre"])
