from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # Base de datos utilizada actualmente por SQLAlchemy
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # RabbitMQ
    rabbitmq_url: str

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