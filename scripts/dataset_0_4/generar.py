"""Generador de pares 0.4.0 (paso 5.2). Solo lectura de la base; sin modelos, sin entrenamiento, sin red.

    python -m scripts.dataset_0_4.generar --salida ../trabajo-dataset-0.4.0

Entradas: la base de datos (DATABASE_URL de techo360-backend/.env, sesion en solo lectura), techo360-proyeccion (nucleo
de huecos), la rama feat/dataset-0.4.0-base de techo360-chatbot (train base y holdouts, leidos con `git show`) y
llama-tokenize con el vocabulario Llama 3. Misma semilla => mismos archivos byte a byte.
"""
import argparse
import collections
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
from pathlib import Path

from services.orquestador.prompts import VERSION_PROMPT_ACTUAL, cargar_system, sha256_de

from . import controles, resp_b, similitud
from .datos import HOY, Datos
from .familias import Candidato, Generador
from .lengua import sin_tildes
from .tokens import Tokenizador

SEMILLA = 20261017
MAX_TOKENS_ENTRADA = 1648
MAX_TOKENS_RESPUESTA = 400
MAX_PARES_POR_TERNA = 3

# subfamilia -> (familia, cuota train, cuota holdout, [(bucket, peso, metodo, kwargs)])
PLAN = {
    "A1": ("A", 140, 8, [("unico", 1, "A1", {})]),
    "A2": ("A", 90, 5, [("unico", 1, "A2", {})]),
    "A3": ("A", 90, 5, [("chico", 10, "A3", {"tamano": "chico"}), ("medio", 65, "A3", {"tamano": "medio"}), ("grande", 15, "A3", {"tamano": "grande"})]),
    "A4": ("A", 80, 4, [("chico", 10, "A4", {"tamano": "chico"}), ("medio", 55, "A4", {"tamano": "medio"}), ("grande", 15, "A4", {"tamano": "grande"})]),
    "A5": ("A", 90, 4, [("chico", 5, "A5", {"tamano": "chico"}), ("medio", 60, "A5", {"tamano": "medio"}), ("grande", 25, "A5", {"tamano": "grande"})]),
    "A6": ("A", 50, 2, [("uno", 25, "A6", {"forma": "uno"}), ("vars", 12, "A6", {"forma": "vars"}), ("munis", 13, "A6", {"forma": "munis"})]),
    "A7": ("A", 40, 2, [("unico", 1, "A7", {})]),
    "B1": ("B", 55, 6, [("sin_historia", 18, "B1", {"tipo": "sin_historia"}), ("fuera_de_rango", 10, "B1", {"tipo": "fuera_de_rango"}),
                        ("sin_dato_real", 8, "B1", {"tipo": "sin_dato_real"}), ("sin_extremo", 6, "B1", {"tipo": "sin_extremo"}),
                        ("componente", 7, "B1", {"tipo": "componente"}), ("sospechoso", 6, "B1", {"tipo": "sospechoso"})]),
    "B2": ("B", 40, 0, [("municipio", 14, "B2", {"tipo": "municipio"}), ("agregado", 14, "B2", {"tipo": "agregado"}), ("ajena", 12, "B2", {"tipo": "ajena"})]),
    "B3": ("B", 25, 2, [("vars", 12, "B3", {"tipo": "vars"}), ("anios", 8, "B3", {"tipo": "anios"}), ("munis", 5, "B3", {"tipo": "munis"})]),
    "B4": ("B", 15, 2, [("antes", 8, "B4", {"tipo": "antes"}), ("despues", 7, "B4", {"tipo": "despues"})]),
    "B5": ("B", 25, 2, [("hueco", 15, "B5", {"tipo": "hueco"}), ("vacio", 10, "B5", {"tipo": "vacio"})]),
    "C1": ("C", 100, 2, [("unico", 1, "C1", {})]),
    "C2": ("C", 60, 2, [("unico", 1, "C2", {})]),
    "C3": ("C", 80, 2, [("unico", 1, "C3", {})]),
    "D1": ("D", 35, 3, [("unico", 1, "D1", {})]),
    "D2": ("D", 20, 0, [("unico", 1, "D2", {})]),
    "D3": ("D", 30, 0, [("unico", 1, "D3", {})]),
    "D4": ("D", 35, 3, [("geo", 25, "D4", {"tipo": "geo"}), ("hablame", 10, "D4", {"tipo": "hablame"})]),
    "E1": ("E", 35, 3, [("unico", 1, "E1", {})]),
    "E2": ("E", 35, 3, [("unico", 1, "E2", {})]),
    "F": ("F", 30, 0, [("datos", 20, "F", {"tipo": "datos"}), ("pura", 10, "F", {"tipo": "pura"})]),
}
FAMILIAS = ["A", "B", "C", "D", "E", "F"]
GRANDES = {"A1", "A2", "A3", "A4", "A5", "A6", "B1", "C1", "C2", "C3"}  # cuota >= 50
NOMBRE_FAMILIA = {"A": "Con contexto", "B": "Abstención", "C": "Sin contexto", "D": "Geografía", "E": "Homónimos", "F": "Inyección"}


