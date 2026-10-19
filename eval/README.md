# Evaluacion Fase 9

La evaluacion contiene 200 casos en `fase9_preguntas.jsonl`: 50 `datos`, 35 `graficas`, 35 `conceptuales`, 30 `seguimientos`, 25 `abstencion` y 25 `adversariales`.

Los casos de datos y seguimiento se generaron exclusivamente desde `training/datos/snapshot_mapcalordb_20261019/mapcalordb.csv` y `zonas_municipio.csv`. En este worktree esos archivos viven en el worktree local del chatbot; no se consulto produccion, SSH ni ningun modelo. El generador conserva el origen real/proyeccion del snapshot en `esperado`.

## Reproducir el banco y la comprobacion de solapamiento

```powershell
python eval/generar_fase9.py --snapshot ..\techo360-chatbot-wt-ds71\training\datos\snapshot_mapcalordb_20261019 --train ..\trabajo-dataset-0.4.1-v2\dataset_0_4_1_sin_h4.jsonl --holdout ..\trabajo-dataset-0.4.1-v2\holdout_rag.jsonl ..\trabajo-dataset-0.4.1-v2\holdout_hist.jsonl ..\trabajo-dataset-0.4.1-v2\holdout_graf.jsonl --salida eval\fase9_preguntas.jsonl
```

La comprobacion normaliza minusculas, elimina tildes y comprueba igualdad exacta y Jaccard por palabras contra el entrenamiento y los tres holdouts. El resultado de la generacion de esta rama fue: 200 casos, cuotas exactas, 6.287 textos comparados, 0 igualdades y Jaccard maximo 0,461538. El valor exacto se imprime siempre para que el PR sea reproducible.

## Ejecutar

El script no levanta Ollama ni un servidor. Recibe una URL compatible con `/api/chat` o con el endpoint RPC HTTP del backend, envia el historial y guarda respuestas crudas:

```powershell
python eval/ejecutar_fase9.py http://127.0.0.1:11434/api/chat --modelo fase9 --salida eval/resultados_20261019.json
```

Valida cifras con `services.orquestador.validador_cifras` 4.5 cuando esta disponible; registra abstencion, formato, resistencia a inyeccion, bloque `grafica` contra el catalogo y p50/p95. Los casos de grafica aceptan solo años 2018--2042 y combinaciones de `services.reportes_graficas_catalogo`.

El test `test_ejecutar_fase9.py` crea un servidor HTTP local con respuestas fijas, ejecuta el cliente y comprueba que guarda el JSON de resultados. No descarga, inicia ni llama ningun modelo real.