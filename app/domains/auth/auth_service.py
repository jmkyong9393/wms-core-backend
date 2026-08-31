from collections import defaultdict
from datetime import date, datetime, timedelta
import unicodedata
from uuid import UUID

from sqlalchemy import func, or_, text
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
from app.core.config import settings
from app.core.security import (
    generate_refresh_token,
    generate_temporary_password,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.wms import User, UserRole, UserStatus
from app.domains.auth.schemas.auth import (
    EmployeeBulkCreateResultRow,
    EmployeeBulkCreateRow,
    EmployeeCreateRequest,
    EmployeeListItemResponse,
    EmployeeListResponse,
)

EMPLOYEE_ID_PREFIX = "NZ"
EMPLOYEE_ID_SEQUENCE_WIDTH = 3
MAX_MONTHLY_EMPLOYEE_SEQUENCE = 999


# 사번으로 사용자를 조회하는 함수
def get_user_by_employee_id(
    session: Session,
    employee_id: str,
) -> User | None:
    statement = select(User).where(
        User.employee_id == employee_id
    )
    return session.exec(statement).first()


# 이메일로 사용자를 조회하는 함수
def get_user_by_email(
    session: Session,
    email: str,
) -> User | None:
    statement = select(User).where(
        User.email == email
    )
    return session.exec(statement).first()

def _apply_employee_list_filters(
    statement,
    tenant_id: UUID,
    keyword: str | None = None,
    role: UserRole | None = None,
    status: UserStatus | None = None,
):
    """
    직원 목록과 전체 건수 조회에 공통으로 적용할 조건을 구성한다.

    목록 조회와 COUNT 조회의 조건이 다르면 페이지 수와 실제 목록이
    달라질 수 있으므로, 테넌트·검색·역할·상태 필터를 한 곳에서 관리한다.
    """
    statement = statement.where(
        User.tenant_id == tenant_id,
    )

    normalized_keyword = (keyword or "").strip()

    if normalized_keyword:
        search_pattern = f"%{normalized_keyword}%"

        statement = statement.where(
            or_(
                User.employee_id.ilike(search_pattern),
                User.name.ilike(search_pattern),
                User.email.ilike(search_pattern),
            )
        )

    if role is not None:
        statement = statement.where(
            User.role == role,
        )

    if status is not None:
        statement = statement.where(
            User.status == status,
        )

    return statement


def list_employees(
    session: Session,
    tenant_id: UUID,
    keyword: str | None = None,
    role: UserRole | None = None,
    status: UserStatus | None = None,
    page: int = 1,
    size: int = 20,
) -> EmployeeListResponse:
    """
    현재 테넌트의 직원 계정을 서버 페이지네이션 방식으로 조회한다.

    반환 행의 id는 권한·상태 변경 API 경로에 사용하는 User UUID다.
    비밀번호나 Refresh Token 관련 민감 정보는 반환하지 않는다.
    """
    count_statement = _apply_employee_list_filters(
        statement=select(func.count()).select_from(User),
        tenant_id=tenant_id,
        keyword=keyword,
        role=role,
        status=status,
    )
    total = session.exec(count_statement).one()

    offset = (page - 1) * size

    list_statement = _apply_employee_list_filters(
        statement=select(User),
        tenant_id=tenant_id,
        keyword=keyword,
        role=role,
        status=status,
    )

    users = session.exec(
        list_statement
        .order_by(
            User.created_at.desc(),
            User.id.desc(),
        )
        .offset(offset)
        .limit(size)
    ).all()

    items = [
        EmployeeListItemResponse(
            id=user.id,
            employee_id=user.employee_id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            must_change_password=user.must_change_password,
            created_at=user.created_at,
        )
        for user in users
    ]

    total_pages = (
        (total + size - 1) // size
        if total > 0
        else 0
    )

    return EmployeeListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        total_pages=total_pages,
    )

# 사용자 조회 중복 제거
def get_user_or_raise(
        session: Session,
        user_id: UUID,
) -> User:
    user = session.get(User, user_id)

    if user is None:
        raise UserNotFoundException()
    
    return user

# Tenant 기준 사용자 조회 함수
def get_tenant_user_or_raise(
    session: Session,
    user_id: UUID,
    tenant_id: UUID,
) -> User:
    statement = select(User).where(
        User.id == user_id,
        User.tenant_id == tenant_id,
    )

    user = session.exec(statement).first()

    if user is None:
        raise UserNotFoundException()

    return user

