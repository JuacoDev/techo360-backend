"""Informe de la corrida (informe.md): conteos contra cuota, descartes, diversidad, distribuciones, sha256, ejemplos y hallazgos."""
import collections
import hashlib
import json
import re
import statistics
import subprocess
from pathlib import Path

from services.orquestador import alias as al
from services.orquestador.fuentes import fuente_de_origen
from services.origen import es_proyeccion

from .generar import FAMILIAS, GRANDES, NOMBRE_FAMILIA, PLAN, apertura, distribucion, sha256_archivo
from .lengua import sin_tildes
from .similitud import UMBRAL, Conjunto, Tfidf

VARS = list(al.NOMBRE_VARIABLE)


def _pct(n, d):
    return f"{100 * n / d:.1f} %" if d else "n/a"


def _git(repo: Path, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    return r.stdout.strip()


def _base_id(rid: str) -> str:
    """Plantilla de respuesta sin las partes de composicion variable (aviso/cola)."""
    return re.split(r"[+]", rid)[0]


def escribir_informe(salida: Path, args, datos, system, sha_system, base, nuevos, hold_ctx, c0_filas, c0_info, motor_t, motor_h,
                     descartes_c0, reservados, sha_reservados, tok, n_dorada):
    L: list[str] = []
    A = L.append
    raiz_backend = Path(__file__).resolve().parents[2]
    sha_backend = _git(raiz_backend, "rev-parse", "HEAD")
    rama = _git(raiz_backend, "rev-parse", "--abbrev-ref", "HEAD")
    sucio = bool(_git(raiz_backend, "status", "--porcelain", "--", "scripts/dataset_0_4"))
    A("# Informe 5.2: generador de pares 0.4.0\n")
    A(f"Semilla `{args.semilla}` · system `sp-0.4.0-1` (sha256 `{sha_system}`) · backend `{rama}` @ `{sha_backend[:10]}`"
      f"{' (con cambios sin commitear en el generador)' if sucio else ''} · sesión de BD `default_transaction_read_only = {datos.solo_lectura}` (SHOW, COMPROBADO).\n")
    A("Marcas: COMPROBADO = medido por esta corrida; REPORTADO = lo dice un documento; HIPÓTESIS = sin medir.\n")

    # ---------------------------------------------------------------- conteos
    A("## 1. Conteos contra cuota (train nuevo)\n")
    A("| Subfamilia | Cuota | Logrado | Diferencia |\n|---|---:|---:|---:|")
    tot_c = tot_l = 0
    for fam in FAMILIAS:
        sc = sl = 0
        for s, (f, cuota, _, _) in PLAN.items():
            if f != fam:
                continue
            got = len(motor_t.aceptados[s])
            sc, sl = sc + cuota, sl + got
            A(f"| {s} | {cuota} | {got} | {got - cuota:+d} |")
        A(f"| **{fam} · {NOMBRE_FAMILIA[fam]}** | **{sc}** | **{sl}** | **{sl - sc:+d}** |")
        tot_c, tot_l = tot_c + sc, tot_l + sl
    A(f"| **Total nuevos** | **{tot_c}** | **{tot_l}** | **{tot_l - tot_c:+d}** |")
    A(f"\nC0 (train base reenvuelto con el bloque vacío): **{len(c0_filas)}** de {len(base['train'])}; retirados: {len(c0_info['retirados'])} (lista y preguntas en `c0_retirados.json`).\n")
    _diff_primera_corrida(A, salida, nuevos, hold_ctx, c0_filas)
    A("### Holdout contextual `holdout_ctx_v0_4`\n")
    A("| Familia | Cuota | Logrado |\n|---|---:|---:|")
    cuotas_h = {"A": 30, "B": 12, "C": 6, "D": 6, "E": 6}
    for fam in "ABCDE":
        got = sum(1 for c in hold_ctx if c.familia == fam)
        A(f"| {fam} | {cuotas_h[fam]} | {got} |")
    A(f"| **Total** | **60** | **{len(hold_ctx)}** |\n")

    # ---------------------------------------------------------------- descartes
    A("## 2. Descartes por motivo\n")
    A("Cada candidato que no llega al archivo se cuenta aquí. `gen:` = el escenario no pudo armarse (o el resolutor real no habría producido ese contexto); "
      "`control:` = falló un control automático; `sobrante_de_cupo` = el candidato era válido pero su cupo (subfamilia o tamaño) ya estaba lleno.\n")
    for nombre, motor in (("Train nuevo", motor_t), ("Holdout contextual", motor_h)):
        agg = collections.Counter()
        for (sub, motivo), n in list(motor.descartes.items()) + list(motor.gen.descartes.items()):
            agg[motivo] += n
        A(f"### {nombre}: {sum(agg.values())} candidatos descartados\n")
        A("| Motivo | N |\n|---|---:|")
        for m, n in agg.most_common():
            A(f"| {m} | {n} |")
        A("")
    if descartes_c0:
        A(f"### C0: {sum(descartes_c0.values())} descartados\n")
        for (s, m), n in descartes_c0.items():
            A(f"- {m}: {n}")
        A("")
    A("Detalle por subfamilia (train):\n")
    A("| Subfamilia | Motivo | N |\n|---|---|---:|")
    detalle = collections.Counter()
    for (sub, motivo), n in list(motor_t.descartes.items()) + list(motor_t.gen.descartes.items()):
        detalle[(sub, motivo)] += n
    for (sub, motivo), n in sorted(detalle.items()):
        A(f"| {sub} | {motivo} | {n} |")
    A("")

    # ---------------------------------------------------------------- aperturas
    A("## 3. Aperturas de la respuesta (dos primeras palabras)\n")
    ap = collections.Counter(apertura(c.respuesta) for c in nuevos)
    total = len(nuevos)
    A(f"Sobre {total} pares nuevos. Apertura más frecuente: **«{ap.most_common(1)[0][0]}» = {_pct(ap.most_common(1)[0][1], total)}** (tope del plan: 8 %).\n")
    A("| # | Apertura | Pares | % |\n|---:|---|---:|---:|")
    for i, (a, n) in enumerate(ap.most_common(10), start=1):
        A(f"| {i} | {a} | {n} | {_pct(n, total)} |")
    primera = collections.Counter(apertura(c.respuesta, 1) for c in nuevos)
    A(f"\nPor primera palabra: «{primera.most_common(1)[0][0]}» = {_pct(primera.most_common(1)[0][1], total)}. "
      f"«Según…» como apertura: {_pct(sum(1 for c in nuevos if apertura(c.respuesta, 1) == 'segun'), total)} (tope 3 %). "
      f"Aperturas distintas: {len(ap)}.\n")
    for fam in FAMILIAS:
        cs = [c for c in nuevos if c.familia == fam]
        aps = collections.Counter(apertura(c.respuesta) for c in cs)
        if cs:
            A(f"- Familia {fam}: {len(aps)} aperturas distintas en {len(cs)} pares; la más frecuente, «{aps.most_common(1)[0][0]}» ({_pct(aps.most_common(1)[0][1], len(cs))} de la familia).")
    A("")

    # ---------------------------------------------------------------- diversidad
    A("## 4. Diversidad\n")
    A("Formulaciones = plantillas de pregunta distintas (sin contar las envolturas «Hola, …»); plantillas de respuesta = identificadores base distintos.\n")
    A("| Subfamilia | Pares | Formulaciones de pregunta | Mínimo | Plantillas de respuesta | Cumple |\n|---|---:|---:|---:|---:|---|")
    for s, (f, cuota, _, _) in PLAN.items():
        cs = motor_t.aceptados[s]
        marcos = {re.sub(r"\.w\d+$", "", c.plantilla_p) for c in cs}
        # C3 y C2: la plantilla de pregunta es el tema/palabra; cuentan las preguntas distintas normalizadas
        if s in ("C1", "C2", "C3"):
            marcos = {sin_tildes(c.entrada["pregunta"].lower()) for c in cs}
        minimo = min(40 if s in GRANDES else 25, cuota)  # no se exigen mas formulaciones que pares de cuota
        rs = {_base_id(c.plantilla_r) for c in cs}
        A(f"| {s} | {len(cs)} | {len(marcos)} | {minimo} | {len(rs)} | {'sí' if len(marcos) >= min(minimo, len(cs)) and len(cs) >= cuota else 'NO'} |")
    A("")
    A("| Familia | Plantillas de respuesta distintas | Mínimo 15 |\n|---|---:|---|")
    for fam in FAMILIAS:
        rs = {_base_id(c.plantilla_r) for c in nuevos if c.familia == fam}
        A(f"| {fam} | {len(rs)} | {'sí' if len(rs) >= 15 else 'NO'} |")
    A("")

    # ---------------------------------------------------------------- distribuciones
    A("## 5. Distribuciones\n")
    nA = [c for c in nuevos if c.familia == "A"]
    A("### Variables en A\n")
    por_par = collections.Counter()
    por_linea = collections.Counter()
    for c in nA:
        vs = {v for (v, _, _, _) in c.meta["datos"]}
        por_par.update(vs)
        por_linea.update(v for (v, _, _, _) in c.meta["datos"])
    A("| Variable | Pares de A que la contienen | % de pares | Líneas de dato | % de líneas |\n|---|---:|---:|---:|---:|")
    tl = sum(por_linea.values())
    for v in VARS:
        A(f"| {al.NOMBRE_VARIABLE[v]} | {por_par[v]} | {_pct(por_par[v], len(nA))} | {por_linea[v]} | {_pct(por_linea[v], tl)} |")
    A("\nNota: un par con varias variables cuenta en cada una; el tope del plan (25 %) se evalúa sobre las líneas de dato y sobre las variables «principales» de los pares de una variable:\n")
    una = collections.Counter(c.meta["datos"][0][0] for c in nA if len({v for (v, _, _, _) in c.meta["datos"]}) == 1)
    A("| Variable | Pares de A de una sola variable | % |\n|---|---:|---:|")
    tu = sum(una.values())
    for v in VARS:
        A(f"| {al.NOMBRE_VARIABLE[v]} | {una[v]} | {_pct(una[v], tu)} |")
    A("\n### Real / proyección\n")
    rp_a = collections.Counter(("real" if r else "proyeccion") for c in nA for (_, _, _, r) in c.meta["datos"])
    rp_all = collections.Counter(("real" if r else "proyeccion") for c in nuevos for (_, _, _, r) in c.meta["datos"])
    pares_a = collections.Counter(("solo real" if all(r for (_, _, _, r) in c.meta["datos"]) else "solo proyección" if not any(r for (_, _, _, r) in c.meta["datos"]) else "mixto") for c in nA)
    A(f"- Líneas de dato en A: real {rp_a['real']} ({_pct(rp_a['real'], sum(rp_a.values()))}) / proyección {rp_a['proyeccion']} ({_pct(rp_a['proyeccion'], sum(rp_a.values()))}). Objetivo del plan: ≈ 55/45.")
    A(f"- Pares de A: {dict(pares_a)}.")
    A(f"- Líneas de dato en todos los nuevos: {dict(rp_all)}.\n")
    A("### Tamaño del contexto (líneas del bloque, incluidas «Sin dato», «Ubicación» y «Se omitieron…»)\n")

    def cubeta(n):
        return "0 (bloque vacío)" if n == 0 else "1–3" if n <= 3 else "4–8" if n <= 8 else "9–20"

    def lineas_c(c):
        return 0 if c.meta["lineas"] == 0 or c.user.split("\n")[1].startswith("Sin datos disponibles") else c.meta["lineas"]
    dA = collections.Counter(cubeta(lineas_c(c)) for c in nA)
    dT = collections.Counter(cubeta(lineas_c(c)) for c in nuevos)
    A("| Tamaño | Pares de A | % de A | Todos los nuevos | % |\n|---|---:|---:|---:|---:|")
    for k in ("0 (bloque vacío)", "1–3", "4–8", "9–20"):
        A(f"| {k} | {dA[k]} | {_pct(dA[k], len(nA))} | {dT[k]} | {_pct(dT[k], len(nuevos))} |")
    A("\nObjetivo del plan para A: 55 / 30 / 15 % (1–3, 4–8, 9–20).\n")
    a7 = [c for c in nuevos if c.subfamilia == "A7"]
    cerca = [c for c in a7 if c.meta["tokens_prompt"] >= 241 + 35 + 750]
    A(f"A7 (omisión): {len(a7)} pares; {len(cerca)} con prompt completo ≥ 1.026 tokens (contexto cerca del tope de ~900). "
      f"Pares con bloque vacío: {sum(1 for c in nuevos if lineas_c(c) == 0)} de {len(nuevos)} ({_pct(sum(1 for c in nuevos if lineas_c(c) == 0), len(nuevos))}); plan: 280 de 1.200 (23 %).\n")
    A("### Tokens (tokenizer de Llama 3.2, vocabulario Llama 3 BPE; COMPROBADO: el system da 241)\n")
    A(f"- Prompt completo (plantilla + system + user, hasta la cabecera del asistente; tope 1.648): {distribucion([c.meta['tokens_prompt'] for c in nuevos])}.")
    A(f"- Respuesta + `<|eot_id|>` (tope 400): {distribucion([c.meta['tokens_respuesta'] for c in nuevos])}.")
    A(f"- Respuesta en palabras: {distribucion([len(c.respuesta.split()) for c in nuevos])}.")
    c0_resp = [r["tokens_respuesta"] if "tokens_respuesta" in r else None for r, _ in c0_filas]
    A("")
    A("## 6. Verificaciones finales\n")
    ternas = collections.Counter((m, v, a) for c in nuevos for (v, m, a, _) in c.meta["datos"])
    A(f"- Prueba dorada (re-renderizar `entrada` con el ensamblador real da el mismo `user`): se ejecutó sobre cada candidato antes de aceptarlo y se repitió leyendo los archivos ya escritos (`train_nuevos`, `train_c0`, `holdout_ctx_v0_4`): {n_dorada} pares, 0 fallos.")
    A(f"- Máximo de pares por terna municipio-variable-año: {max(ternas.values()) if ternas else 0} (tope 3).")
    A(f"- Municipios reservados: {len(reservados)} (sha256 de la lista `{sha_reservados}`); ninguno aparece en un contexto de train; el holdout solo usa municipios reservados (comprobado antes de escribir).")
    hold_q = [p["instruction"] for p in base["holdout_v3_3"]] + [p["instruction"] for p in base["holdout_v0_4"]] + [c.entrada["pregunta"] for c in hold_ctx]
    todos_q = [c.entrada["pregunta"] for c in nuevos] + [r["instruction"] for r, _ in c0_filas] + hold_q
    t = Tfidf(todos_q)
    hv = Conjunto()
    for q in hold_q:
        hv.agregar(t.vec(q))
    mx = max((hv.maximo(t.vec(c.entrada["pregunta"]))[0] for c in nuevos), default=0)
    mx0 = max((hv.maximo(t.vec(p["instruction"]))[0] for _, p in c0_filas), default=0)
    A(f"- Similitud máxima pregunta de train nuevo vs los tres holdouts (TF-IDF, umbral {UMBRAL}): {mx:.3f}. C0 vs holdouts: {mx0:.3f} (el train base ya se validó contra holdout_v3_3 en su día; REPORTADO).")
    A(f"- Consultas a la base: solo SELECT con parámetros; sesión en solo lectura; 0 escrituras.\n")

    # ---------------------------------------------------------------- sha256 y comando
    A("## 7. Archivos y reproducción\n")
    A("| Archivo | Filas | sha256 |\n|---|---:|---|")
    for nombre in ("train_nuevos.jsonl", "train_c0.jsonl", "holdout_ctx_v0_4.jsonl", "reservados.json"):
        ruta = salida / nombre
        n = sum(1 for _ in ruta.open(encoding="utf-8")) if nombre.endswith(".jsonl") else "-"
        A(f"| {nombre} | {n} | `{sha256_archivo(ruta)}` |")
    A("\nComando exacto (desde `techo360-backend/`, con `techo360-chatbot` en `origin/feat/dataset-0.4.0-base` y `techo360-proyeccion` junto al backend):\n")
    A("```\npython -m scripts.dataset_0_4.generar --salida ../trabajo-dataset-0.4.0 --semilla %d\n```\n" % args.semilla)
    A("Salida en UTF-8 con saltos de línea `\\n`. Misma semilla, mismos archivos (prueba de determinismo en `tests_generador.py`).\n")

    # ---------------------------------------------------------------- ejemplos
    A("## 8. Ejemplos completos (system omitido), tres por familia\n")
    A("Cada ejemplo muestra el `user` tal como lo ensambla el backend y la respuesta.\n")
    for fam in FAMILIAS:
        cs = [c for c in nuevos if c.familia == fam]
        if not cs:
            continue
        idx = sorted({0, len(cs) // 2, len(cs) - 1})
        if len(idx) < 3 and len(cs) >= 3:
            idx = [0, 1, 2]
        A(f"### Familia {fam} · {NOMBRE_FAMILIA[fam]}\n")
        for i in idx[:3]:
            c = cs[i]
            A(f"**{c.subfamilia}** (`{c.plantilla_p}` / `{c.plantilla_r}`)\n")
            A("```\n" + c.user + "\n```")
            A("Respuesta: " + c.respuesta + "\n")
    # ---------------------------------------------------------------- puntos ⚠
    A("## 9. Puntos ⚠ de fase5-1 §1 resueltos con datos reales\n")
    A("**Nombre y departamento de las ciudades con alias (catálogo `zonas_municipio`, COMPROBADO):**\n")
    A("| Alias | `nombre` en el contexto | `departamento` en el contexto |\n|---|---|---|")
    for cod, alias in (("11001", "Bogotá"), ("76001", "Cali"), ("13001", "Cartagena"), ("54001", "Cúcuta")):
        m = datos.municipio(cod)
        A(f"| {alias} | {m['nombre']} | {m['departamento']} |")
    A("\n**Fuente real por variable y año** (COMPROBADO sobre `mapcalordb`; la fuente es la que `fuentes.py` escribe en la línea):\n")
    A("| Variable | Origen en la base | Fuente en la línea | ¿Real? | Años | Filas |\n|---|---|---|---|---|---:|")
    agr: dict = collections.defaultdict(lambda: [10**9, 0, 0])
    for (cod, var), serie in datos.series.items():
        if cod not in datos.universo:
            continue
        for f in serie:
            if f["valor"] is None:
                continue
            k = (var, f["origen"])
            agr[k][0] = min(agr[k][0], f["anio"])
            agr[k][1] = max(agr[k][1], f["anio"])
            agr[k][2] += 1
    for (var, origen), (a0, a1, n) in sorted(agr.items(), key=lambda kv: (VARS.index(kv[0][0]) if kv[0][0] in VARS else 99, kv[0][1])):
        A(f"| {al.NOMBRE_VARIABLE.get(var, var)} | `{origen}` | {fuente_de_origen(origen)} | {'no' if es_proyeccion(origen) else 'sí'} | {a0}–{a1} | {n} |")
    A("\nScore de privación: no es una fila de la base; sale de `calcular_score_punto`/`score_con_filas` del backend: «Techo 360 - Score de privación», dato real solo si sus tres componentes son reales (en la práctica 2018) y proyección «Proyección Techo 360» en el resto, con la fiabilidad mínima de sus componentes.\n")
    A("**Huecos naturales usados** (años del rango de la variable sin fila, evaluados con el núcleo de `techo360-proyeccion`, COMPROBADO):\n")
    huecos = datos.huecos_naturales()
    cnt = collections.Counter()
    for (var, cod), lista in huecos.items():
        for a, r in lista:
            cnt[(var, r)] += 1
    A("| Variable | Resultado del núcleo | Municipio-año |\n|---|---|---:|")
    for (var, r), n in sorted(cnt.items()):
        A(f"| {al.NOMBRE_VARIABLE[var]} | {r} | {n} |")
    A("")
    # ---------------------------------------------------------------- hallazgos
    A("## 10. Hallazgos que contradicen fase5-1 o el código, y decisiones que necesito\n")
    hall = _hallazgos(datos, nuevos, c0_filas, c0_info, motor_t, reservados, base)
    for h in hall:
        A(h)
    (salida / "informe.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")


def _diff_primera_corrida(A, salida: Path, nuevos, hold_ctx, c0_filas) -> None:
    previa = salida.parent / "trabajo-dataset-0.4.0"
    ruta = previa / "train_nuevos.jsonl"
    if not ruta.exists():
        return
    antes = collections.Counter(json.loads(l)["subfamilia"] for l in ruta.open(encoding="utf-8"))
    ahora = collections.Counter(c.subfamilia for c in nuevos)
    ap_antes = collections.Counter()
    for l in ruta.open(encoding="utf-8"):
        ap_antes[apertura(json.loads(l)["response"])] += 1
    ap_ahora = collections.Counter(apertura(c.respuesta) for c in nuevos)
    c0_antes = sum(1 for _ in (previa / "train_c0.jsonl").open(encoding="utf-8")) if (previa / "train_c0.jsonl").exists() else 0
    A("### Diff de conteos contra la primera corrida (`trabajo-dataset-0.4.0/`, no modificada)\n")
    A("| Subfamilia | Primera corrida | Esta corrida | Diferencia |\n|---|---:|---:|---:|")
    for s_ in PLAN:
        A(f"| {s_} | {antes[s_]} | {ahora[s_]} | {ahora[s_] - antes[s_]:+d} |")
    A(f"| C0 | {c0_antes} | {len(c0_filas)} | {len(c0_filas) - c0_antes:+d} |")
    A(f"| Total nuevos | {sum(antes.values())} | {sum(ahora.values())} | {sum(ahora.values()) - sum(antes.values()):+d} |")
    A(f"\nApertura más frecuente: primera corrida «{ap_antes.most_common(1)[0][0]}» ({ap_antes.most_common(1)[0][1]}), esta «{ap_ahora.most_common(1)[0][0]}» ({ap_ahora.most_common(1)[0][1]}).\n")


def _hallazgos(datos, nuevos, c0_filas, c0_info, motor_t, reservados, base):
    h = []
    n = 1

    def add(titulo, cuerpo):
        nonlocal n
        h.append(f"**N{n}. {titulo}** {cuerpo}\n")
        n += 1
    ret = c0_info["retirados"]
    por_municipio = sum(1 for r in ret if r["motivo"].startswith("la pregunta resuelve"))
    por_colombia = sum(1 for r in ret if r["motivo"].startswith("contaba como municipio"))
    add("D8 partía de un conteo anterior a H1.", f"Los 77 de H9 eran 8 preguntas que resuelven un municipio de verdad y 69 que solo lo resolvían porque «Colombia» contaba como el municipio del Huila (COMPROBADO: "
        f"con el resolutor corregido, solo {por_municipio} resuelven municipio). Los 69 son hoy bloque vacío correcto en producción. "
        f"Esta corrida retira {len(ret)} pares y deja C0 en {len(c0_filas)}. Con `--c0-retirar-colombia` se retiran los 77 y C0 queda en 727, como pedía el prompt. Decisión: ¿cuál de los dos?")
    add("«¿Dónde queda X?» choca con la intención geográfica de la parte A.", "Para Granada y Venecia (nombres de varios municipios) esa pregunta ahora pide aclaración de homónimo y nunca llega al modelo (COMPROBADO con `resolver`). "
        "Por eso C2 no incluye «¿Dónde queda Venecia/Granada?». Con ciudades fuera de Colombia que no son municipios sí funcionaría, pero no están en la lista de palabras.")
    add("«Providencia» siempre resuelve un municipio.", "Aun en minúscula y tras «una», «providencia» resuelve el municipio Providencia (COMPROBADO), así que no hay pregunta de definición con bloque vacío. Providencia queda fuera de C2. "
        "«Santa Rosa» solo entra como la santa («¿Quién fue Santa Rosa de Lima?»), que sí queda con bloque vacío.")
    add("Respaldo de los hechos de C2.", "No consulté fuentes en línea (sin red): cada sentido de C2 lleva en el código un comentario con su fuente de verdad, que es conocimiento general del DLE y de historia escolar, REPORTADO por quien lo redactó. Hay que revisarlo en el 100 %.")
    colombia_c1 = sum(1 for c in nuevos if c.subfamilia == "C1" and "Colombia" in c.entrada["pregunta"])
    n_c1 = sum(1 for c in nuevos if c.subfamilia == "C1")
    colombia_b2 = sum(1 for c in nuevos if c.subfamilia == "B2" and c.meta.get("tipo") == "agregado" and "Colombia" in c.entrada["pregunta"])
    n_b2 = sum(1 for c in nuevos if c.subfamilia == "B2" and c.meta.get("tipo") == "agregado")
    add("D6: preguntas nacionales con «en Colombia».", f"C1: {colombia_c1} de {n_c1} ({colombia_c1 / max(n_c1, 1) * 100:.0f} %); B2 agregado: {colombia_b2} de {n_b2} ({colombia_b2 / max(n_b2, 1) * 100:.0f} %). Meta: ≥ 40 %.")
    n3 = sum(1 for c in nuevos if len(__import__("re").split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚ¿¡«])", c.respuesta)) > 3)
    add("Respuestas con más de tres frases.", f"{n3} de {len(nuevos)} pares nuevos tienen más de 3 frases (A5, A7 y E1, que ahora cita todas las líneas); la cota dura es 140 palabras (220 en A7).")
    return h
