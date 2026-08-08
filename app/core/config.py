from uuid import UUID
from typing import Literal
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

    # ISBN 기반 도서 메타데이터 조회용 알라딘 OpenAPI 설정
    ALADIN_TTB_KEY: str = ""
    ALADIN_API_BASE_URL: str = "https://www.aladin.co.kr/ttb/api"
    ALADIN_REQUEST_TIMEOUT_SECONDS: float = 5.0

    # 자동 발주 추천 Agent에서 사용할 임시 OpenAI 모델 설정
    # TODO:
    # 팀에서 사용할 모델과 비용 정책이 확정되면 모델명을 변경한다.
    RESTOCK_AGENT_MODEL: str = "gpt-4o-mini"

    # TODO:
    # 발주 추천 결과의 일관성을 위해 낮게 설정한 임시값이다.
    # 실제 Agent 품질 테스트 후 조정한다.
    RESTOCK_AGENT_TEMPERATURE: float = 0.1

    RESTOCK_AGENT_TIMEOUT_SECONDS: float = 30.0

    # 안전재고 부족 AUTO_PO 배치가 생성할 관리자 추천안의 테넌트
    AUTO_PO_TENANT_ID: UUID | None = None
    
    # AWS, Supabase or other config can be added here
    
    # JWT 설정
    JWT_SECRET_KEY: str = "local-development-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30 #현재는 30분 설정

    # Refresh Token 설정
    # 개발 환경에서는 HTTP 접속을 위해 False,
    # 운영 HTTPS 환경에서는 반드시 True로 설정한다.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    REFRESH_TOKEN_COOKIE_NAME: str = "refresh_token"
    REFRESH_TOKEN_COOKIE_SECURE: bool = False
    REFRESH_TOKEN_COOKIE_SAMESITE: Literal[
        "lax",
        "strict",
        "none",
    ] = "lax"

    # 네트워크 라벨 프린터 설정
    # 개발·테스트 환경에서는 False로 두어 프린터 연결 실패가
    # 입고·검수 DB 처리 실패로 이어지지 않게 한다.
    LABEL_PRINTER_ENABLED: bool = False

    # Xprinter XP-423B LAN Raw TCP 설정.
    # 실제 장비의 고정 IP는 .env에서 주입한다.
    LABEL_PRINTER_HOST: str = ""
    LABEL_PRINTER_PORT: int = 9100
    LABEL_PRINTER_TIMEOUT_SECONDS: float = 5.0

    # XP-423B: 203 DPI(약 8 dots/mm), 라벨 50mm × 30mm
    LABEL_PRINTER_DPI: int = 203
    LABEL_PRINTER_LABEL_WIDTH_MM: int = 50
    LABEL_PRINTER_LABEL_HEIGHT_MM: int = 30

    # ZPL 문자열 전송 인코딩.
    # 실제 프린터 한글 설정에 따라 utf-8 또는 euc-kr로 조정 가능하다.
    LABEL_PRINTER_ENCODING: str = "utf-8"

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
        extra = "ignore"

settings = Settings()
