from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "B2B WMS Platform API"
    DATABASE_URL: str = "postgresql://admin:password@localhost:5432/wms_db"
    PUBLIC_WEB_BASE_URL: str = "http://localhost:3000"

    # 검수 이미지 조회에 허용할 CloudFront 배포 도메인과 객체 경로
    CLOUDFRONT_IMAGE_BASE_URL: str = (
        "https://d3j61tpuly7r0p.cloudfront.net"
    )
    CLOUDFRONT_IMAGE_PATH_PREFIX: str = "/uploads/"

    CHROMA_SERVER_HOST: str = "localhost"
    CHROMA_SERVER_PORT: int = 8001

    OPENAI_API_KEY: str = ""

    # 자동 발주 추천 Agent에서 사용할 임시 OpenAI 모델 설정
    # TODO:
    # 팀에서 사용할 모델과 비용 정책이 확정되면 모델명을 변경한다.
    RESTOCK_AGENT_MODEL: str = "gpt-4o-mini"

    # TODO:
    # 발주 추천 결과의 일관성을 위해 낮게 설정한 임시값이다.
    # 실제 Agent 품질 테스트 후 조정한다.
    RESTOCK_AGENT_TEMPERATURE: float = 0.1
    
    # AWS, Supabase or other config can be added here
    
    # JWT 설정
    JWT_SECRET_KEY: str = "local-development-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30 #현재는 30분 설정

    # SSE 티켓 설정
    REDIS_URL: str = "redis://localhost:6379/0"
    SSE_TICKET_EXPIRE_SECONDS: int = 300
    SSE_RETRY_MILLISECONDS: int = 3000
    SSE_HEARTBEAT_SECONDS: int = 15

    # 최초 MASTER 계정 설정
    INITIAL_MASTER_EMPLOYEE_ID: str
    INITIAL_MASTER_NAME: str
    INITIAL_MASTER_EMAIL: str | None = None
    INITIAL_MASTER_PASSWORD: str

    # 기본 정책 : 최대 1000건, 마지막 실패 적재 후 14일 보관.
    INSPECTION_DLQ_MAX_ENTRIES: int = 1000
    INSPECTION_DLQ_TTL_SECONDS: int = 60 * 60 * 24 * 14

    # WMS API 요청 제한 시간
    WMS_REQUEST_TIMEOUT_SECONDS: float = 10.0
    class Config:
        env_file = ".env"

settings = Settings()
