from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Duracion del token de acceso (JWT). Valor UNICO: lo usan routers/auth.py y
# services/auth_service.py. Antes era 1 h (duplicado en ambos archivos) y la
# sesion se cerraba aunque el usuario estuviera activo: no hay refresh token.
# Los tokens ya emitidos conservan su caducidad original; solo los nuevos
# duran esto.
ACCESS_TOKEN_EXPIRE_HOURS = 8

# Limites por IP de /otp/request (ver services/otp_service.py). Los valores por defecto son los de
# produccion; se pueden subir con OTP_IP_RATE_LIMIT_MAX / OTP_IP_DAILY_MAX en el .env (p. ej. una
# demo con registro en vivo desde la red de la universidad, que comparte una sola IP).
OTP_IP_RATE_LIMIT_MAX_DEFECTO = 10   # solicitudes por IP cada 15 minutos
OTP_IP_DAILY_MAX_DEFECTO = 150       # solicitudes por IP cada 24 horas
# Maximos admitidos: un valor MAYOR (o invalido) vuelve al DEFECTO, no al maximo -- un error de tipeo
# (4000 en vez de 40) no puede dejar el limite practicamente apagado ni exponer el cupo de Brevo (300/dia).
OTP_IP_RATE_LIMIT_MAX_MAXIMO = 100
OTP_IP_DAILY_MAX_MAXIMO = 500


def entero_positivo_o_defecto(valor, defecto: int, maximo: int | None = None) -> int:
    """Entero > 0 (y <= `maximo`, si se da) leido de una variable de entorno. Vacio, no numerico
    ("abc", "4.5"), cero, negativo o MAYOR que el maximo -> `defecto` (no el maximo): un valor mal
    escrito NUNCA desactiva ni afloja de golpe un limite de seguridad."""
    if isinstance(valor, bool):
        return defecto
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto
    if n <= 0 or (maximo is not None and n > maximo):
        return defecto
    return n