# --------------------------------------------------------------------------- lectura de la base 0.4.0 (chatbot)
def _git_show(repo: Path, ref: str, ruta: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{ruta}"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git show {ref}:{ruta} fallo: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return r.stdout.decode("utf-8")


def _jsonl(texto: str) -> list[dict]:
    return [json.loads(l) for l in texto.replace("\r\n", "\n").split("\n") if l.strip()]


def leer_base(repo: Path, ref: str) -> dict:
    return {
        "train": _jsonl(_git_show(repo, ref, "training/v0_4_0/entrenamiento_v0_4_0_base.jsonl")) +
                 _jsonl(_git_show(repo, ref, "training/v0_4_0/actualizar_aplicado.jsonl")),
        "holdout_v3_3": _jsonl(_git_show(repo, ref, "dataset/holdout_v3_3.jsonl")),
        "holdout_v0_4": _jsonl(_git_show(repo, ref, "training/v0_4_0/holdout_v0_4.jsonl")),
    }


def sha256_archivo(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- reservados
def elegir_reservados(datos: Datos, semilla: int, fraccion: float = 0.05, grupos_reservados: int = 3) -> list[str]:
    """~5 % de los municipios, elegidos con la semilla. Incluye `grupos_reservados` grupos de homonimos COMPLETOS (de
    tamano 2) para el holdout E; el resto son municipios sin nombre sensible, sin homonimos y sin alias de ciudad."""
    import random
    rng = random.Random(f"{semilla}:reservados")
    gen = Generador(datos, semilla, set(), "train")
    objetivo = round(len(datos.universo) * fraccion)
    grupos2 = sorted(k for k, v in gen.homonimos.items() if len(v) == 2)
    elegidos_g = rng.sample(grupos2, grupos_reservados)
    reservados = {c for k in elegidos_g for c in gen.homonimos[k]}
    from .familias import CIUDADES, SIN_DATO_REAL
    elegibles = [c for c in sorted(datos.universo)
                 if c not in gen.nombre_hom and c not in gen.cod_sensibles and c not in CIUDADES and c not in SIN_DATO_REAL and c not in reservados]
    rng.shuffle(elegibles)
    reservados |= set(elegibles[: max(0, objetivo - len(reservados))])
    return sorted(reservados)


# --------------------------------------------------------------------------- motor de generacion
class Motor:
    def __init__(self, datos, gen: Generador, tok: Tokenizador, system: str, holdouts_preguntas: list[str], modo: str, cuotas: dict[str, int]):
        self.datos, self.gen, self.tok, self.system, self.modo, self.cuotas = datos, gen, tok, system, modo, cuotas
        self.hold_preguntas = holdouts_preguntas
        self.descartes: collections.Counter = collections.Counter()
        self.aceptados: dict[str, list[Candidato]] = {s: [] for s in cuotas}
        self.por_bucket: dict[str, collections.Counter] = {s: collections.Counter() for s in cuotas}
        self.ternas: collections.Counter = collections.Counter()
        self.sim_max_q: dict[str, list[float]] = collections.defaultdict(list)

    def descarta(self, sub, motivo):
        self.descartes[(sub, motivo)] += 1

    # un lote de candidatos con controles baratos
    def lote(self, sub: str, n: int) -> list[Candidato]:
        familia, _, _, buckets = PLAN[sub]
        objetivo = {b: peso for b, peso, _, _ in buckets}
        total_peso = sum(objetivo.values())
        cuota = self.cuotas[sub]
        meta_b = {b: cuota * p / total_peso for b, p in objetivo.items()}
        deficit = {b: max(meta_b[b] - self.por_bucket[sub][b], 0.0) for b in meta_b}
        if sum(deficit.values()) == 0:
            deficit = dict(meta_b)
        suma = sum(deficit.values())
        asign = {b: int(n * deficit[b] / suma) for b in deficit}
        resto = n - sum(asign.values())
        for b in sorted(deficit, key=lambda b: -(n * deficit[b] / suma - asign[b])):
            if resto <= 0:
                break
            asign[b] += 1
            resto -= 1
        salida = []
        for bucket, peso, metodo, kw in buckets:
            for _ in range(asign.get(bucket, 0)):
                intentos = 0
                c = None
                while c is None and intentos < 4:  # un fallo de generacion se cuenta y se reintenta
                    c = getattr(self.gen, metodo)(**kw)
                    intentos += 1
                if c is None:
                    continue
                c.meta["bucket"] = bucket
                m = controles.todos(self.datos, c, b3_con_cifras=resp_b.B3_CON_CIFRAS)
                if m:
                    self.descarta(sub, "control:" + m)
                    continue
                salida.append(c)
        return salida

    def medir(self, cands: list[Candidato]) -> list[Candidato]:
        buenos = []
        textos = []
        from .tokens import prompt_hasta_asistente
        for c in cands:
            textos.append(prompt_hasta_asistente(self.system, c.user))
            textos.append(c.respuesta + "<|eot_id|>")
        cuentas = self.tok.contar(textos)
        for i, c in enumerate(cands):
            total, resp = cuentas[2 * i], cuentas[2 * i + 1]
            c.meta["tokens_prompt"], c.meta["tokens_respuesta"] = total, resp
            if total > MAX_TOKENS_ENTRADA:
                self.descarta(c.subfamilia, "control:tokens_entrada>1648")
            elif resp > MAX_TOKENS_RESPUESTA:
                self.descarta(c.subfamilia, "control:tokens_respuesta>400")
            else:
                buenos.append(c)
        return buenos


def procesar_familia(motor: Motor, familia: str, base_hold: list[str], oversample: float = 1.6, rondas: int = 8) -> None:
    subs = [s for s in PLAN if PLAN[s][0] == familia and motor.cuotas.get(s, 0) > 0]
    if not subs:
        return
    # ronda 0: candidatos de todas las subfamilias -> IDF fijo de la familia
    lotes0 = {s: motor.medir(motor.lote(s, math.ceil(motor.cuotas[s] * oversample))) for s in subs}
    corpus_q = [c.entrada["pregunta"] for s in subs for c in lotes0[s]] + base_hold
    corpus_r = [c.respuesta for s in subs for c in lotes0[s]]
    tq, tr = similitud.Tfidf(corpus_q), similitud.Tfidf(corpus_r)
    hold = similitud.Conjunto()
    for q in base_hold:
        hold.agregar(tq.vec(q))
    aq, ar = similitud.Conjunto(), similitud.Conjunto()
    formulaciones: dict[str, collections.Counter] = {s: collections.Counter() for s in subs}

    def intentar(sub: str, c: Candidato, estricto: bool = True) -> bool:
        if len(motor.aceptados[sub]) >= motor.cuotas[sub]:
            return False
        marco = re.sub(r"\.w\d+$", "", c.plantilla_p)
        minimo_f = min(40 if sub in GRANDES else 25, motor.cuotas[sub])
        if estricto and sub not in ("C1", "C2", "C3") and marco in formulaciones[sub] and len(formulaciones[sub]) < minimo_f:
            motor.descarta(sub, "formulacion_repetida (se busca diversidad)")
            return False
        bucket = c.meta["bucket"]
        _, _, _, buckets = PLAN[sub]
        meta_b = motor.cuotas[sub] * dict((b, p) for b, p, _, _ in buckets)[bucket] / sum(p for _, p, _, _ in buckets)
        if motor.por_bucket[sub][bucket] >= math.ceil(meta_b):
            motor.descarta(sub, "sobrante_de_cupo")
            return False
        claves = [(m, v, a) for (v, m, a, _) in c.meta["datos"]]
        if any(motor.ternas[k] >= MAX_PARES_POR_TERNA for k in claves):
            motor.descarta(sub, "control:terna_repetida>3")
            return False
        vq, vr = tq.vec(c.entrada["pregunta"]), tr.vec(c.respuesta)
        sq, _ = hold.maximo(vq)
        if sq >= similitud.UMBRAL:
            motor.descarta(sub, "control:pregunta_similar_a_holdout")
            return False
        sq, _ = aq.maximo(vq)
        if sq >= similitud.UMBRAL:
            motor.descarta(sub, "control:pregunta_duplicada")
            return False
        sr, _ = ar.maximo(vr)
        if sr >= similitud.UMBRAL:
            motor.descarta(sub, "control:respuesta_duplicada")
            return False
        aq.agregar(vq)
        ar.agregar(vr)
        for k in claves:
            motor.ternas[k] += 1
        motor.aceptados[sub].append(c)
        formulaciones[sub][marco] += 1
        motor.por_bucket[sub][bucket] += 1
        motor.sim_max_q[sub].append(sq)
        return True

    for s in subs:
        for c in lotes0[s]:
            intentar(s, c)
    for ronda in range(rondas):
        pendientes = [s for s in subs if len(motor.aceptados[s]) < motor.cuotas[s]]
        if not pendientes:
            break
        for s in pendientes:
            falta = motor.cuotas[s] - len(motor.aceptados[s])
            for c in motor.medir(motor.lote(s, math.ceil(falta * 2.5) + 8)):
                intentar(s, c, estricto=ronda < 5)


# --------------------------------------------------------------------------- escritura
def fila(c: Candidato, idx: int, semilla: int, sha_system: str) -> dict:
    return {
        "id": f"n52-{c.subfamilia}-{idx:04d}",
        "familia": c.familia,
        "subfamilia": c.subfamilia,
        "instruction": c.user,
        "response": c.respuesta,
        "source": f"techo360-generador-0.4.0/{c.subfamilia}",
        "version_prompt": VERSION_PROMPT_ACTUAL,
        "system_sha256": sha_system,
        "semilla": semilla,
        "entrada": c.entrada,
        "plantilla_pregunta": c.plantilla_p,
        "plantilla_respuesta": c.plantilla_r,
    }


def escribir_jsonl(ruta: Path, filas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8", newline="\n") as f:
        for r in filas:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")


def verificar_dorada_archivos(datos: Datos, rutas: list[Path]) -> tuple[int, list[str]]:
    """Prueba dorada sobre los ARCHIVOS ya escritos: cada `entrada` se re-renderiza con el ensamblador real y debe dar el `instruction`."""
    n, fallos = 0, []
    for ruta in rutas:
        with ruta.open(encoding="utf-8") as f:
            for linea in f:
                r = json.loads(linea)
                n += 1
                if datos.renderizar(r["entrada"])["user"] != r["instruction"]:
                    fallos.append(r["id"])
    return n, fallos


def construir_c0(datos: Datos, tok: Tokenizador, system: str, sha_system: str, base: dict, semilla: int, hold_vec_fn, descartes: collections.Counter,
                 retirar_colombia: bool = False):
    from .tokens import prompt_hasta_asistente
    from services.orquestador.resolutor import normalizar, resolver
    filas, informativos = [], collections.Counter()
    informativos["retirados"] = []
    pendientes = []
    for i, p in enumerate(base["train"], start=1):
        entrada = {"pregunta": p["instruction"], "municipios": [], "variables": [], "anios": [], "anio_implicito": True}
        render = datos.renderizar(entrada)
        pendientes.append((i, p, entrada, render["user"]))
    cuentas = tok.contar([prompt_hasta_asistente(system, u) for _, _, _, u in pendientes] +
                         [p["response"] + "<|eot_id|>" for _, p, _, _ in pendientes])
    n = len(pendientes)
    for k, (i, p, entrada, user) in enumerate(pendientes):
        total, resp = cuentas[k], cuentas[n + k]
        if total > MAX_TOKENS_ENTRADA:
            descartes[("C0", "control:tokens_entrada>1648")] += 1
            continue
        if resp > MAX_TOKENS_RESPUESTA:
            descartes[("C0", "control:tokens_respuesta>400")] += 1
            continue
        # D8: se retiran los pares cuya pregunta resuelve un municipio en produccion (llevaria lineas de contexto, no bloque vacio)
        r = resolver(p["instruction"], datos.catalogo, HOY)
        motivo = None
        if r.municipios:
            motivo = "la pregunta resuelve un municipio: " + ", ".join(m["nombre"] for m in r.municipios)
        elif r.aclaracion:
            motivo = "la pregunta activa una aclaracion: " + r.aclaracion["tipo"]
        elif retirar_colombia and "colombia" in normalizar(p["instruction"]).split():
            motivo = "contaba como municipio antes de H1 (Colombia, Huila); se retira por la decision del 17/10 (C0 = 727)"
        if motivo:
            informativos["retirados"].append({"id": f"n52-C0-{i:04d}", "id_origen": p.get("id"), "pregunta": p["instruction"], "motivo": motivo})
            continue
        c = Candidato("C", "C0", entrada, user, p["response"], "base", "base", {"tokens_prompt": total, "tokens_respuesta": resp})
        r_ = fila(c, i, semilla, sha_system)
        r_["id"] = f"n52-C0-{i:04d}"
        r_["source"] = p.get("source") or "techo360-dataset-0.4.0-base"
        if "id" in p:
            r_["id_origen"] = p["id"]
        filas.append((r_, p))
    return filas, informativos


# --------------------------------------------------------------------------- informe
def apertura(texto: str, n: int = 2) -> str:
    return " ".join(sin_tildes(texto.lower()).replace("¿", "").replace("«", "").split()[:n])


def distribucion(valores: list[float]) -> str:
    if not valores:
        return "sin datos"
    v = sorted(valores)
    return f"min {v[0]:.0f} · mediana {statistics.median(v):.0f} · p95 {v[int(0.95 * (len(v) - 1))]:.0f} · max {v[-1]:.0f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    ap.add_argument("--proyeccion-dir", type=Path, default=Path(__file__).resolve().parents[3] / "techo360-proyeccion")
    ap.add_argument("--chatbot-dir", type=Path, default=Path(__file__).resolve().parents[3] / "techo360-chatbot")
    ap.add_argument("--base-ref", default="origin/feat/dataset-0.4.0-base")
    ap.add_argument("--escala", type=float, default=1.0, help="multiplica las cuotas (pruebas rapidas)")
    ap.add_argument("--hilos", type=int, default=8)
    ap.add_argument("--b3-sin-cifras", action="store_true", help="B3 no cita el dato disponible (literal de fase5-1 §6.2; la decision del 17/10 es citarlo)")
    ap.add_argument("--sin-informe", action="store_true")
    ap.add_argument("--c0-retirar-colombia", action="store_true", help="retira tambien los 69 pares de C0 que solo contaban como municipio por «Colombia» (C0 = 727)")
    ap.add_argument("--sin-c0", action="store_true", help="no reenvuelve el train base (pruebas rapidas)")
    ap.add_argument("--cache-tokens", type=Path, default=None, help="archivo de cache de conteos de tokens (por defecto, dentro de --salida)")
    args = ap.parse_args(argv)

    salida: Path = args.salida
    salida.mkdir(parents=True, exist_ok=True)
    system = cargar_system()
    sha_system = sha256_de(VERSION_PROMPT_ACTUAL)
    datos = Datos(args.proyeccion_dir.resolve())
    tok = Tokenizador(cache=args.cache_tokens or salida / ".cache_tokens.json", hilos=args.hilos)
    base = leer_base(args.chatbot_dir, args.base_ref)
    reservados = elegir_reservados(datos, args.semilla)
    sha_reservados = hashlib.sha256(json.dumps(reservados).encode("utf-8")).hexdigest()
    (salida / "reservados.json").write_text(json.dumps(
        {"semilla": args.semilla, "n": len(reservados), "sha256_lista": sha_reservados, "codigos": reservados,
         "municipios": [datos.municipio(c) for c in reservados]}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    escala = lambda n: max(1, round(n * args.escala)) if n else 0
    hold_prev = [p["instruction"] for p in base["holdout_v3_3"]] + [p["instruction"] for p in base["holdout_v0_4"]]

    # --- holdout contextual (municipios reservados)
    gen_h = Generador(datos, args.semilla, set(reservados), "holdout", not args.b3_sin_cifras)
    cuotas_h = {s: escala(v[2]) for s, v in PLAN.items()}
    motor_h = Motor(datos, gen_h, tok, system, hold_prev, "holdout", cuotas_h)
    for fam in FAMILIAS:
        procesar_familia(motor_h, fam, hold_prev)
    hold_ctx = [c for s in PLAN for c in motor_h.aceptados.get(s, [])]

    # --- train nuevo
    gen_t = Generador(datos, args.semilla, set(reservados), "train", not args.b3_sin_cifras)
    cuotas_t = {s: escala(v[1]) for s, v in PLAN.items()}
    todas_hold = hold_prev + [c.entrada["pregunta"] for c in hold_ctx]
    motor_t = Motor(datos, gen_t, tok, system, todas_hold, "train", cuotas_t)
    for fam in FAMILIAS:
        procesar_familia(motor_t, fam, todas_hold)
    nuevos = [c for s in PLAN for c in motor_t.aceptados[s]]

    # --- C0
    descartes_c0: collections.Counter = collections.Counter()
    if args.sin_c0:
        base = {**base, "train": []}
    c0_filas, c0_info = construir_c0(datos, tok, system, sha_system, base, args.semilla, None, descartes_c0, args.c0_retirar_colombia)
    (salida / "c0_retirados.json").write_text(json.dumps({"n": len(c0_info["retirados"]), "pares": c0_info["retirados"]}, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8", newline="\n")

    # --- archivos
    filas_nuevos = [fila(c, i, args.semilla, sha_system) for s in PLAN for i, c in enumerate(motor_t.aceptados[s], start=1)]
    filas_hold = [fila(c, i, args.semilla, sha_system) for i, c in enumerate(hold_ctx, start=1)]
    for i, r in enumerate(filas_hold, start=1):
        r["id"] = f"hctx-{r['subfamilia']}-{i:03d}"
    escribir_jsonl(salida / "train_nuevos.jsonl", filas_nuevos)
    escribir_jsonl(salida / "train_c0.jsonl", [r for r, _ in c0_filas])
    escribir_jsonl(salida / "holdout_ctx_v0_4.jsonl", filas_hold)
    tok.guardar()

    # --- verificaciones finales
    n_dorada, fallos_dorada = verificar_dorada_archivos(datos, [salida / "train_nuevos.jsonl", salida / "train_c0.jsonl", salida / "holdout_ctx_v0_4.jsonl"])
    if fallos_dorada:
        print("PRUEBA DORADA FALLO EN:", fallos_dorada[:10], file=sys.stderr)
        sys.exit(2)
    prob = []
    reservados_set = set(reservados)
    for r in filas_nuevos:
        if reservados_set & set(r["entrada"]["municipios"]):
            prob.append(("municipio_reservado_en_train", r["id"]))
    for r in filas_hold:
        if r["entrada"]["municipios"] and not set(r["entrada"]["municipios"]) <= reservados_set:
            prob.append(("holdout_con_municipio_no_reservado", r["id"]))
    ternas = collections.Counter((m, v, a) for c in nuevos for (v, m, a, _) in c.meta["datos"])
    if any(n > MAX_PARES_POR_TERNA for n in ternas.values()):
        prob.append(("terna_repetida", ""))
    if prob:
        print("PROBLEMAS EN LA VERIFICACION FINAL:", prob[:10], file=sys.stderr)
        sys.exit(2)

    if not args.sin_informe:
        from .informe import escribir_informe
        escribir_informe(salida, args, datos, system, sha_system, base, nuevos, hold_ctx, c0_filas, c0_info, motor_t, motor_h,
                         descartes_c0, reservados, sha_reservados, tok, n_dorada)
    print(f"nuevos={len(filas_nuevos)} c0={len(c0_filas)} holdout_ctx={len(filas_hold)} -> {salida}")


if __name__ == "__main__":
    main()
