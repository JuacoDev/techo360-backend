"""Conteo de tokens con el tokenizer de Llama 3.2 (5.2, control 4). No llama a ningun modelo.

Usa `llama-tokenize` de llama.cpp con el vocabulario BPE de Llama 3 (`ggml-vocab-llama-bpe.gguf`, solo el vocabulario:
no hay pesos). Calibracion COMPROBADA: el system `sp-0.4.0-1` da 241 tokens, igual que la medicion de fase2-6.
El prompt completo replica el chat template de Llama 3.2 con la fecha fija del entrenamiento (`date_string`).
"""
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ_TECHO = Path(__file__).resolve().parents[3]
EXE = RAIZ_TECHO / "llama-cpp-bin" / "llama-tokenize.exe"
VOCAB = RAIZ_TECHO / "llama.cpp-tools" / "models" / "ggml-vocab-llama-bpe.gguf"
FECHA_ENTRENAMIENTO = "18 Sep 2026"
_TOTAL = re.compile(r"Total number of tokens:\s*(\d+)")


def prompt_hasta_asistente(system: str, user: str) -> str:
    """system + user tal como los renderiza el chat template de Llama 3.2 (hasta la cabecera del asistente)."""
    return (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        f"Cutting Knowledge Date: December 2023\nToday Date: {FECHA_ENTRENAMIENTO}\n\n{system}<|eot_id|>"
        f"<|start_header_id|>user<|end_header_id|>\n\n{user}<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\n"
    )


class Tokenizador:
    def __init__(self, cache: Path | None = None, hilos: int = 8, exe: Path = EXE, vocab: Path = VOCAB):
        if not exe.exists() or not vocab.exists():
            raise FileNotFoundError(f"Falta llama-tokenize o el vocabulario Llama 3 ({exe}, {vocab})")
        self.exe, self.vocab, self.hilos = exe, vocab, hilos
        self.cache_ruta = cache
        self.cache: dict[str, int] = {}
        if cache and cache.exists():
            self.cache = json.loads(cache.read_text(encoding="utf-8"))

    @staticmethod
    def _clave(texto: str) -> str:
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()

    def _uno(self, texto: str) -> int:
        r = subprocess.run(
            [str(self.exe), "-m", str(self.vocab), "--stdin", "--no-bos", "--show-count", "--log-disable"],
            input=texto.encode("utf-8"), capture_output=True, timeout=120,
        )
        m = _TOTAL.search(r.stdout.decode("utf-8", "replace"))
        if r.returncode != 0 or not m:
            raise RuntimeError(f"llama-tokenize fallo (codigo {r.returncode}): {r.stderr.decode('utf-8', 'replace')[:200]}")
        return int(m.group(1))

    def contar(self, textos: list[str]) -> list[int]:
        pendientes = sorted({t for t in textos if self._clave(t) not in self.cache}, key=self._clave)
        if pendientes:
            with ThreadPoolExecutor(self.hilos) as pool:
                for t, n in zip(pendientes, pool.map(self._uno, pendientes)):
                    self.cache[self._clave(t)] = n
            self.guardar()
        return [self.cache[self._clave(t)] for t in textos]

    def guardar(self) -> None:
        if self.cache_ruta:
            self.cache_ruta.parent.mkdir(parents=True, exist_ok=True)
            self.cache_ruta.write_text(json.dumps(self.cache), encoding="utf-8")

    def medir_par(self, system: str, user: str, respuesta: str) -> tuple[int, int, int]:
        """-> (tokens del prompt completo system+user con plantilla, tokens del user solo, tokens de la respuesta + eot)."""
        total, solo_user, resp = self.contar([prompt_hasta_asistente(system, user), user, respuesta + "<|eot_id|>"])
        return total, solo_user, resp
