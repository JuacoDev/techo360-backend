# techo360-backend

API FastAPI de Techo 360.

<!-- Prueba de despliegue automatico: 2026-07-27 11:03:25 -->

<!-- Prueba 2 de despliegue: 2026-07-27 11:13:05 -->

<!-- Prueba 3 de despliegue: 2026-07-27 11:32:25 -->

## Limitacion conocida: remitente sin autenticar

El modulo de Email Sender (Brevo) usa actualmente `juan.tellez@upb.edu.co` como remitente temporal, porque el dominio propio del proyecto (`techo360.bucaramanga.upb.edu.co`) sigue pendiente de entrega por parte del CTIC.

Esto significa que los correos SI se envian y llegan correctamente, pero clientes como Outlook los marca como 'no verificados' o los manda a spam, porque Brevo no tiene permiso DNS (SPF/DKIM) para enviar en nombre de un dominio de UPB que no controla.

**Esto es aceptable para pruebas internas del equipo, pero NO debe usarse asi en produccion real** (los usuarios finales no deberian recibir OTPs marcados como spam).

**Cuando el CTIC entregue el DNS**, hay que: autenticar techo360.bucaramanga.upb.edu.co en Brevo (Settings > Senders, Domains, IPs > Domains > Authenticate a domain), agregar los registros SPF/DKIM que de Brevo al DNS del subdominio, y actualizar `brevo_sender_email` en core/config.py para usar un correo de ese dominio (ej. no-reply@techo360.bucaramanga.upb.edu.co) en vez del correo personal actual.

## Estado en producción (`development`, 4–5-oct-2026)

Estado de `development` (lo que corre en producción) hasta el SHA `a4553e6`. La rama `testing` está desfasada respecto a `development` (6 commits propios y 6 por detrás a 5-oct), por eso este README documenta `development`. Cada cambio se desplegó por separado y se revierte con `git revert -m 1 <SHA del merge>` y un nuevo deploy (push a `development`).

| Paso | PR | SHA del merge | SHA anterior | Qué cambia |
|---|---|---|---|---|
| D1 | #8 | `14b282f` | `0398f5a` | `/api/zonas` excluye el Área en Litigio Cauca-Huila (código `00000` / `00`). |
| D5 | #10 | `3587950` | `14b282f` | Sesión de 8 horas con una constante única: `ACCESS_TOKEN_EXPIRE_HOURS = 8` en `core/config.py`. |
| D6 | #11 | `cab98e1` | `3587950` | Caché Redis de `/api/zonas` con ETag/304, `X-Cache` y precalentamiento. |
| D8 | #14 | `a4553e6` | `cab98e1` | Estados de los mensajes del chatbot, reintentar/cancelar/eliminar, interruptor y barrido (ver abajo). |

### Caché Redis de `/api/zonas` (D6)
- Cachea la respuesta gzip por nivel (`departamento`, `municipio`) y **solo el año 2024**. Claves `zonas:v1:...`, TTL de 36 h.
- Responde con `ETag` y `304` si el cliente ya lo tiene; `Cache-Control: private, no-cache`; la cabecera `X-Cache` indica `HIT`, `MISS` o `BYPASS`.
- Un candado (`SET NX`, 60 s) evita varios cálculos a la vez; un cortacircuitos (circuit breaker, 30 s) deja pasar sin caché si Redis falla (timeouts de 1 s para conectar y 3 s para leer).
- Precalentamiento: ~90 s después de arrancar el backend y todos los días a las 03:30 (hora del contenedor, UTC). La ingesta invalida la caché (SCAN) y la vuelve a calentar.

### Chatbot: estados, endpoints e interruptor (D8)
- Estados del mensaje de usuario: `pendiente` → `procesando` → `respondido` | `error` | `cancelado`. El del asistente es siempre `respondido`. Columnas nuevas en `mensajes`: `estado`, `error_detalle` (máx. 500 caracteres), `actualizado_en` e `intentos` (cuenta el intento que espera la respuesta; un RPC cancelado no pisa un reintento posterior).
- Un índice único parcial permite como máximo **un mensaje de usuario sin resolver por conversación**. Con el interruptor encendido, enviar otro devuelve **409** con `mensaje_pendiente`.
- Endpoints: `POST /chatbot/mensajes/{id}/reintentar`, `POST /chatbot/mensajes/{id}/cancelar` (idempotente) y `DELETE /chatbot/mensajes/{id}`. Un id ajeno o inexistente da el mismo 404.
- Interruptor `CHATBOT_BLOQUEO_PENDIENTES` (por defecto apagado; se encendió en producción el 5-oct). Apagado: ruta de siempre, el mensaje se guarda `respondido` y no hay 409. Cambiarlo exige recrear el contenedor (`docker compose up -d --no-deps backend`, no `restart`).
- Barrido de mensajes colgados: perezoso (los `pendiente`/`procesando` de más de 4 min pasan a `error`) y al arrancar (todos los `pendiente`/`procesando` pasan a `error`; correcto con un solo contenedor).
- RPC al chatbot por RabbitMQ con tope de 200 s (`RPC_TIMEOUT_SECONDS`). Las sesiones de BD se liberan antes del RPC.
- Migración de la base: `techo360-chatbot/migrations/0002_mensajes_estado.sql` (aplicada a mano el 5-oct; antes que el backend).

