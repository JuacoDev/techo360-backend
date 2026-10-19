# Paso 7.1b / H1: formato del mensaje multi-turno (para el generador de datos)

Code, 19/10/2026, paquete H1. Implementa `claude/fase7-1b-adenda-modelo-final.md` §3. Lo usa el generador de las familias HS (DS) y lo produce el ensamblador real (`services/orquestador/ensamblador.py`); si este documento y el código chocan, manda el código.

## 1. Forma

Una petición multi-turno es una lista de `mensajes` de chat (contrato RPC v2, `MAX_MENSAJES` 12), **sin etiquetas ni separadores propios**: el historial son turnos de chat normales.

```
[system]      system prompt (sp-0.4.1-1)
[user]        pregunta del intercambio más antiguo   (texto plano, saneado)
[assistant]   respuesta de ese intercambio            (texto plano, completo)
[user]        pregunta del intercambio más reciente
[assistant]   respuesta de ese intercambio
[user]        "CONTEXTO\n<líneas>\n\nPREGUNTA DEL USUARIO\n<pregunta actual>"
```

- Cero intercambios previos = `[system, user]`, idéntico byte a byte al de 0.4.0.
- Máximo **2** intercambios previos (los últimos). Con 1 intercambio van 4 mensajes (`system, user, assistant, user`).
- Los mensajes previos **no llevan** `CONTEXTO` ni `PREGUNTA DEL USUARIO` ni ids `[cN]`/`[dN]`: solo el texto de la pregunta y el texto de la respuesta tal como se guardaron. Solo el último `user` lleva el CONTEXTO fresco del turno actual (con `[cN]`, ubicación, «Sin dato», línea de opciones de gráfica y `[dN]`, como siempre) y la PREGUNTA.
- Las cifras de la respuesta final salen del CONTEXTO actual, nunca de los mensajes previos (regla 10 del system y validador 4.5, que no cuenta el historial como fuente).
- Pérdida del entrenamiento: solo sobre la última respuesta del asistente (la que se genera tras el último `user`); las `assistant` previas son contexto, no objetivo.

## 2. Saneado y recorte (lo que hace el ensamblador antes de armar la lista)

1. **Pregunta previa:** misma limpieza que la pregunta actual (sin caracteres de control, espacios colapsados, líneas en blanco repetidas fuera, corte a 1.500 caracteres). Una línea que imite un delimitador (`CONTEXTO`, `PREGUNTA DEL USUARIO`, `DOCUMENTOS`, `[cN]`/`[dN]` al inicio, o la línea «Gráfica disponible.») se neutraliza anteponiendo `» ` (HS6: un turno previo no puede fingir contexto ni instrucciones).
2. **Respuesta previa:** misma neutralización, **sin corte**.
3. **Tope de 400 tokens** para todo el historial (estimación 2,3 caracteres por token, más 6 tokens por mensaje de rol): si no cabe se descarta el intercambio **más antiguo**; si el intercambio más reciente solo ya pasa de 400, el historial queda vacío. Nunca se corta una respuesta a la mitad.
4. **Presupuesto total** 2.608 tokens (num_ctx 3072 − num_predict 400 − 64). Si se excede, orden de recorte: fragmentos RAG (el menos relevante primero) → historial (el intercambio más antiguo primero) → líneas de contexto de menor prioridad. Ni el system ni la pregunta actual se tocan; si la pregunta sola no cabe no se llama al modelo.
5. Un intercambio cuya pregunta o respuesta queda vacía tras sanear se ignora.

Para que un par de entrenamiento coincida con producción, el generador debe **armar `mensajes` llamando a `ensamblar(pregunta, contexto, metadatos, system, historial=[{"pregunta", "respuesta"}, ...])`** (el más antiguo primero) y no concatenar texto por su cuenta. Las notas devueltas traen `historial_intercambios`, `historial_tokens` y `heredado`.

## 3. Ejemplo real (HS1, seguimiento de año)

Salida del pipeline con el modelo doblado (system abreviado):

