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
