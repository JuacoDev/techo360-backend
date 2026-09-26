from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # JWT
    jwt_secret_key: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()