# Generador de pares de entrenamiento 0.4.0 (paso 5.2)

Genera los pares nuevos del dataset 0.4.0 del chatbot a partir de **datos reales de `mapcalordb`** y de las **funciones reales del orquestador** (`services/orquestador/`). Especificación: `techo360/claude/fase5-1-plantillas-pares.md`; tarea: `techo360/claude/prompt-code-5-2.md`.

Este directorio es solo código. Los archivos generados van a `techo360/trabajo-dataset-0.4.0/` (fuera de los repos, sin commitear).

## Reglas con las que está construido

- **El contexto nunca se escribe a mano.** Cada par pide el contexto a `construir_contexto` y arma el `user` con `ensamblar` del backend (`datos.py`). El system es `sp-0.4.0-1`, leído con `cargar_system()` (valida el sha256).
- **Las respuestas salen de plantillas** (`resp_*.py`); las cifras las inserta el generador desde el contexto ya renderizado. Nunca un LLM, nunca una llamada a un modelo.
- **Solo lectura.** La sesión se abre con `default_transaction_read_only = on` y se confirma con `SHOW`; si no es `on`, el generador aborta. Todas las consultas son `SELECT` parametrizados. No escribe en la base.
- **Misma semilla ⇒ mismos archivos byte a byte.** Cada subfamilia tiene su propio generador pseudoaleatorio derivado de la semilla; las listas se recorren siempre ordenadas. La salida usa `\n`.
- **El resolutor real decide qué contexto es posible.** Antes de aceptar una pregunta, `resolver()` debe llegar a la misma entrada (municipios, variables y años) con la que se renderizó el contexto. Si no, producción no armaría ese contexto y el candidato se descarta (`gen:resolutor_*`).

## Cómo se ejecuta

Desde `techo360-backend/`:

```
python -m scripts.dataset_0_4.generar --salida ../trabajo-dataset-0.4.0 --semilla 20261017
```

Necesita:

| Entrada | De dónde sale |
|---|---|
| Base de datos | `DATABASE_URL` de `techo360-backend/.env` (no se imprime), en solo lectura |
| Núcleo de huecos | `techo360-proyeccion/app/services/puntos.py` (la misma lógica que atiende el RPC `puntos`); `--proyeccion-dir` |
| Train y holdouts base | rama `origin/feat/dataset-0.4.0-base` de `techo360-chatbot`, leída con `git show` (`--chatbot-dir`, `--base-ref`) |
| Tokenizer | `llama-cpp-bin/llama-tokenize.exe` y `llama.cpp-tools/models/ggml-vocab-llama-bpe.gguf` (solo el vocabulario Llama 3; no hay pesos ni inferencia) |

Opciones útiles: `--escala 0.1` (cuotas reducidas), `--sin-c0`, `--sin-informe`, `--cache-tokens`, `--b3-sin-cifras` (B3 sin citar el dato; el valor por defecto es citarlo), `--c0-retirar-colombia` (retira también los 69 pares de C0 que solo contaban como municipio por «Colombia»; C0 = 727).

## Qué produce

| Archivo | Contenido |
|---|---|
| `train_nuevos.jsonl` | ~1.200 pares nuevos (A 580, B 160, C 240, D 120, E 70, F 30) |
| `train_c0.jsonl` | los pares del train base (735 + 69 actualizados) reenvueltos con el bloque vacío `CONTEXTO / Sin datos disponibles para esta consulta.`; conservan su respuesta; se retiran los que en producción resolverían un municipio (`c0_retirados.json`) |
| `holdout_ctx_v0_4.jsonl` | 60 pares sobre municipios reservados (A 30, B 12, C 6, D 6, E 6) |
| `reservados.json` | lista de ~5 % de municipios (y su sha256) que no aparecen en ningún contexto de train |
| `informe.md` | conteos contra cuota, descartes por motivo, aperturas, diversidad, distribuciones, sha256, ejemplos y hallazgos |