# 저장 코드 통일
def save_user(
    session: Session,
    user: User,
) -> None:
    user.updated_at = datetime.utcnow()

    session.add(user)
    session.commit()
    session.refresh(user)


def build_employee_id_prefix(hire_date: date) -> str:
    """
    입사 월 기준 사번 접두어를 생성한다.

    예: 2026-08-01 -> NZ2608
    """
    return (
        f"{EMPLOYEE_ID_PREFIX}"
        f"{hire_date.strftime('%y%m')}"
    )


def extract_employee_sequence(
    employee_id: str,
    prefix: str,
) -> int | None:
    """
    현재 월별 사번 규격과 일치하는 순번만 반환한다.

    과거 일 단위 사번(예: NZ26080101)은 길이가 달라
    월 단위 신규 사번 순번 계산에서 제외한다.
    """
    suffix = employee_id.removeprefix(prefix)

    if (
        len(suffix) != EMPLOYEE_ID_SEQUENCE_WIDTH
        or not suffix.isdigit()
    ):
        return None

    return int(suffix)


def generate_employee_id(
    session: Session,
    hire_date: date,
) -> str:
    """
    입사 월 기준 사번을 생성한다.

    형식: NZ + YYMM + 월별 순번 3자리
    예: NZ2608001
    """
    prefix = build_employee_id_prefix(hire_date)

    employee_ids = session.exec(
        select(User.employee_id).where(
            User.employee_id.startswith(prefix)
        )
    ).all()

    issued_sequences = [
        sequence
        for employee_id in employee_ids
        if (
            sequence := extract_employee_sequence(
                employee_id=employee_id,
                prefix=prefix,
            )
        ) is not None
    ]

    next_sequence = max(
        issued_sequences,
        default=0,
    ) + 1

    if next_sequence > MAX_MONTHLY_EMPLOYEE_SEQUENCE:
        raise ValueError(
            "해당 입사 월의 사번 발급 가능 인원(999명)을 초과했습니다."
        )

    return (
        f"{prefix}"
        f"{next_sequence:0{EMPLOYEE_ID_SEQUENCE_WIDTH}d}"
    )


# 관리자가 직원 계정 생성
def create_employee(
    session: Session,
    request: EmployeeCreateRequest,
    current_master: User,
) -> tuple[User, str]:
    email = (
        str(request.email)
        if request.email
        else None
    )

    if email and get_user_by_email(session, email):
        raise DuplicateEmailException()
    
    # 입사일을 기준으로 사번 자동 생성
    employee_id = generate_employee_id(
        session=session,
        hire_date=request.hire_date,
    )

    temporary_password = generate_temporary_password()

    user = User(
        tenant_id=current_master.tenant_id,
        employee_id=employee_id,
        email=email,
        name=request.name,
        password_hash=hash_password(temporary_password),
        role=request.role,
        status=UserStatus.ACTIVE,
        must_change_password=True,
    )

    session.add(user)
    session.commit()
    session.refresh(user)

    return user, temporary_password

def _lock_employee_id_month(
    session: Session,
    prefix: str,
) -> None:
    """
    같은 입사 월의 사번을 동시에 발급할 때 순번이 겹치지 않도록 잠근다.

    employee_id는 테넌트와 무관하게 전역 unique이므로
    월별 잠금도 전역 기준으로 사용한다.
    """
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:lock_key, 0)"
            ")"
        ).bindparams(
            lock_key=f"employee-id-month:{prefix}"
        )
    )


def _normalize_employee_name_for_sort(
    name: str,
) -> str:
    """
    동일 입사일 직원의 사번 순번을 안정적으로 정하기 위한 이름 정렬값.

    한글 이름은 유니코드 정규화 후 정렬하면 가나다 순서와 일관되게 처리된다.
    """
    return unicodedata.normalize(
        "NFC",
        name,
    ).casefold()


def _normalize_optional_email(
    email: object | None,
) -> str | None:
    if email is None:
        return None

    return str(email).strip().lower() or None


def _get_issued_employee_sequences(
    session: Session,
    prefix: str,
) -> set[int]:
    employee_ids = session.exec(
        select(User.employee_id).where(
            User.employee_id.startswith(prefix)
        )
    ).all()

    return {
        sequence
        for employee_id in employee_ids
        if (
            sequence := extract_employee_sequence(
                employee_id=employee_id,
                prefix=prefix,
            )
        ) is not None
    }