```json
[
  {"rol": "system", "contenido": "<system sp-0.4.1-1>"},
  {"rol": "user", "contenido": "¿Cuál es la cobertura de acueducto en Bucaramanga en 2019?"},
  {"rol": "assistant", "contenido": "En Bucaramanga (Santander), la cobertura de acueducto en 2019 fue 80,0 % (dato real)."},
  {"rol": "user", "contenido": "CONTEXTO\n[c1] Cobertura de acueducto, Bucaramanga (Santander), 2020: 81,0 %. Fuente: DNP - TerriData. Dato real.\n\nPREGUNTA DEL USUARIO\n¿y en 2020?"}
]
```

El `CONTEXTO` es el del turno actual (2020), no el del turno anterior (2019): el resolutor heredó municipio y variable (`notas.heredado = ["municipios", "variables"]`).

## 4. Herencia (resolutor, `services/orquestador/herencia.py`)

Entrada: la pregunta actual y la resolución efectiva del turno anterior (se calcula repitiendo el resolutor sobre las preguntas previas de la conversación, encadenado: «¿y en 2020?» → «¿y en Cali?» hereda del anterior ya heredado).

| Pregunta actual trae | Hereda del turno anterior |
|---|---|
| solo año («¿y en 2020?») | municipio y variable |
| solo municipio («¿y en Cali?») | variable y año |
| solo variable («¿y el alcantarillado?») | municipio y año |
| referencia («¿ese año fue real o estimado?», «explícame eso», «ese dato») | todo lo que falte |
| municipio y variable (autocontenida) | nada, ni el año |
| nada y sin referencia (saludo, cambio de tema, concepto: «¿Qué es el IPM?») | nada |

Además: no hereda si la pregunta es conceptual (salvo referencia explícita), si la pregunta misma pide aclaración de homónimo, ni cuando hay una aclaración pendiente (Redis, 4.13) o el turno anterior fue una aclaración sin resolver; tras «el de Antioquia» el turno que resuelve no hereda de antes, pero el siguiente sí hereda el municipio elegido. Una gráfica de evolución sin años no hereda el año (necesita la serie completa, 7.1). Sin herencia entre conversaciones ni usuarios: el historial se lee con ambos ids (§5).

Para generar familias: HS1 = pregunta con municipio+variable+año, luego «solo año»; HS2 = «solo municipio»; HS3 = «solo variable»; HS4 = referencia; HS5 = cambio de tema (sin herencia, el historial viaja pero el contexto es el de la pregunta nueva); HS6 = instrucción en un turno previo. Un seguimiento a un turno que fue aclaración o «Sin dato» sigue las mismas reglas.

## 5. Lectura y aislamiento

`services/orquestador/historial.py: leer_intercambios(db, conversacion_id, usuario_id, antes_de_id)`: una consulta parametrizada sobre postgres-vector que une `mensajes` con `conversaciones` y exige `conversacion_id` **y** `usuario_id` (ambos los pone el servidor, con la conversación ya resuelta para el usuario autenticado). Una conversación ajena o de otro usuario devuelve `[]`. Un intercambio es un mensaje de usuario en estado `respondido` seguido de la respuesta del asistente. Se leen como máximo 8 (para la herencia encadenada) y al modelo viajan los últimos 2. No lee contexto, metadatos ni gráficas.

## 6. Activación

`CHATBOT_HISTORIAL_ACTIVO` (por defecto `false`; requiere `CHATBOT_ORQUESTADOR_ACTIVO`). Apagado: ni se lee el historial ni se hereda, y la petición es la de 0.4.0. Encenderlo solo con el modelo 0.4.1 (la regla 10 del system explica al modelo qué hacer con los mensajes previos). Reversa: apagarlo y recrear el backend. Si la lectura del historial falla, la pregunta se responde sin historial.

## 7. Pendiente fuera de H1

- Aviso del frontend «Para respuestas más rápidas, abre un chat nuevo con frecuencia.» (H2, lo toma W2).
- `sp-0.4.1-1` con las reglas 7–10 (paquete DS/TR): el backend sigue cargando `sp-0.4.0-1` hasta entonces.
- Medir si Ollama reutiliza la caché del prefijo (system + historial): con historial el prefijo cambia por turno salvo el system.