Cada línea sigue el esquema del dataset base (`instruction` = mensaje de usuario completo, `response`, `source`) y agrega `id`, `familia`, `subfamilia`, `version_prompt`, `system_sha256`, `semilla`, `entrada` (municipios, variables, años, pregunta y huecos con que se renderizó, para poder re-renderizar) y los identificadores de plantilla de pregunta y de respuesta. `source` no entra al prompt.

## Controles por par (un fallo descarta con motivo y todo descarte se cuenta)

1. **Prueba dorada** (`controles.dorada`): re-renderizar `entrada` con el ensamblador real da el mismo `user` byte a byte.
2. **Validador de cifras 4.5** en A, D, E y F; en B y C no hay números salvo años del contexto o de la pregunta.
3. B sin «aproximadamente», «estimo», «alrededor», «cerca de» (ni «aproxim…»).
4. system con el sha256 de `sp-0.4.0-1`; **tokens medidos** con el tokenizer de Llama 3: prompt completo ≤ 1.648 y respuesta ≤ 400.
5. Sin emojis, Markdown, LightGBM ni repos sin prefijo `techo360-`; ni la marca «Techo 360» (ver más abajo).
6. Ninguna pregunta de train con similitud ≥ 0,75 contra `holdout_v3_3`, `holdout_v0_4` y `holdout_ctx_v0_4`; deduplicación < 0,75 dentro de cada familia, en pregunta y en respuesta por separado (TF-IDF de uni y bigramas, la misma definición de `dataset/validacion_v3_3/simlib.py`).
7. Diversidad: se reporta el cumplimiento de las formulaciones por subfamilia y de las plantillas por familia; no se rellena con casi-duplicados.
8. Máximo 3 pares por la misma terna municipio-variable-año.
9. **Lint de redacción** (5.2b D1): sin «de el», «a el», «No hay el/la/los/las», «ese año… ese año…», mayúscula tras «: » (salvo nombres del contexto o de la fuente) ni minúscula tras «. ».
10. **Cobertura** (5.2b D2): en A, D, E, B3 y B4 con valor, todo valor de las líneas del contexto aparece literal en la respuesta.
11. Reglas de la respuesta derivadas de fase5-1 §2: cada dato con `Municipio (Departamento)` y año copiados del contexto; advertencia y fiabilidad solo en proyecciones.

## Decisiones y límites que conviene conocer

- **«Techo 360» no aparece en las respuestas** (salvo dentro del nombre de una fuente copiado del contexto). El validador ya exime la marca (5.2b H11), pero se conserva el estilo «la plataforma».
- **B3 cita el valor disponible** (decisión del 17/10, validado por 4.5); `--b3-sin-cifras` reproduce el literal de fase5-1 §6.2.
- **Años de la pregunta** (1900–2100) se pueden repetir en la respuesta (5.2b H12).
- **«Colombia» es el país** salvo «municipio de Colombia» o con Huila (5.2b H1): las preguntas nacionales de C1 y B2 la usan (≥ 40 %).
- **Huecos y motivos de «Sin dato».** Salen del núcleo real de `techo360-proyeccion` sobre la serie de la base; solo `valor_sospechoso` se construye con la entrada `huecos` (misma ruta de `construir_contexto`). No se generan `fuera_de_universo` ni `score_no_calculable`: el resolutor no puede producirlos.
- **Cifras derivadas** (≤ 10 % de los pares): una diferencia o una variación porcentual entre valores del contexto; se calculan sobre los valores **mostrados** y solo se usan si coinciden con el cálculo del validador (que usa los valores sin redondear).

## Pruebas

```
python -m unittest scripts.dataset_0_4.tests_generador                 # puras
TECHO360_PRUEBAS_BD=1 python -m unittest scripts.dataset_0_4.tests_generador   # + base de datos (solo lectura)
```

Las pruebas con base incluyen un ejemplo fijo por subfamilia, la prueba dorada, la deduplicación y el determinismo (dos corridas, mismos sha256).
