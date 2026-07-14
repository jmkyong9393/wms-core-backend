from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "B2B WMS Platform API"
    DATABASE_URL: str = "postgresql://admin:password@localhost:5432/wms_db"

    CHROMA_SERVER_HOST: str = "localhost"
    CHROMA_SERVER_PORT: int = 8001

    OPENAI_API_KEY: str = ""
    # AWS, Supabase or other config can be added here
    
    # JWT 설정
    JWT_SECRET_KEY: str = "local-development-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30 #현재는 30분 설정

    # 가입 제한 코드
    WORKER_SIGNUP_CODE: str = "worker-local-code"
    MASTER_SIGNUP_CODE: str = "master-local-code"

    class Config:
        env_file = ".env"

settings = Settings()
