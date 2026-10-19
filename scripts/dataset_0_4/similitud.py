"""Similitud TF-IDF (uni y bigramas, sin tildes), la misma definicion de `dataset/validacion_v3_3/simlib.py` del repo
techo360-chatbot, con un indice invertido para deduplicar cientos de textos sin recorrer todos los pares.
Umbral del proyecto: 0,75 (coseno)."""
import collections
import math
import re
import unicodedata

UMBRAL = 0.75


def _norm(texto: str) -> list[str]:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", t)


def _feats(texto: str) -> list[str]:
    w = _norm(texto)
    return w + [a + "_" + b for a, b in zip(w, w[1:])]


class Tfidf:
    def __init__(self, corpus: list[str]):
        self.df: collections.Counter = collections.Counter()
        for d in corpus:
            self.df.update(set(_feats(d)))
        self.n = len(corpus)

    def vec(self, texto: str) -> dict[str, float]:
        tf = collections.Counter(_feats(texto))
        v = {}
        for k, c in tf.items():
            idf = math.log((1 + self.n) / (1 + self.df.get(k, 0))) + 1
            v[k] = (1 + math.log(c)) * idf
        norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {k: x / norma for k, x in v.items()}


def coseno(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())


class Conjunto:
    """Vectores aceptados con indice invertido: `maximo(v)` = mayor coseno contra todos los aceptados."""

    def __init__(self):
        self.vectores: list[dict] = []
        self.postings: dict[str, list[tuple[int, float]]] = collections.defaultdict(list)

    def agregar(self, v: dict) -> None:
        i = len(self.vectores)
        self.vectores.append(v)
        for k, x in v.items():
            self.postings[k].append((i, x))

    def maximo(self, v: dict) -> tuple[float, int]:
        acc: dict[int, float] = collections.defaultdict(float)
        for k, x in v.items():
            for i, y in self.postings.get(k, ()):
                acc[i] += x * y
        if not acc:
            return 0.0, -1
        i = max(acc, key=acc.get)
        return acc[i], i
