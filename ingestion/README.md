# Pipeline de Ingesta de Datos — Techo 360

## Estado: completo, los 7 pasos

| Pieza | Archivo | Estado |
|---|---|---|
| 1. Adaptador FNA | `ingestion/fna_adapter.py` | ✅ Construido y probado (columnas SIN confirmar en vivo, ver aviso abajo) |
| 2. Adaptador Vías (INVIAS/OSM) | `ingestion/vias_adapter.py` | ✅ Construido y probado |
| 3. Criterios de aceptación | `ingestion/validacion.py` | ✅ Probado |
| 4. Validación de esquema | `ingestion/validacion_esquema.py` | ✅ Probado, **y conectada de verdad** dentro de `terridata_adapter.py` y `fna_adapter.py` (se llama antes de traer el dataset completo) |
| 5. Alertas al admin | integrado en `run.py` | ✅ Probado, con deduplicación |
| 6. Guardado real (UPSERT/BD) | `ingestion/db.py` | ✅ Probado contra el esquema real de Jeremy (`MapCalorDB`, `ViaDB`) |
| 7. Scheduler (APScheduler) | `ingestion/scheduler.py` | ✅ Integrado en `techo360-backend`, corre semanal (lunes 3am) |

## Cómo correrlo manualmente (para nutrir la base con histórico)

Dentro del contenedor del backend en la VPS:

```bash
docker exec -it techo360-backend python3 -m ingestion.run --anio 2019
```

- `--anio` acepta **cualquier año**, no solo el actual — así se puede
  nutrir el histórico completo corriendo varias veces con años distintos
  (2015, 2016, ..., 2023), no solo el dato más reciente.
- Si se omite `--anio`, cada fuente trae su propio comportamiento por
  defecto (TerriData/FNA devuelven lo disponible sin filtrar por año).
- La URL de conexión a la base se toma automáticamente de la variable
  de entorno `DATABASE_URL` (ya está seteada dentro del contenedor) —
  no hace falta pasarla a mano.
- Usa `--dry-run` para probar sin guardar nada en la base real (solo
  valida y reporta, útil para confirmar que una fuente sigue
  funcionando antes de comprometerse a guardar).

Ejemplo para nutrir varios años de una sola vez (bash):

```bash
for anio in 2018 2019 2020 2021 2022 2023; do
  docker exec -it techo360-backend python3 -m ingestion.run --anio $anio
done
```

## Corrida automática

El scheduler (`ingestion/scheduler.py`) corre solo, cada lunes a las
3am, dentro del proceso del Backend Principal — no requiere ninguna
acción manual una vez desplegado. Tiene una guardia contra doble
ejecución (vía Redis) para el caso de que el backend se reinicie el
mismo día que ya corrió.

## ⚠️ Antes de confiar en los resultados de FNA

Los nombres de columna de `ingestion/fna_adapter.py` nunca se
confirmaron contra la API real (este entorno de desarrollo no tiene
salida hacia `datos.gov.co`). Primer paso real en la VPS:

```bash
docker exec -it techo360-backend python3 -c "
from ingestion.fna_adapter import FNAAdapter
print(FNAAdapter().obtener_muestra(limite=5))
"
```

Y ajustar `COLUMNAS_ESPERADAS`/los nombres de campo en el archivo si no
coinciden. Lo mismo aplica a `ingestion/vias_adapter.py` (INVIAS) con
`obtener_muestra_invias()`.

## Diseño: por qué el guardado de `MapCalorDB` NO usa `INSERT ... ON CONFLICT`

La tabla tiene un trigger `BEFORE INSERT` que lanza una excepción ante
un duplicado — y en Postgres, los triggers `BEFORE INSERT` se ejecutan
ANTES de que se evalúe la cláusula `ON CONFLICT`, así que un UPSERT
clásico nunca llegaría a la parte de `UPDATE`. La solución en
`ingestion/db.py`: se intenta `UPDATE` primero (por la llave única
`municipios + nameVar + yearM`); solo si no actualizó ninguna fila, se
hace `INSERT`. Así el trigger nunca ve un duplicado en el camino
normal.

## Diseño: por qué `ViaDB` se trata como "foto completa"

`ViaDB` no tiene restricción de unicidad — un `INSERT` normal
duplicaría todos los segmentos en cada corrida. Como la red vial casi
no cambia, `reemplazar_vias()` borra todo (`TRUNCATE`) y vuelve a
insertar completo en cada corrida, en vez de actualizar fila por fila.
