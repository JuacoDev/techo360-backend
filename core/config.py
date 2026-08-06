from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://user:password@postgres:5432/techo360_db"
    database_users_url: str
    redis_url: str = "redis://redis:6379/0"
    rabbitmq_url: str

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
