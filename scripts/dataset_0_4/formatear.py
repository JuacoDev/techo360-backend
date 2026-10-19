"""Paso 5.4: formateo del dataset 0.4.0 para entrenar (NO entrena, no usa GPU, base de datos ni red).

    python -m scripts.dataset_0_4.formatear --r2 ../trabajo-dataset-0.4.0-r2

Lee `train_nuevos.jsonl` + `train_c0.jsonl` de la segunda corrida y, por par, arma los mensajes
    system  = prompts/sp-0.4.0-1.txt (se aborta si su sha256 no es el esperado)
    user    = campo `instruction` tal cual, byte a byte
    assistant = campo `response`
los renderiza con `tokenizer.apply_chat_template` del tokenizer del adaptador v3.3 (la misma plantilla de produccion) con
`date_string` fijo «18 Sep 2026» (el de v3.3; cierra H-002 para el entrenamiento), los tokeniza sin tokens especiales
adicionales (la plantilla ya trae el BOS) y enmascara con labels = -100 todo hasta el encabezado del asistente
(inclusive); se aprende la respuesta y el `<|eot_id|>` final. Nada se trunca: los que superan max_length de v3.3 se listan.

Salidas en <r2>/formateado/: train_tokenizado.jsonl, informe_5_4.md, excede_max_length.json.
Necesita transformers + tokenizers + jinja2 (venv fuera del repo) y las dependencias del backend para comparar con `ensamblar`.
"""
import argparse
import hashlib
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ_BACKEND = Path(__file__).resolve().parents[2]
if str(RAIZ_BACKEND) not in sys.path:
    sys.path.insert(0, str(RAIZ_BACKEND))

SHA_SYSTEM = "b63287584676daa3ae514b90884fa8065eeb94f11881711f4517a8b02cfaa9ce"
FECHA = "18 Sep 2026"
SEMILLA = 42
MARCADOR_ASSISTANT = "<|start_header_id|>assistant<|end_header_id|>\n\n"  # igual que training/entrenar_lora.py
MAX_LENGTH_POR_DEFECTO = 512


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def leer_jsonl(ruta: Path) -> list[dict]:
    with ruta.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def leer_system() -> str:
    ruta = RAIZ_BACKEND / "prompts" / "sp-0.4.0-1.txt"
    crudo = ruta.read_bytes()
    if sha256_bytes(crudo) != SHA_SYSTEM:
        raise SystemExit(f"ABORTO: el sha256 de {ruta.name} ({sha256_bytes(crudo)}) no es el esperado ({SHA_SYSTEM})")
    return crudo.decode("utf-8")