### Qué guarda Redis (sin persistencia)
- Caché: `zonas:v1:...`, `proyeccion:v3:zonas:...` (contrato con origen/fiabilidad/componentes, 10-oct-2026), `proyeccion:v2:limites_base:...` y `proyeccion:serie:...`. Las `proyeccion:v2:zonas:*` viejas expiran solas (36 h); `scripts/limpiar_cache_proyeccion.py` las lista (y con `--ejecutar` las borra; no se ejecuta sin autorización).
- OTP y límites: `otp:<correo>:code` (10 min), `otp:<correo>:attempts` (5 intentos), `otp:<correo>:cooldown` (60 s) y `otp:ip_limit:<ip>` (10 por 15 min), y `email:sent_count:<fecha>`.
- Guardias del scheduler: `pipeline_datos:ultima_corrida:<fecha>` y `zonas_igac:ultima_corrida:<fecha>` (26 h).
- Las sesiones **no** están en Redis (son JWT). El volumen de Redis es anónimo: se pierde al recrear el contenedor.

### Pipeline semanal (lunes 03:00 UTC): fallos del 5-oct
Resumen de la corrida: 6 fuentes OK, 0 registros rechazados, 0 advertencias, 1 bloqueada y 1 con error; se envió el correo de alerta.
- INVIAS: certificado vencido (`CERTIFICATE_VERIFY_FAILED`), con respaldo OSM.
- Overpass (OSM): `504 Gateway Timeout` en `conectividad_vial` (la fuente con error).
- Dataset FNA `8njd-mfua` (`cercania_constructoras_vis`): `404 Not Found` (bloqueada).
- No verificado: si estos fallos ya ocurrían en semanas anteriores.

### Pruebas
`unittest` con dobles, sin framework, ejecutadas el 4–5-oct: `python -m routers.tests_chatbot_estados` (42), `services.tests_zonas_cache` (49), `services.tests_precalentamiento` (22), `services.tests_sesion_token` (10), `services.tests_score_privacion` (14), `services.tests_contrato_proyeccion` (25, etiquetado real/proyección y score con componentes), `services.tests_invalidacion_ingesta` (4) y `routers.tests_zonas_excluidas` (5). Las pruebas contra PostgreSQL real (`services.tests_chatbot_estados_pg`, 36) se saltan solas salvo que `PG_BIN` apunte a los binarios de PostgreSQL 16 y `MIGRACION_0002_SQL` a la migración.

### No verificado
Reintentar con la cola ocupada y el posible 504 de nginx (ver el README del frontend); el reencolado de un mensaje en vuelo al recrear el chatbot; los proxies fuera del VPS.


## Modo local: `DESACTIVAR_TAREAS_DE_FONDO=1`
Al arrancar, el backend lanza tareas que tocan recursos compartidos: el scheduler de ingesta (lunes 03:00 en la hora del reloj de la máquina; trimestral de zonas; precalentamiento diario 03:30), el `ALTER TABLE` de `public.reportes`, los consumers de RabbitMQ (`chatbot_eventos_persistencia`, prefetch 4, y `reportes_generacion`, prefetch 2), el barrido de mensajes `pendiente`/`procesando` a `error` y el precalentamiento de cachés. En una máquina de desarrollo apuntando a servicios reales, eso compite con el backend de producción (se llevaría eventos y trabajos de reportes, y reiniciar con `--reload` volvería a barrer mensajes).

Con `DESACTIVAR_TAREAS_DE_FONDO=1` en el `.env` local, el evento `startup` imprime `[main] modo local: tareas de fondo desactivadas` y **no arranca nada de lo anterior**; la API sigue sirviendo peticiones. Sin la variable el comportamiento es el de siempre. **No debe definirse en producción.** Límites: no impide conectarse a RabbitMQ, a las bases ni a Redis al atender peticiones. Con las tareas apagadas, `POST /api/reportes/generar` responde 503 (no hay canal de reportes y no publica), pero el chat sí abre conexión y publica en `chatbot_requests` de la RabbitMQ a la que apunte `RABBITMQ_URL`. Para aislarse del todo, usar un RabbitMQ y bases propios. Pruebas: `python -m routers.tests_tareas_de_fondo`.

## Reportes asíncronos (8-oct-2026)

La tabla `public.reportes` se creó a mano en `postgres-principal` (base `techo360_principal`) antes del deploy: `preparar_esquema_reportes` solo hace `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` y falla al arrancar si la tabla no existe. El DDL fue `CREATE TABLE IF NOT EXISTS` con las columnas de `models/reporte.py` más `estado`, `formato`, `solicitud`, `archivo_nombre` y `error`, y el índice `ix_reportes_usuario_id`. Cola nueva: `reportes_generacion` (la publica y la consume el backend).

### Deuda: los archivos de reportes no persisten
Los reportes generados se guardan en `data/reportes` dentro del contenedor, sin volumen. Cada recreación del contenedor del backend borra los archivos; las filas de `public.reportes` quedan con `estado = 'listo'` y `archivo_nombre` apuntando a un archivo que ya no existe. Pendiente: un volumen para `reportes_dir` (o almacenamiento externo) en el compose de infra.
