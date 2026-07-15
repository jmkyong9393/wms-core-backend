from datetime import date, datetime
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.exceptions import (
    DuplicateEmailException,
    InactiveUserException,
    InvalidCredentialsException,
    InvalidCurrentPasswordException,
    SamePasswordException,
    LastActiveMasterException,
    SelfRoleChangeNotAllowedException,
    SelfStatusChangeNotAllowedException,
    UserNotFoundException,
)
from app.core.security import (
    generate_temporary_password,
    hash_password,
    verify_password,
)
from app.models.wms import User, UserStatus, UserRole
from app.schemas.auth import EmployeeCreateRequest


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


# 입사일 기준으로 직원 사번 자동 생성
# 형식 : AV + YYMMDD + 당일 순번 2자리 (예: AV26033105)
def generate_employee_id(
        session: Session,
        hire_date: date,
) -> str:
    date_part = hire_date.strftime("%y%m%d")
    prefix = f"AV{date_part}"

    # 같은 입사일로 발급된 기존 사번 조회
    employee_ids = session.exec(
        select(User.employee_id).where(
            User.employee_id.startswith(prefix)
        )
    ).all()

    sequences: list[int] = []

    for employee_id in employee_ids:
        suffix = employee_id[-2:]

        if suffix.isdigit():
            sequences.append(int(suffix))

    # 기존 번호 중 가장 큰 값 다음 번호 발급
    next_sequence = max(sequences, default=0) + 1 

    if next_sequence > 99: # 인원 수정 가능.
        raise ValueError(
            "해당 입사일의 사번 발급 가능 인원을 초과했습니다."
        )
    
    return f"{prefix}{next_sequence:02d}"



# 관리자가 직원 계정 생성
def create_employee(
        session: Session,
        request: EmployeeCreateRequest,
) -> tuple[User, str]:
    
    if request.email and get_user_by_email(session, str(request.email)):
        raise DuplicateEmailException()
    
    # 입사일을 기준으로 사번 자동 생성
    employee_id = generate_employee_id(
        session=session,
        hire_date=request.hire_date,
    )

    temporary_password = generate_temporary_password()

    user = User(
        employee_id=employee_id,
        email=str(request.email) if request.email else None,
        name=request.name,
        password_hash=hash_password(temporary_password),
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
        must_change_password=True,
    )

    session.add(user)
    session.commit()
    session.refresh(user)

    return user, temporary_password


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
        raise InvalidCredentialsException()

    if not verify_password(
        plain_password=password,
        hashed_password=user.password_hash,
    ):
        raise InvalidCredentialsException()

    if user.status != UserStatus.ACTIVE:
        raise InactiveUserException()

    
    return user

# 현재 비밀번호를 확인하고 새 비밀번호로 변경
def change_password(
    session: Session,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    if not verify_password(
        plain_password=current_password,
        hashed_password=user.password_hash,
    ):
        raise InvalidCurrentPasswordException()

    if verify_password(
        plain_password=new_password,
        hashed_password=user.password_hash,
    ):
        raise SamePasswordException()

    user.password_hash = hash_password(new_password)
    user.must_change_password = False

    session.add(user)
    session.commit()


# 활성 MASTER 계정 수 조회
def count_active_masters(session: Session) -> int:
    count = session.exec(
        select(func.count(User.id)).where(
            User.role == UserRole.MASTER,
            User.status == UserStatus.ACTIVE,
        )
    ).one()

    return int(count)


# 사용자 권한 변경
def update_user_role(
    session: Session,
    target_user_id,
    current_master_id,
    new_role: UserRole,
) -> User:
    target_user = session.get(User, target_user_id)

    if target_user is None:
        raise UserNotFoundException()

    if target_user.id == current_master_id:
        raise SelfRoleChangeNotAllowedException()

    is_active_master = (
        target_user.role == UserRole.MASTER
        and target_user.status == UserStatus.ACTIVE
    )

    if (
        is_active_master
        and new_role != UserRole.MASTER
        and count_active_masters(session) <= 1
    ):
        raise LastActiveMasterException()

    target_user.role = new_role
    target_user.updated_at = datetime.utcnow()

    session.add(target_user)
    session.commit()
    session.refresh(target_user)

    return target_user


# 사용자 계정 상태 변경
def update_user_status(
    session: Session,
    target_user_id,
    current_master_id,
    new_status: UserStatus,
) -> User:
    target_user = session.get(User, target_user_id)

    if target_user is None:
        raise UserNotFoundException()

    if target_user.id == current_master_id:
        raise SelfStatusChangeNotAllowedException()

    is_last_active_master = (
        target_user.role == UserRole.MASTER
        and target_user.status == UserStatus.ACTIVE
        and new_status == UserStatus.INACTIVE
        and count_active_masters(session) <= 1
    )

    if is_last_active_master:
        raise LastActiveMasterException()

    target_user.status = new_status
    target_user.updated_at = datetime.utcnow()

    session.add(target_user)
    session.commit()
    session.refresh(target_user)

    return target_user