def max_length_v33(chatbot: Path) -> tuple[int, str]:
    ruta = chatbot / "training" / "entrenar_lora.py"
    if ruta.exists():
        m = re.search(r"^MAX_LENGTH\s*=\s*(\d+)", ruta.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            return int(m.group(1)), "training/entrenar_lora.py (MAX_LENGTH)"
    return MAX_LENGTH_POR_DEFECTO, "valor por defecto (no se encontro entrenar_lora.py)"


def renderizar(tok, system: str, user: str, response: str) -> str:
    mensajes = [{"role": "system", "content": system}, {"role": "user", "content": user}, {"role": "assistant", "content": response}]
    return tok.apply_chat_template(mensajes, tokenize=False, add_generation_prompt=False, date_string=FECHA)


def tokenizar(tok, texto: str) -> tuple[list[int], list[int]]:
    ids = tok(texto, add_special_tokens=False)["input_ids"]  # sin truncar
    corte = texto.rindex(MARCADOR_ASSISTANT) + len(MARCADOR_ASSISTANT)
    prefijo = tok(texto[:corte], add_special_tokens=False)["input_ids"]
    if ids[: len(prefijo)] != prefijo:
        raise SystemExit("ABORTO: el prefijo tokenizado por separado no coincide con el inicio del texto completo")
    n = len(prefijo)
    return ids, [-100] * n + ids[n:]


# ---------------------------------------------------------------------------- comparacion con el orquestador real
def user_a_candidatos(instruction: str):
    """Reconstruye un ContextoConstruido con las lineas del bloque CONTEXTO tal como estan escritas, para pasarlo por `ensamblar`."""
    from services.orquestador.contexto import LINEA_SIN_CONTEXTO, Candidato, ContextoConstruido
    bloque, pregunta = instruction.split("\n\nPREGUNTA DEL USUARIO\n", 1)
    lineas = bloque.split("\n")[1:]
    ctx = ContextoConstruido()
    for k, l in enumerate(lineas, start=1):
        if l == LINEA_SIN_CONTEXTO:
            continue
        if l.startswith("Se omitieron"):
            return None, pregunta  # la nota de omision la genera `seleccionar`: ese par se compara aparte
        m = re.match(r"^\[c\d+\] (.*)$", l)
        if m:
            item = {"variable": "x", "codigodivipola": "0", "anio": 0, "valor": 0, "origen": "x", "fuente": "x"}
            ctx.candidatos.append(Candidato(0, k, item, m.group(1)))
        else:
            ctx.candidatos.append(Candidato(0, k, None, l, True))
    return ctx, pregunta


def comparar_con_orquestador(tok, system: str, filas: list[dict], textos: dict[str, str]) -> dict:
    """Para cada par: instruction -> ensamblar() real -> apply_chat_template == texto formateado. Devuelve el resumen."""
    from services.orquestador.ensamblador import ensamblar
    resumen = {"comparados": 0, "iguales": 0, "omitidos_por_nota_de_omision": 0, "difieren": [], "por_tipo": Counter()}
    for r in filas:
        ctx, pregunta = user_a_candidatos(r["instruction"])
        if ctx is None:
            resumen["omitidos_por_nota_de_omision"] += 1
            continue
        paquete = ensamblar(pregunta, ctx, {"usuario_id": 0, "consentimiento_entrenamiento": False}, system=system)
        msgs = paquete["peticion"]["mensajes"]
        texto = renderizar(tok, msgs[0]["contenido"], msgs[1]["contenido"], r["response"])
        resumen["comparados"] += 1
        if texto == textos[r["id"]]:
            resumen["iguales"] += 1
            resumen["por_tipo"][r["familia_salida"]] += 1
        else:
            resumen["difieren"].append(r["id"])
    return resumen


# ---------------------------------------------------------------------------- principal
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--r2", required=True, type=Path)
    ap.add_argument("--chatbot-dir", type=Path, default=RAIZ_BACKEND.parent / "techo360-chatbot")
    ap.add_argument("--tokenizer-dir", type=Path, default=None, help="por defecto, training/adapters/techo360-v3.3 del chatbot")
    args = ap.parse_args(argv)

    import transformers
    from transformers import AutoTokenizer

    r2, destino = args.r2, args.r2 / "formateado"
    destino.mkdir(parents=True, exist_ok=True)
    tok_dir = args.tokenizer_dir or args.chatbot_dir / "training" / "adapters" / "techo360-v3.3"
    system = leer_system()
    tok = AutoTokenizer.from_pretrained(str(tok_dir))
    max_len, origen_max = max_length_v33(args.chatbot_dir)

    nuevos, c0 = leer_jsonl(r2 / "train_nuevos.jsonl"), leer_jsonl(r2 / "train_c0.jsonl")
    filas = []
    for r in nuevos:
        filas.append({**r, "familia_salida": r["familia"]})
    for r in c0:
        filas.append({**r, "familia_salida": "C0"})

    # conteos contra el informe de r2
    informe_r2 = (r2 / "informe.md").read_text(encoding="utf-8")
    esperado = {}
    for m in re.finditer(r"^\| ([A-F][0-9]?) \| (\d+) \| (\d+) \| [+-]\d+ \|", informe_r2, re.MULTILINE):  # tabla "Conteos contra cuota"
        esperado.setdefault(m.group(1), int(m.group(3)))
    real = Counter(r["subfamilia"] for r in nuevos)
    discrepancias = [f"{k}: informe r2 {esperado[k]}, archivo {real.get(k, 0)}" for k in esperado if k != "C0" and esperado[k] != real.get(k, 0)]
    m_c0 = re.search(r"C0 \(.*?\): \*\*(\d+)\*\* de", informe_r2)
    if m_c0 and int(m_c0.group(1)) != len(c0):
        discrepancias.append(f"C0: informe r2 {m_c0.group(1)}, archivo {len(c0)}")

    textos, items = {}, []
    for r in filas:
        texto = renderizar(tok, system, r["instruction"], r["response"])
        ids, labels = tokenizar(tok, texto)
        textos[r["id"]] = texto
        items.append({"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labels, "id": r["id"], "familia": r["familia_salida"]})

    # orden barajado con semilla fija
    orden = list(range(len(items)))
    random.Random(SEMILLA).shuffle(orden)
    salida = destino / "train_tokenizado.jsonl"
    with salida.open("w", encoding="utf-8", newline="\n") as f:
        for i in orden:
            f.write(json.dumps(items[i], ensure_ascii=False, separators=(",", ":")) + "\n")

    # estadisticas
    largos = [len(x["input_ids"]) for x in items]
    excede = [{"id": x["id"], "familia": x["familia"], "tokens": len(x["input_ids"])} for x in items if len(x["input_ids"]) > max_len]
    (destino / "excede_max_length.json").write_text(json.dumps({"max_length_v3_3": max_len, "n": len(excede), "ejemplos": sorted(excede, key=lambda e: -e["tokens"])},
                                                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    por_fam = defaultdict(lambda: [0, 0, 0, []])
    for x in items:
        tot = len(x["input_ids"])
        ent = sum(1 for l in x["labels"] if l != -100)
        p = por_fam[x["familia"]]
        p[0] += 1
        p[1] += tot
        p[2] += ent
        p[3].append(tot)
    cmp_ = comparar_con_orquestador(tok, system, filas, textos)

    def pct(v, q):
        v = sorted(v)
        return v[int(q * (len(v) - 1))]

    L = []
    A = L.append
    A("# Informe 5.4: formateo del dataset 0.4.0 (solo formateo, sin entrenar)\n")
    A("Marcas: COMPROBADO = medido por el script; REPORTADO = lo dice un archivo o documento.\n")
    A("## Parámetros\n")
    A(f"- Entrada: `train_nuevos.jsonl` ({len(nuevos)}) + `train_c0.jsonl` ({len(c0)}) de `{r2.name}` = **{len(filas)}** pares. El holdout contextual no entra.")
    A(f"- System: `prompts/sp-0.4.0-1.txt`, sha256 `{SHA_SYSTEM}` (verificado antes de formatear; el script aborta si difiere).")
    A(f"- Plantilla: `tokenizer.apply_chat_template` del tokenizer del adaptador v3.3 (`{tok_dir.name}`): `tokenizer.json` sha256 `{sha256_bytes((tok_dir / 'tokenizer.json').read_bytes())[:16]}…`, "
      f"`chat_template.jinja` sha256 `{sha256_bytes((tok_dir / 'chat_template.jinja').read_bytes())[:16]}…`; transformers {transformers.__version__}.")
    A(f"- **`date_string` fijo = «{FECHA}»** (el mismo de v3.3, `training/convertir_dataset.py`). Cierra el punto de H-002 para el ENTRENAMIENTO: la línea `Today Date: {FECHA}` va en el system de los {len(filas)} pares. "
      "El servicio sigue sin medirse (H-002 sigue abierto en lo que toca a producción).")
    A(f"- Tokenización sin tokens especiales adicionales (la plantilla ya escribe `<|begin_of_text|>`) y sin truncar. Enmascarado: `labels = -100` hasta el encabezado del asistente inclusive "
      "(system, user y encabezados); se aprende la respuesta y el `<|eot_id|>` final. Mismo criterio que `training/entrenar_lora.py`.")
    A(f"- Orden barajado con `random.Random({SEMILLA})` sobre la lista train_nuevos + train_c0.\n")

    A("## Longitudes en tokens (secuencia completa)\n")
    A(f"- mín {min(largos)} · mediana {int(statistics.median(largos))} · p95 {pct(largos, 0.95)} · máx {max(largos)}.")
    A(f"- `max_length` del script de v3.3: **{max_len}** (fuente: {origen_max}).")
    A(f"- **Ejemplos que superan {max_len} tokens: {len(excede)} de {len(items)} ({100 * len(excede) / len(items):.1f} %).** No se truncó ninguno; la lista completa (id, familia, tokens) está en `excede_max_length.json`.")
    if excede:
        top = sorted(excede, key=lambda e: -e["tokens"])[:10]
        A("  Los 10 más largos: " + ", ".join(f"{e['id']} ({e['tokens']})" for e in top) + ".")
        porf = Counter(e["familia"] for e in excede)
        A("  Por familia: " + ", ".join(f"{k} {v}" for k, v in sorted(porf.items())) + ".")
        A(f"  **Discrepancia con 5.5:** `entrenar_lora.py` trunca a {max_len} (`truncation=True`) y con ese valor se cortaría la respuesta de esos ejemplos, "
          f"incluido el `<|eot_id|>`. El entrenamiento necesita `MAX_LENGTH` ≥ {max(largos)} (el tope de entrada del plan es 1.648 + 400 de respuesta).")
    A("")
    A("## Tokens entrenables frente a totales, por familia\n")
    A("| Familia | Pares | Tokens totales | Tokens entrenables | % entrenable | mín / mediana / p95 / máx |\n|---|---:|---:|---:|---:|---|")
    tp = tt = te = 0
    for fam in sorted(por_fam):
        n, tot, ent, v = por_fam[fam]
        A(f"| {fam} | {n} | {tot} | {ent} | {100 * ent / tot:.1f} % | {min(v)} / {int(statistics.median(v))} / {pct(v, 0.95)} / {max(v)} |")
        tp, tt, te = tp + n, tt + tot, te + ent
    A(f"| **Total** | **{tp}** | **{tt}** | **{te}** | **{100 * te / tt:.1f} %** | |")
    A("")
    A("## Conteo por familia frente al informe de r2\n")
    A("| Subfamilia | Pares formateados |\n|---|---:|")
    for k in sorted(real):
        A(f"| {k} | {real[k]} |")
    A(f"| C0 | {len(c0)} |")
    A("")
    A("Coincide con las tablas de `informe.md` de r2: " + ("**sí, sin diferencias**." if not discrepancias else "**NO**: " + "; ".join(discrepancias)))
    A("")
    A("## Comparación con el orquestador real\n")
    A("Para cada par se toma su `instruction`, se pasa por `ensamblar()` del backend (el mismo que arma la petición en producción, con `cargar_system()` y la sanitización de la pregunta), "
      "y los mensajes resultantes se renderizan con la misma plantilla. No se usó la base de datos: el contexto se reconstruye con las líneas ya escritas del par.\n")
    A(f"- Pares comparados: {cmp_['comparados']}; **idénticos byte a byte: {cmp_['iguales']}**; distintos: {len(cmp_['difieren'])}"
      f"{' (' + ', '.join(cmp_['difieren'][:10]) + ')' if cmp_['difieren'] else ''}.")
    A(f"- No comparados: {cmp_['omitidos_por_nota_de_omision']} pares con la nota «Se omitieron N datos…» (la nota la genera la selección del contexto, que necesita los candidatos originales).")
    A("- Por tipo: " + ", ".join(f"{k} {v}" for k, v in sorted(cmp_["por_tipo"].items())) + ".")
    ejemplo_a = next(r for r in filas if r["familia_salida"] == "A")
    ejemplo_c0 = next(r for r in filas if r["familia_salida"] == "C0")
    for etiqueta, r in (("par A", ejemplo_a), ("par C0", ejemplo_c0)):
        ctx, pregunta = user_a_candidatos(r["instruction"])
        from services.orquestador.ensamblador import ensamblar
        ok = ctx is not None and renderizar(tok, *[m["contenido"] for m in ensamblar(pregunta, ctx, {"usuario_id": 0, "consentimiento_entrenamiento": False}, system=system)["peticion"]["mensajes"]], r["response"]) == textos[r["id"]]
        A(f"- {etiqueta} (`{r['id']}`): render del formateador = render del orquestador → **{'igual' if ok else 'DISTINTO'}**.")
    A("")
    A("## Tres ejemplos del enmascarado (token, label)\n")
    A("Se muestran los últimos 4 tokens enmascarados, los 4 primeros aprendidos y los 3 últimos. `-100` = no se aprende.\n")
    for fam in ("A", "C0", "B"):
        x = next(i for i in items if i["familia"] == fam)
        ids, labels = x["input_ids"], x["labels"]
        n = next(i for i, l in enumerate(labels) if l != -100)
        A(f"**{x['id']}** ({fam}; {len(ids)} tokens, {len(ids) - n} entrenables)\n")
        A("| Posición | token_id | token | label |\n|---:|---:|---|---:|")
        for p in list(range(n - 4, n + 4)) + list(range(len(ids) - 3, len(ids))):
            A(f"| {p} | {ids[p]} | `{tok.decode([ids[p]]).replace(chr(10), chr(92) + 'n').replace('|', chr(92) + '|')}` | {labels[p]} |")
        assert tok.decode(ids[:n]).endswith(MARCADOR_ASSISTANT) and tok.decode(ids[-1:]) == "<|eot_id|>" and labels[-1] == ids[-1]
        A("")
    A("Comprobado por aserción para los 3: el prefijo enmascarado termina en el encabezado del asistente y el último token aprendido es `<|eot_id|>`.\n")
    A("## Archivos y reproducción\n")
    A("| Archivo | sha256 |\n|---|---|")
    A(f"| `formateado/train_tokenizado.jsonl` ({len(items)} líneas: input_ids, attention_mask, labels, id, familia) | `{sha256_bytes(salida.read_bytes())}` |")
    A(f"| `formateado/excede_max_length.json` | `{sha256_bytes((destino / 'excede_max_length.json').read_bytes())}` |")
    A("")
    A("`familia` es la del par (A, B, C, D, E, F) y `C0` para el train base reenvuelto. Comando:\n")
    A(f"```\npython -m scripts.dataset_0_4.formatear --r2 ../{r2.name}\n```\n")
    A("Determinismo: el informe no contiene fechas; dos corridas dan el mismo sha256 de `train_tokenizado.jsonl` (comprobado al entregar, ver el PR).")
    (destino / "informe_5_4.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    print(f"pares={len(items)} excede_{max_len}={len(excede)} iguales_orquestador={cmp_['iguales']}/{cmp_['comparados']} difieren={len(cmp_['difieren'])} discrepancias={len(discrepancias)} -> {destino}")


if __name__ == "__main__":
    main()
