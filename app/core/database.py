from sqlmodel import SQLModel, Session, create_engine, select

from app.core.config import settings
from app.core.security import hash_password
from app.models import wms
from app.models.wms import User, UserRole, UserStatus


engine = create_engine(settings.DATABASE_URL, echo=True)

# 최초 MASTER 계정이 없을 때 한 번만 생성하는 함수
def create_initial_master() -> None:

    with Session(engine) as session:
        existing_user = session.exec(
            select(User).where(
                User.employee_id == settings.INITIAL_MASTER_EMPLOYEE_ID
            )
        ).first()

        if existing_user is not None:
            return

        master = User(
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

def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    create_initial_master()

def get_session():
    with Session(engine) as session:
        yield session
