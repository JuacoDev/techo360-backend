from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # Base de datos utilizada actualmente por SQLAlchemy
    # NOTA: valor por defecto es un placeholder de desarrollo local, NO
    # apunta a produccion. Definir DATABASE_URL en .env para la conexion
    # real (nunca comitear la URL real aqui).
    database_url: str = (
        "postgresql://usuario:password@localhost:5432/techo360_principal"
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