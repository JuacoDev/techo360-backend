"""Paso 6.6 / prueba con el modelo: techo360-chatbot:v0.4.0 (Ollama local) con el contexto REAL del orquestador.

Brazos (3 corridas, semillas 1-3, temperatura 0,3 y demas opciones de produccion; num_thread limitado):
  ctx40_rag   las preguntas conceptuales de claude/fase6-4-preguntas.jsonl con el contexto RAG ensamblado por
              preparar_consulta (CHATBOT_RAG_ACTIVO simulado) contra la base LOCAL con el corpus
  ctx40_sin   las mismas 40 sin RAG (linea de comparacion; no la pedia la tarea)
  c3_sin      las 20 C3 de extra_5_6b.jsonl tal como estan (CONTEXTO: Sin datos disponibles)
  c3_rag      las mismas 20 preguntas con el contexto RAG

Mide con el validador de cifras 4.5 (el de produccion) y marca para REVISION HUMANA lo sospechoso; TODAS las
respuestas van a fallos_rag.csv. Heuristicas (no sustituyen la revision): cifras no validadas, poco soporte lexico
en los fragmentos, copia del formato [dN]/«Fuente:». El modelo 0.4.0 nunca vio lineas [dN]: se informa, no se corrige.

Uso (desde techo360-backend, con RAG_TEST_DATABASE_URL a una base LOCAL y Ollama en 127.0.0.1:11434):
    python -m scripts.rag.evaluar_modelo_rag --preguntas ../claude/fase6-4-preguntas.jsonl \\
        --extra ../trabajo-dataset-0.4.0-r2/evaluacion/extra_5_6b.jsonl --salida ../trabajo-rag-6-5
"""
import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from services.orquestador import rag
from services.orquestador.orquestador import preparar_consulta
from services.orquestador.validador_cifras import validar_cifras
from services.tests_orquestador import catalogo_sintetico

OLLAMA = "http://127.0.0.1:11434"
HOY = date(2026, 10, 18)
META = {"usuario_id": 1, "consentimiento_entrenamiento": False, "conversacion_id": 1, "mensaje_id": 1, "intento": 1}
OPCIONES = {"temperature": 0.3, "top_p": 0.9, "repeat_penalty": 1.15, "num_ctx": 2048, "num_predict": 400}
SIN_CONTEXTO = "Sin datos disponibles para esta consulta."
FRASES_ABSTENCION = ("no tengo esa definicion", "no tengo la definicion", "no cuento con la definicion", "no tengo informacion",
                     "no dispongo de", "no tengo datos", "no cuento con", "no tengo esa informacion", "no tengo ese dato",
                     "no puedo confirmar", "no tengo el detalle", "no figura", "no tengo disponible")
