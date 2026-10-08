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

    # JWT
    jwt_secret_key: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()