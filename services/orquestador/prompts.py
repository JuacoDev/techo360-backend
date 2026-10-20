"""Lectura verificada del system prompt versionado (fase4-especificacion-1, 4.3).

COPIA UNICA en el backend: prompts/<version>.txt (copia byte a byte de techo360-chatbot/prompts/, que es
la fuente) y prompts/INDICE.md con su sha256. cargar_system() lee los bytes, compara el sha256 contra el
indice y solo entonces devuelve el texto; si no coincide, falla (no se envia un system alterado al modelo).
`prompts/*.txt` va como `-text` en .gitattributes para que ninguna conversion de saltos de linea cambie los bytes.
"""
import hashlib
import re
from pathlib import Path

from core.config import settings

VERSION_PROMPT_ACTUAL = settings.chatbot_system_version
DIRECTORIO_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
_FILA = re.compile(r"^\|\s*(sp-[\w.\-]+)\s*\|\s*([^|\s]+)\s*\|\s*([0-9a-f]{64})\s*\|", re.MULTILINE)


class PromptCorruptoError(RuntimeError):
    pass


def leer_indice(directorio: Path | None = None) -> dict[str, dict]:
    directorio = directorio or DIRECTORIO_PROMPTS
    contenido = (directorio / "INDICE.md").read_text(encoding="utf-8")
    return {m.group(1): {"archivo": m.group(2), "sha256": m.group(3)} for m in _FILA.finditer(contenido)}


def sha256_de(version: str, directorio: Path | None = None) -> str:
    directorio = directorio or DIRECTORIO_PROMPTS
    return hashlib.sha256((directorio / f"{version}.txt").read_bytes()).hexdigest()


def cargar_system(version: str | None = None, directorio: Path | None = None) -> str:
    version = VERSION_PROMPT_ACTUAL if version is None else version
    directorio = directorio or DIRECTORIO_PROMPTS
    indice = leer_indice(directorio)
    if version not in indice:
        raise PromptCorruptoError(f"La version {version} no figura en prompts/INDICE.md")
    crudo = (directorio / f"{version}.txt").read_bytes()
    if hashlib.sha256(crudo).hexdigest() != indice[version]["sha256"]:
        raise PromptCorruptoError(f"El sha256 de prompts/{version}.txt no coincide con prompts/INDICE.md")
    return crudo.decode("utf-8")