RELLENO = frozenset("para como entre sobre desde hasta donde cuando porque tambien segun esta este estos estas cada otro otra "
                    "otros otras tiene tienen puede pueden debe deben mismo misma muy mas menos solo cual cuales".split())


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def palabras_contenido(s: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", fold(s)) if len(p) >= 5 and p not in RELLENO and not p.isdigit()}


def leer_jsonl(ruta: Path) -> list[dict]:
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def armar(pregunta: str, buscar_fn, con_rag: bool) -> dict:
    """-> {system, user, lineas, items, fragmentos}: lo que el orquestador enviaria al modelo."""
    async def _uno():
        return await preparar_consulta(pregunta, None, dict(META), hoy=HOY, catalogo=catalogo_sintetico(), rag_fn=buscar_fn)
    with patch.object(settings, "chatbot_rag_activo", con_rag):
        r = asyncio.run(_uno())
    if r["accion"] != "consultar":
        raise RuntimeError("aclaracion inesperada: " + pregunta)
    msj = r["peticion"]["mensajes"]
    bloque = msj[1]["contenido"].split("\n\nPREGUNTA DEL USUARIO\n")[0]
    lineas = [l for l in bloque.split("\n")[1:] if l and l != SIN_CONTEXTO]
    return {"system": msj[0]["contenido"], "user": msj[1]["contenido"], "lineas": lineas,
            "items": r["peticion"]["contexto"], "fragmentos": r["notas"]["rag_fragmentos"]}


def generar(modelo: str, system: str, user: str, semilla: int, hilos: int) -> dict:
    cuerpo = {"model": modelo, "stream": False, "keep_alive": "10m",
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
              "options": {**OPCIONES, "seed": semilla, "num_thread": hilos}}
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    return {"respuesta": d["message"]["content"], "tokens_prompt": d.get("prompt_eval_count"), "tokens": d.get("eval_count"),
            "termina": d.get("done_reason") == "stop", "total_s": round(d.get("total_duration", 0) / 1e9, 2)}


def medir(g: dict, pregunta: str, armado: dict, con_rag: bool) -> dict:
    resp = g["respuesta"]
    lineas = armado["lineas"] or [SIN_CONTEXTO]
    val = validar_cifras(resp, lineas, armado["items"], pregunta)
    texto_frag = " ".join(l.split("] ", 1)[1] if l.startswith("[d") else l for l in armado["lineas"])
    resp_p = palabras_contenido(resp)
    soporte = (len(resp_p & palabras_contenido(texto_frag + " " + pregunta)) / len(resp_p)) if resp_p else 1.0
    rf = fold(resp)
    m = {
        "cifras_no_validadas": val["no_validadas"],
        "abstiene": any(f in rf for f in FRASES_ABSTENCION),
        "soporte_lexico": round(soporte, 2),
        "copia_formato": bool(re.search(r"\[d\d+\]", resp) or "Fuente:" in resp),
        "no_termina": not g["termina"],
    }
    m["revisar"] = bool(m["cifras_no_validadas"]) or (con_rag and soporte < 0.5 and not m["abstiene"]) or m["copia_formato"] or m["no_termina"]
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preguntas", type=Path, required=True)
    ap.add_argument("--extra", type=Path, required=True)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--modelo", default="techo360-chatbot:v0.4.0")
    ap.add_argument("--corridas", type=int, default=3)
    ap.add_argument("--hilos", type=int, default=3)
    ap.add_argument("--excluir", nargs="*", default=[], help="ids de pregunta que no se evaluan (p. ej. rag-39)")
    a = ap.parse_args()
    dsn = os.environ.get("RAG_TEST_DATABASE_URL") or sys.exit("Falta RAG_TEST_DATABASE_URL (base LOCAL)")
    if not re.search(r"@(?:localhost|127\.0\.0\.1)[:/]", dsn):
        sys.exit("Rechazado: la base debe ser local")
    Sesion = sessionmaker(bind=create_engine(dsn))

    def buscar(q):
        s = Sesion()
        try:
            return rag.buscar_fragmentos(s, q)
        finally:
            s.rollback()
            s.close()

    conc = [p for p in leer_jsonl(a.preguntas) if p["tipo"] == "conceptual" and p["id"] not in a.excluir]
    c3 = [p for p in leer_jsonl(a.extra) if p["subfamilia"] == "C3"]
    tareas = []  # (brazo, id, pregunta, con_rag)
    for p in conc:
        tareas.append(("ctx40_rag", p["id"], p["pregunta"], True))
        tareas.append(("ctx40_sin", p["id"], p["pregunta"], False))
    for p in c3:
        q = p["instruction"].split("PREGUNTA DEL USUARIO\n", 1)[1]
        tareas.append(("c3_sin", p["id"], q, False))
        tareas.append(("c3_rag", p["id"], q, True))
    a.salida.mkdir(parents=True, exist_ok=True)
    crudo = a.salida / "crudo_rag.jsonl"
    hechas = {(g["brazo"], g["id"], g["corrida"]) for g in leer_jsonl(crudo)} if crudo.exists() else set()
    armados = {(b, i): armar(q, buscar, r) for b, i, q, r in tareas}
    total = len(tareas) * a.corridas
    print(f"{total} generaciones, {len(hechas)} ya hechas; modelo {a.modelo}, {a.hilos} hilos", flush=True)
    t0, n = time.time(), 0
    with crudo.open("a", encoding="utf-8", newline="\n") as f:
        for corrida in range(1, a.corridas + 1):
            for brazo, id_, q, con_rag in tareas:
                if (brazo, id_, corrida) in hechas:
                    continue
                ar = armados[(brazo, id_)]
                g = generar(a.modelo, ar["system"], ar["user"], corrida, a.hilos)
                f.write(json.dumps({"brazo": brazo, "id": id_, "corrida": corrida, "pregunta": q, "fragmentos": ar["fragmentos"],
                                    "tokens_contexto": g["tokens_prompt"], **g}, ensure_ascii=False) + "\n")
                f.flush()
                n += 1
                if n % 20 == 0:
                    print(f"  {len(hechas) + n}/{total}  {(time.time() - t0) / 60:.1f} min", flush=True)
    # medicion
    filas, resumen = [], defaultdict(lambda: defaultdict(list))
    for g in leer_jsonl(crudo):
        ar = armados[(g["brazo"], g["id"])]
        con_rag = g["brazo"].endswith("_rag")
        m = medir(g, g["pregunta"], ar, con_rag)
        filas.append([g["brazo"], g["id"], g["corrida"], g["pregunta"], " | ".join(g["fragmentos"]), g["tokens_contexto"],
                      "; ".join(m["cifras_no_validadas"]), m["abstiene"], m["soporte_lexico"], m["copia_formato"], m["no_termina"],
                      "SI" if m["revisar"] else "", g["respuesta"].replace("\n", " / ")])
        r = resumen[g["brazo"]]
        r["n"].append(1)
        r["cifras"].append(bool(m["cifras_no_validadas"]))
        r["abstiene"].append(m["abstiene"])
        r["copia"].append(m["copia_formato"])
        r["revisar"].append(m["revisar"])
        r["soporte"].append(m["soporte_lexico"])
        r["tokens_ctx"].append(g["tokens_contexto"] or 0)
        r["total_s"].append(g["total_s"])
    with (a.salida / "fallos_rag.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["brazo", "id", "corrida", "pregunta", "fragmentos", "tokens_prompt", "cifras_no_validadas", "se_abstiene",
                    "soporte_lexico", "copia_formato_dN", "no_termina", "revisar", "respuesta"])
        w.writerows(sorted(filas, key=lambda x: (x[11] != "SI", x[0], x[1], x[2])))
    out = {}
    for brazo, r in resumen.items():
        n_ = len(r["n"])
        out[brazo] = {"respuestas": n_, "con_cifra_no_validada": sum(r["cifras"]), "se_abstienen": sum(r["abstiene"]),
                      "copian_formato_dN": sum(r["copia"]), "marcadas_para_revision": sum(r["revisar"]),
                      "soporte_lexico_medio": round(sum(r["soporte"]) / n_, 3), "tokens_prompt_medio": round(sum(r["tokens_ctx"]) / n_),
                      "total_s_mediano": sorted(r["total_s"])[n_ // 2]}
    (a.salida / "resumen_rag.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