def create_employees_bulk(
    session: Session,
    rows: list[EmployeeBulkCreateRow],
    current_master: User,
) -> list[EmployeeBulkCreateResultRow]:
    """
    검증된 직원 행을 한 번에 생성한다.

    - 입사일 → 이름 → 원본 엑셀 행 순서로 사번 순번을 결정한다.
    - 같은 월의 동시 발급은 Advisory Lock으로 보호한다.
    - 한 행이라도 실패하면 전체를 롤백한다.
    - 반환값의 순서는 사번 배정 순서와 무관하게 원본 엑셀 행 순서를 유지한다.
    """
    if not rows:
        raise ValueError("생성할 직원 정보가 없습니다.")

    normalized_email_by_row = {
        row.source_row: _normalize_optional_email(row.email)
        for row in rows
    }

    email_rows: dict[str, list[int]] = defaultdict(list)

    for row in rows:
        email = normalized_email_by_row[row.source_row]
        if email is not None:
            email_rows[email].append(row.source_row)

    duplicated_emails = {
        email: source_rows
        for email, source_rows in email_rows.items()
        if len(source_rows) > 1
    }

    if duplicated_emails:
        details = ", ".join(
            (
                f"{email} "
                f"(행: {', '.join(map(str, source_rows))})"
            )
            for email, source_rows in duplicated_emails.items()
        )
        raise ValueError(
            f"엑셀 파일 내 이메일이 중복되었습니다: {details}"
        )

    emails = list(email_rows)

    if emails:
        existing_emails = session.exec(
            select(User.email).where(
                func.lower(User.email).in_(emails)
            )
        ).all()

        if existing_emails:
            raise ValueError(
                "이미 등록된 이메일이 포함되어 있습니다: "
                + ", ".join(
                    sorted(
                        str(email)
                        for email in existing_emails
                        if email is not None
                    )
                )
            )

    rows_by_prefix: dict[
        str,
        list[EmployeeBulkCreateRow],
    ] = defaultdict(list)

    for row in rows:
        prefix = build_employee_id_prefix(
            row.hire_date
        )
        rows_by_prefix[prefix].append(row)

    assigned_employee_id_by_row: dict[int, str] = {}

    try:
        # 여러 월이 섞여도 항상 접두어 오름차순으로 잠가 교착 상태를 방지한다.
        for prefix in sorted(rows_by_prefix):
            _lock_employee_id_month(
                session=session,
                prefix=prefix,
            )

            issued_sequences = (
                _get_issued_employee_sequences(
                    session=session,
                    prefix=prefix,
                )
            )

            sorted_rows = sorted(
                rows_by_prefix[prefix],
                key=lambda row: (
                    row.hire_date,
                    _normalize_employee_name_for_sort(
                        row.name
                    ),
                    row.source_row,
                ),
            )

            last_sequence = max(
                issued_sequences,
                default=0,
            )

            if (
                last_sequence + len(sorted_rows)
                > MAX_MONTHLY_EMPLOYEE_SEQUENCE
            ):
                raise ValueError(
                    f"{prefix} 사번의 월별 발급 가능 "
                    "인원(999명)을 초과했습니다."
                )

            for offset, row in enumerate(
                sorted_rows,
                start=1,
            ):
                sequence = last_sequence + offset

                assigned_employee_id_by_row[
                    row.source_row
                ] = (
                    f"{prefix}"
                    f"{sequence:0{EMPLOYEE_ID_SEQUENCE_WIDTH}d}"
                )

        result_by_source_row: dict[
            int,
            EmployeeBulkCreateResultRow,
        ] = {}

        for row in rows:
            temporary_password = (
                generate_temporary_password()
            )

            user = User(
                tenant_id=current_master.tenant_id,
                employee_id=assigned_employee_id_by_row[
                    row.source_row
                ],
                email=normalized_email_by_row[
                    row.source_row
                ],
                name=row.name,
                password_hash=hash_password(
                    temporary_password
                ),
                role=row.role,
                status=UserStatus.ACTIVE,
                must_change_password=True,
            )
            session.add(user)

            result_by_source_row[row.source_row] = (
                EmployeeBulkCreateResultRow(
                    source_row=row.source_row,
                    name=row.name,
                    hire_date=row.hire_date,
                    role=row.role,
                    email=normalized_email_by_row[
                        row.source_row
                    ],
                    employee_id=user.employee_id,
                    temporary_password=temporary_password,
                )
            )

        session.commit()

    except Exception:
        session.rollback()
        raise

    return [
        result_by_source_row[row.source_row]
        for row in sorted(
            rows,
            key=lambda row: row.source_row,
        )
    ]

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

