from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://user:password@postgres:5432/techo360_db"
    database_users_url: str
    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str
    brevo_api_key: str
    brevo_sender_email: str = "juan.tellez@upb.edu.co"
    brevo_sender_name: str = "Techo 360"
    jwt_secret_key: str

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
