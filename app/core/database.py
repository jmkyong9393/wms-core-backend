from sqlmodel import Session, create_engine, select

from app.core.config import settings
from app.core.security import hash_password
from app.models.wms import (
    Tenant,
    User,
    UserRole,
    UserStatus,
)


engine = create_engine(settings.DATABASE_URL, echo=True)

DEFAULT_TENANT_CODE = "AIVLE_WMS"
DEFAULT_TENANT_NAME = "AIVLE WMS"

# 기본 Tenant 조회 또는 생성
def get_or_create_default_tenant(
    session: Session,
) -> Tenant:
    tenant = session.exec(
        select(Tenant).where(
            Tenant.code == DEFAULT_TENANT_CODE
        )
    ).first()

    if tenant is not None:
        return tenant

    tenant = Tenant(
        code=DEFAULT_TENANT_CODE,
        name=DEFAULT_TENANT_NAME,
        is_active=True,
    )

    session.add(tenant)

    # commit 전 tenant.id를 확보하고,
    # 같은 트랜잭션에서 MASTER의 FK로 사용
    session.flush()

    return tenant

# 최초 MASTER 계정이 없을 때 한 번만 생성하는 함수
def create_initial_master() -> None:
    with Session(engine) as session:
        default_tenant = get_or_create_default_tenant(
            session=session,
        )

        existing_user = session.exec(
            select(User).where(
                User.employee_id
                == settings.INITIAL_MASTER_EMPLOYEE_ID
            )
        ).first()

        if existing_user is not None:
            # Tenant만 새로 생성된 경우에도 저장되도록 commit
            session.commit()
            return

        master = User(
            tenant_id=default_tenant.id,
            employee_id=settings.INITIAL_MASTER_EMPLOYEE_ID,
            name=settings.INITIAL_MASTER_NAME,
            email=settings.INITIAL_MASTER_EMAIL,
            password_hash=hash_password(
                settings.INITIAL_MASTER_PASSWORD
            ),
            role=UserRole.MASTER,
            status=UserStatus.ACTIVE,
            must_change_password=False,
        )

        session.add(master)
        session.commit()


def initialize_application_data() -> None:
    create_initial_master()


def get_session():
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
