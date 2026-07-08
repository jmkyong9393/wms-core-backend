from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "B2B WMS Platform API"
    DATABASE_URL: str = "postgresql://admin:password@localhost:5432/wms_db"
    CHROMA_SERVER_HOST: str = "localhost"
    CHROMA_SERVER_PORT: int = 8001
    # AWS, Supabase or other config can be added here
    
    class Config:
        env_file = ".env"

settings = Settings()
