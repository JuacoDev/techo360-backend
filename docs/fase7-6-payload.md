# Paso 7.6 (mitad backend): payload de la gráfica del chat

Code, 19/10/2026, paquete W1. Fija el payload propuesto en `fase7-1-especificacion.md` §2.5. Lo implementa `techo360-backend` (rama `feat/grafica-w1`); lo consume el renderizador del chat (W2, frontend) sin consultar nada por su cuenta.

## 1. Dónde llega

El campo nuevo es `grafica` en `MensajeResponse` (`schemas/chatbot.py`), **solo en el mensaje del asistente**. Es `null` en todo lo demás (mensajes del usuario, respuestas sin gráfica, gráfica descartada, orquestador apagado).

| Endpoint | Dónde |
|---|---|
| `POST /chatbot/conversaciones/{id}/mensajes/enviar` | `mensaje_asistente.grafica` |
| `POST /chatbot/mensajes/{id}/reintentar` | `mensaje_asistente.grafica` |
| `GET /chatbot/conversaciones/{id}/mensajes` | `grafica` de cada mensaje del asistente (solo si `CHATBOT_ORQUESTADOR_ACTIVO`) |

Un frontend viejo ignora el campo. El texto del mensaje **nunca** contiene el bloque ```` ```grafica ````: el backend lo quita siempre. Si `grafica` es `null`, se muestra solo el texto.

## 2. Forma

```json
{
  "v": 1,
  "tipo": "lineas",
  "modo": "individual",
  "variables": ["cobertura_acueducto"],
  "zonas": ["68001"],
  "anios": [2019, 2020, 2021, 2022, 2023, 2024],
  "titulo": "opcional",
  "datos": [
    {"variable": "cobertura_acueducto", "zona": "68001", "nombre": "Bucaramanga (Santander)",
     "anio": 2019, "valor": 90.1, "origen": "real", "fiabilidad": null}
  ],
  "universo": null
}
```

| Campo | Significado |
|---|---|
| `v` | Versión del payload, entero, hoy 1. El frontend debe ignorar un `v` que no conozca. |
| `tipo`, `modo`, `variables`, `zonas`, `anios` | La especificación **ya validada** (esquema cerrado de 2.4-B y combinación de `validar_combinacion`). `zonas` son códigos DIVIPOLA; `anios` ordenados, sin repetidos, 2018–2042. |
| `titulo` | Solo si el modelo lo emitió (0.4.1 no lo emite). Texto plano ≤ 80 caracteres, sin Markdown ni HTML. Si falta, la clave no existe. |
| `datos[]` | Un punto por variable × zona × año **que tiene valor**. Orden: variable (como en `variables`), zona (como en `zonas`), año ascendente. Un año sin valor no aparece (no se inventa ni se rellena con `null`). |
| `datos[].nombre` | `Municipio (Departamento)`, listo para la leyenda (distingue homónimos). |
| `datos[].valor` | Número finito, 4 decimales como máximo, en la unidad de la variable: porcentaje 0–100, `hab/km²`, o índice 0–1 para `score_privacion`. |
| `datos[].origen` | `"real"` o `"proyeccion"`. El frontend marca como proyección todo lo que no sea `real` (misma regla que Reportes). |
| `datos[].fiabilidad` | Entero 0–100 para proyecciones; `null` en datos reales. |
| `universo` | `null`, salvo en los tipos que necesitan el universo nacional según el catálogo (`gauge`, `bullet`, `barras_ranking`, `radar`, …). Ver §3. |

## 3. `universo`

```json
{"periodo": [2024, 2024],
 "estadisticos": {"cobertura_acueducto": {"media": 70.2, "min": 4.0, "q1": 58.1, "mediana": 72.4, "q3": 85.0, "max": 99.9, "n": 1100}}}
```

Estadísticos de los municipios del país (promedio del periodo de cada municipio; `n` = municipios con dato), calculados con `reportes_datos_graficas.calcular_nacional`, el mismo cálculo del reporte. `periodo` es `[min(anios), max(anios)]`. Hay una entrada por cada variable de `variables`; si falta alguna, la gráfica no se entrega (queda sin gráfica y se registra el motivo).

Nota: para `score_privacion` el universo usa la normalización min-max **por año** de Reportes, mientras que el score del municipio sale del cálculo del chat (límites fijos de 2024, como la proyección). Las escalas pueden diferir ligeramente; un gauge del score debe mostrar el valor del municipio contra el rango, no recalcularlo.

## 4. Qué emite hoy el modelo (0.4.1) y qué acepta el backend

El modelo emite `lineas`, `barras_ranking`, `barras_agrupadas` y `gauge`. El backend valida y entrega **cualquiera de los 16 tipos del catálogo** si el bloque es válido; el renderizador debe ignorar con elegancia (mostrar solo el texto) un `tipo` que no sepa dibujar.

## 5. Cuándo NO hay gráfica (`grafica = null`)

- el modelo no emitió bloque;
- bloque con JSON roto, sin cierre, clave desconocida o combinación inválida;
- código de zona que no existe en `zonas_municipio`;
- menos de 2 puntos para `lineas` (1 para los demás) o `anios` vacío;
- falta el universo de un tipo que lo necesita, o falla la lectura de la base;
- la respuesta cayó a la determinista (cifra no validada dos veces).

El motivo queda en `mensajes.grafica` (`{"spec", "valida", "motivo", "payload"}`) para auditoría; **el cliente nunca ve `spec` ni `motivo`**.

## 6. Persistencia y tamaño

`mensajes.grafica` (JSONB, migración 0004, sin cambios de esquema) guarda `spec`, `valida`, `motivo` y, si es válida, el `payload` completo: el historial muestra lo mismo que vio el usuario aunque los datos cambien después. Tamaño máximo teórico: 6 variables × 10 zonas × 25 años = 1.500 filas (~200 KB); lo normal son < 50 filas.

## 7. Diferencias respecto al borrador de `fase7-1-especificacion.md` §2.5

1. `nombre` es `Municipio (Departamento)` y no solo el municipio.
2. `universo` pasó de `{...}` libre a `{periodo, estadisticos}`; aplica a todo tipo que lo necesite en el catálogo, no solo gauge y ranking.
3. `titulo` se conserva si existe (el esquema 2.4-B lo admite aunque 0.4.1 no lo emita).
4. El cliente recibe solo el payload en `MensajeResponse.grafica`, no `valida`/`motivo`.