# 로그인 성공 시 단일 활성 Refresh Token 세션을 새로 만든다.
# 새 로그인은 기존 브라우저/기기의 Access Token과 Refresh Token을 무효화한다.
def create_refresh_session_for_login(
    user: User,
) -> str:
    refresh_token = generate_refresh_token()

    # 새 로그인 시 인증 버전을 증가시켜 기존 Access Token을 즉시 무효화한다.
    user.auth_version += 1
    user.refresh_token_hash = hash_refresh_token(
        refresh_token
    )
    user.refresh_token_expires_at = (
        datetime.utcnow()
        + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    )
    user.updated_at = datetime.utcnow()

    return refresh_token


# Refresh Token으로 인증된 사용자의 세션을 회전한다.
# 기존 Refresh Token은 즉시 사용할 수 없게 되고 새 Token으로 교체된다.
def rotate_refresh_session(
    user: User,
) -> str:
    refresh_token = generate_refresh_token()

    user.refresh_token_hash = hash_refresh_token(
        refresh_token
    )
    user.refresh_token_expires_at = (
        datetime.utcnow()
        + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    )
    user.updated_at = datetime.utcnow()

    return refresh_token


# Cookie로 전달받은 Refresh Token에 연결된 활성 사용자를 조회한다.
# 원본 Token은 저장하지 않고 해시값으로만 비교한다.
def get_user_by_refresh_token(
    session: Session,
    refresh_token: str,
) -> User | None:
    token_hash = hash_refresh_token(refresh_token)

    user = session.exec(
        select(User)
        .where(
            User.refresh_token_hash == token_hash
        )
        .with_for_update()
    ).first()

    if user is None:
        return None

    if user.refresh_token_expires_at is None:
        return None

    if user.refresh_token_expires_at <= datetime.utcnow():
        return None

    return user


# 현재 활성 Refresh Token을 제거하고 모든 기존 Access Token도 즉시 무효화한다.
def revoke_refresh_session(
    user: User,
) -> None:
    user.refresh_token_hash = None
    user.refresh_token_expires_at = None
    user.auth_version += 1
    user.updated_at = datetime.utcnow()

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

    # 비밀번호 변경 후 기존 로그인 세션을 모두 무효화
    revoke_refresh_session(user)

    save_user(
        session=session,
        user=user,
    )


# 활성 MASTER 계정 수 조회
def count_active_masters(
    session: Session,
    tenant_id: UUID,
) -> int:
    count = session.exec(
        select(func.count(User.id)).where(
            User.tenant_id == tenant_id,
            User.role == UserRole.MASTER,
            User.status == UserStatus.ACTIVE,
        )
    ).one()

    return int(count)


# 사용자 권한 변경
def update_user_role(
    session: Session,
    target_user_id: UUID,
    current_master: User,
    new_role: UserRole,
) -> User:
    target_user = get_tenant_user_or_raise(
        session=session,
        user_id=target_user_id,
        tenant_id=current_master.tenant_id,
    )

    if target_user.id == current_master.id:
        raise SelfRoleChangeNotAllowedException()

    is_active_master = (
        target_user.role == UserRole.MASTER
        and target_user.status == UserStatus.ACTIVE
    )

    is_last_active_master = (
        is_active_master
        and new_role != UserRole.MASTER
        and count_active_masters(
            session=session,
            tenant_id=current_master.tenant_id,
        ) <= 1
    )

    if is_last_active_master:
        raise LastActiveMasterException()

    target_user.role = new_role

    save_user(
        session=session,
        user=target_user,
    )

    return target_user


# 사용자 계정 상태 변경
def update_user_status(
    session: Session,
    target_user_id: UUID,
    current_master: User,
    new_status: UserStatus,
) -> User:
    target_user = get_tenant_user_or_raise(
        session=session,
        user_id=target_user_id,
        tenant_id=current_master.tenant_id,
    )

    if target_user.id == current_master.id:
        raise SelfStatusChangeNotAllowedException()

    is_last_active_master = (
        target_user.role == UserRole.MASTER
        and target_user.status == UserStatus.ACTIVE
        and new_status == UserStatus.INACTIVE
        and count_active_masters(
            session=session,
            tenant_id=current_master.tenant_id,
        ) <= 1
    )

    if is_last_active_master:
        raise LastActiveMasterException()

    target_user.status = new_status

    save_user(
        session=session,
        user=target_user,
    )

    return target_user