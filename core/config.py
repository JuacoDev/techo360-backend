from pydantic_settings import BaseSettings, SettingsConfigDict

# Duracion del token de acceso (JWT). Valor UNICO: lo usan routers/auth.py y
# services/auth_service.py. Antes era 1 h (duplicado en ambos archivos) y la
# sesion se cerraba aunque el usuario estuviera activo: no hay refresh token.
# Los tokens ya emitidos conservan su caducidad original; solo los nuevos
# duran esto.
ACCESS_TOKEN_EXPIRE_HOURS = 8


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

    # Modo local (DESACTIVAR_TAREAS_DE_FONDO=1 en el .env): el evento startup no arranca NADA de
    # lo que toca recursos compartidos -- scheduler de ingesta, ALTER TABLE de reportes, consumers
    # de RabbitMQ (persistencia del chatbot y reportes), barrido de mensajes huerfanos ni el
    # precalentamiento de caches. Sirve para correr la API en una maquina de desarrollo apuntando
    # a servicios reales sin competir con el backend de produccion. Sin la variable (False) todo
    # arranca como siempre. En produccion NO debe definirse.
    desactivar_tareas_de_fondo: bool = False

    # JWT
    jwt_secret_key: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()