class Settings(BaseSettings):

    # Base de datos utilizada actualmente por SQLAlchemy
    # NOTA: valor por defecto es un placeholder de desarrollo local, NO
    # apunta a produccion. Definir DATABASE_URL en .env para la conexion
    # real (nunca comitear la URL real aqui).
    database_url: str = (
        "postgresql://usuario:password@localhost:5432/techo360_principal"
    )

    # postgres-vector -- conversaciones/mensajes del chatbot (T-97,
    # migracion en techo360-chatbot/migrations/0001_conversaciones_mensajes.sql).
    # Decision del usuario (26-sep-2026): el backend maneja TODA la
    # persistencia -- los servicios de IA (chatbot, proyeccion) solo
    # procesan solicitudes, no son dueños de su propia escritura a BD.
    # Por eso el backend necesita esta credencial (antes solo la tenia
    # techo360-chatbot) en vez de pedirle a chatbot que escriba por su
    # cuenta via RabbitMQ.
    database_vector_url: str = (
        "postgresql://usuario:password@localhost:5432/techo360_vector"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Archivos generados por el consumer de reportes
    reportes_dir: str = "data/reportes"

    # Caché de 24 h de los reportes generados en Redis (ver services/reportes_cache.py). El Redis de
    # produccion tiene 128 MB sin maxmemory: los topes evitan que los archivos lo comprometan.
    reportes_cache_activa: bool = True
    reportes_cache_ttl_s: int = 24 * 60 * 60
    reportes_cache_max_archivo: int = 3 * 1024 * 1024  # un archivo mayor no se cachea (queda en disco temporal)
    reportes_cache_por_usuario: int = 5  # reportes cacheados por usuario; el mas antiguo se descarta
    reportes_cache_redis_max_usado: int = 80 * 1024 * 1024  # si Redis ya usa mas, no se escribe

    # RabbitMQ
    # NOTA: mismo criterio que database_url -- placeholder, no produccion.
    rabbitmq_url: str = (
        "amqp://usuario:password@localhost:5672/"
    )

    # Brevo
    brevo_api_key: str
    brevo_sender_email: str = "juan.tellez@upb.edu.co"
    brevo_sender_name: str = "Techo 360"
    admin_alert_email: str = "juan.tellez@upb.edu.co"

    # Fase C (estados de los mensajes del chatbot): interruptor del BLOQUEO.
    # False (por defecto) = comportamiento de siempre (el mensaje del usuario se
    # guarda 'respondido', sin estados ni 409). True = maquina de estados
    # completa: pendiente -> procesando -> respondido/error, y una conversacion
    # con un mensaje sin resolver no admite otro (409). Se activa con
    # CHATBOT_BLOQUEO_PENDIENTES=true en el .env del VPS DESPUES de aplicar la
    # migracion 0002 y de desplegar el frontend que muestra los estados.
    chatbot_bloqueo_pendientes: bool = False

    # Aviso de cancelacion al asistente: al cancelar un mensaje que se estaba procesando,
    # el backend publica un aviso (mensaje_id + intento) en el exchange fanout
    # `chatbot_cancelaciones` para que el worker que ya lo tiene deje de generar. Es
    # opcional y de mejor esfuerzo: un chatbot/worker con imagen vieja lo ignora (sigue
    # generando como siempre) y un fallo al publicar solo se registra. False = apagado
    # (CHATBOT_AVISAR_CANCELACION=false en el .env), sin tocar nada mas.
    chatbot_avisar_cancelacion: bool = True

    # Admision de mensajes al asistente (solo con CHATBOT_BLOQUEO_PENDIENTES=true: usan los estados).
    # Contados en la BD como mensajes de usuario en 'pendiente' o 'procesando' (los que de verdad
    # esperan al asistente; 'error' y 'cancelado' no cuentan).
    #  - por usuario: sin esto una cuenta abre N conversaciones y encola un mensaje en cada una.
    #  - cola global: el asistente atiende pocos a la vez (2 consumidores, prefetch 1, ~20-45 s
    #    cada uno) y el RPC del backend se rinde a los 200 s: con mas de ~10 esperando, los ultimos
    #    ya no llegarian a tiempo. Se rechaza al entrar (503) en vez de encolar para fallar despues.
    # 0 = sin limite. Se cambian con CHATBOT_MAX_EN_PROCESO_POR_USUARIO / CHATBOT_MAX_COLA en el .env.
    chatbot_max_en_proceso_por_usuario: int = 2
    chatbot_max_cola: int = 10

    # Fase 4 (orquestador): interruptor del pipeline v2 (resolutor de entidades, contexto de datos, contrato RPC v2,
    # validador de cifras, sanitizacion y metadatos de la migracion 0004). False (por defecto) = el comportamiento de
    # siempre (contrato v1). Orden de despliegue (fase2-2 seccion 6): primero actualizar TODOS los consumers del chatbot
    # (VPS y workers) a la version que acepta v1 y v2, despues activar con CHATBOT_ORQUESTADOR_ACTIVO=true y recrear
    # el backend. Reversa: apagarlo y recrear el backend.
    chatbot_orquestador_activo: bool = False

    # Fase 6 (RAG curado, 6.6): con el orquestador activo, las preguntas CONCEPTUALES (definicion, metodo, relacion;
    # nunca las de datos) añaden hasta 2 fragmentos del corpus aprobado (rag_fragmentos, migracion 0005) al contexto.
    # False (por defecto) = sin RAG. Requiere la 0005 aplicada (6.8, [AUT]); si las tablas faltan o la consulta
    # falla, la respuesta sigue sin fragmentos. Reversa: apagarlo y recrear el backend.
    chatbot_rag_activo: bool = False

    # /otp/request: tope por IP en la ventana de 15 min y por dia (ver services/otp_service.py). Variables
    # OTP_IP_RATE_LIMIT_MAX (1..100) y OTP_IP_DAILY_MAX (1..500); vacio, no numerico, <= 0 o mayor que el
    # maximo -> el valor por defecto (10 y 150).
    otp_ip_rate_limit_max: int = OTP_IP_RATE_LIMIT_MAX_DEFECTO
    otp_ip_daily_max: int = OTP_IP_DAILY_MAX_DEFECTO

    # Modo local (DESACTIVAR_TAREAS_DE_FONDO=1 en el .env): el evento startup no arranca NADA de
    # lo que toca recursos compartidos -- scheduler de ingesta, ALTER TABLE de reportes, consumers
    # de RabbitMQ (persistencia del chatbot y reportes), barrido de mensajes huerfanos ni el
    # precalentamiento de caches. Sirve para correr la API en una maquina de desarrollo apuntando
    # a servicios reales sin competir con el backend de produccion. Sin la variable (False) todo
    # arranca como siempre. En produccion NO debe definirse.
    desactivar_tareas_de_fondo: bool = False

    @field_validator("otp_ip_rate_limit_max", mode="before")
    @classmethod
    def _otp_ip_rate_limit_max_valido(cls, valor):
        return entero_positivo_o_defecto(valor, OTP_IP_RATE_LIMIT_MAX_DEFECTO, OTP_IP_RATE_LIMIT_MAX_MAXIMO)

    @field_validator("otp_ip_daily_max", mode="before")
    @classmethod
    def _otp_ip_daily_max_valido(cls, valor):
        return entero_positivo_o_defecto(valor, OTP_IP_DAILY_MAX_DEFECTO, OTP_IP_DAILY_MAX_MAXIMO)

    # JWT
    jwt_secret_key: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()