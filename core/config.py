from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # Base de datos utilizada actualmente por SQLAlchemy
    database_url: str = (
        "postgresql://techo360_backend:5839147261"
        "@localhost:5432/techo360_principal"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # RabbitMQ
    rabbitmq_url: str = (
        "amqp://techo360:password@localhost:5672/"
    )

    # Brevo
    brevo_api_key: str
    brevo_sender_email: str = "juan.tellez@upb.edu.co"
    brevo_sender_name: str = "Techo 360"

    # JWT
    jwt_secret_key: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()
