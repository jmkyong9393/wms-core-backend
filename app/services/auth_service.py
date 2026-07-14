from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.wms import User, UserRole,UserStatus
from app.schemas.auth import SignupRequest, SignupRole

# 사용자가 선택한 role과 입력한 가입 제한 코드가 일치하는지 확인하는 함수
def validate_signup_code(
        role: SignupRole,
        security_code: str,
) -> bool:
    
    role_code_map = {
        SignupRole.WORKER: settings.WORKER_SIGNUP_CODE,
        SignupRole.MASTER: settings.MASTER_SIGNUP_CODE,
    }

    expected_code = role_code_map.get(role)

    return expected_code == security_code

# 사번으로 사용자를 조회하는 함수 (같은 사번이 이미 존재하는지 확인할 때 사용)
def get_user_by_employee_id(
        session: Session,
        employee_id: str,
) -> User | None:
    return session.exec(
        select(User).where(User.employee_id == employee_id)
    ).first()

# 이메일로 사용자를 조회하는 함수 (같은 이메일이 이미 존재하는지 확인할 때 사용)
def get_user_by_email(
        session: Session,
        email: str,
) -> User | None:
    return session.exec(
        select(User).where(User.email == email)
    ).first()

# 회원가입 요청을 바탕으로 사용자를 생성하고 DB에 저장
def create_user(
        session: Session,
        request: SignupRequest,
) -> User:
    user = User(
        employee_id = request.employee_id,
        email = request.email,
        name = request.name,
        password_hash = hash_password(request.password),
        role = UserRole(request.role.value),
        status = UserStatus.ACTIVE,
    )

    session.add(user)
    session.commit()
    session.refresh(user)

    return user


# 사번과 비밀번호 검증하는 함수 (로그인 시 사용)
# 실패 : 사용자가 없거나 비밀번호가 일치하지 않으면 None 반환
# 성공 : User 객체 반환
def authenticate_user(
        session: Session,
        employee_id: str,
        password: str,
) -> User | None:
    user = get_user_by_employee_id(
        session = session,
        employee_id = employee_id,
    )

    if user is None:
        return None
    
    if not verify_password(
        plain_password=password,
        hashed_password=user.password_hash,
    ):
        return None
    
    return user
