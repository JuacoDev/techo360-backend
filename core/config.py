from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://user:password@postgres:5432/techo360_db"
    redis_url: str = "redis://redis:6379/0"
    ai_service_url: str = "http://ai-service:8001"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
