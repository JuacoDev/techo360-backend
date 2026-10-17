# Prompts versionados del asistente

Una fila por version del system prompt. El archivo es UTF-8, LF y **sin salto de linea final**; el sha256 es el de los bytes del archivo.
Un cambio de texto crea una version nueva (otro archivo y otra fila); no se edita una version publicada.

| version_prompt | archivo | sha256 | caracteres | tokens (Llama 3.2) | origen |
|---|---|---|---|---|---|
| sp-0.4.0-1 | prompts/sp-0.4.0-1.txt | b63287584676daa3ae514b90884fa8065eeb94f11881711f4517a8b02cfaa9ce | 864 | 241 | claude/fase2-6-system-prompt.md seccion 2 (aprobado 17/10/2026) |

Copia en el backend: techo360-backend/prompts/ (mismo archivo y mismo INDICE.md; una prueba del backend comprueba el sha256 contra este indice).
