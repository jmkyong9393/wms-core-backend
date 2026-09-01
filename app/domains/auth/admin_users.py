from datetime import datetime
from io import BytesIO
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.api.dependencies.auth import require_master
from app.core.database import get_session
from app.domains.auth.auth_service import (
    create_employee,
    create_employees_bulk,
    list_employees,
    update_user_role,
    update_user_status,
)
from app.domains.auth.employee_bulk_excel_service import (
    EmployeeBulkExcelValidationError,
    build_employee_bulk_result_xlsx,
    build_employee_bulk_template_xlsx,
    parse_employee_bulk_create_xlsx,
)
from app.domains.auth.schemas.auth import (
    EmployeeCreateRequest,
    EmployeeCreateResponse,
    EmployeeListResponse,
    UserResponse,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
)
from app.models.wms import (
    User,
    UserRole,
    UserStatus,
)

router = APIRouter()


# 권한.상태 변경 응답 반복 제거
def build_user_response(
    user: User,
) -> UserResponse:
    return UserResponse(
        id=user.id,
        employee_id=user.employee_id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        must_change_password=user.must_change_password,
    )


@router.get(
    "",
    response_model=EmployeeListResponse,
    summary="MASTER 직원 계정 목록 조회",
)
def get_employee_accounts(
    keyword: str | None = Query(
        default=None,
        description="사번·이름·이메일 부분 검색",
    ),
    role: UserRole | None = Query(
        default=None,
        description="역할 필터",
    ),
    status: UserStatus | None = Query(
        default=None,
        description="계정 상태 필터",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="페이지 번호",
    ),
    size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="페이지당 조회 건수",
    ),
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> EmployeeListResponse:
    """
    현재 MASTER와 같은 테넌트에 속한 직원 계정을 조회한다.

    목록의 id는 권한·상태 변경 API에서 사용하는 User UUID이며,
    비밀번호와 Refresh Token 같은 민감한 인증 정보는 노출하지 않는다.
    """
    return list_employees(
        session=session,
        tenant_id=current_master.tenant_id,
        keyword=keyword,
        role=role,
        status=status,
        page=page,
        size=size,
    )


@router.get(
    "/bulk-template",
    summary="MASTER 직원 일괄 생성 엑셀 양식 다운로드",
)
def download_employee_bulk_template(
    _: User = Depends(require_master),
) -> StreamingResponse:
    """
    직원 일괄 생성에 사용할 빈 엑셀 양식을 다운로드한다.

    입력 헤더:
    이름 | 입사일 | 역할 | 이메일
    """
    content = build_employee_bulk_template_xlsx()

    return StreamingResponse(
        BytesIO(content),
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={
            "Content-Disposition": ('attachment; filename="employee_bulk_template.xlsx"'),
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/bulk-create",
    status_code=status.HTTP_201_CREATED,
    summary="MASTER 직원 계정 엑셀 일괄 생성",
    responses={422: {"description": ("엑셀 형식, 개별 행 값 또는 이메일 중복 검증 실패")}},
)
async def create_employee_accounts_bulk(
    file: UploadFile = File(
        description=("이름 | 입사일 | 역할 | 이메일 헤더를 가진 .xlsx 직원 일괄 생성 파일"),
    ),
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """
    직원 정보를 엑셀로 업로드하여 한 번에 생성한다.

    생성 성공 시 사번과 최초 비밀번호가 담긴 결과 엑셀을 반환한다.
    최초 비밀번호는 DB에 저장하지 않으며 이 응답 파일에서만 제공한다.
    """
    filename = file.filename or ""

    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": ("직원 일괄 생성 파일은 .xlsx 형식이어야 합니다."),
                "errors": ["파일 확장자를 확인해주세요."],
            },
        )

    try:
        file_content = await file.read()

        if not file_content:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_ENTITY),
                detail={
                    "message": ("업로드한 엑셀 파일이 비어 있습니다."),
                    "errors": [],
                },
            )

        rows = parse_employee_bulk_create_xlsx(file_content)

        results = create_employees_bulk(
            session=session,
            rows=rows,
            current_master=current_master,
        )

        result_xlsx = build_employee_bulk_result_xlsx(results)

    except EmployeeBulkExcelValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": str(exc),
                "errors": exc.errors,
            },
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": ("직원 계정 일괄 생성에 실패했습니다."),
                "errors": [str(exc)],
            },
        ) from exc

    finally:
        await file.close()

    issued_at = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    download_filename = f"employee_accounts_{issued_at}.xlsx"

    return StreamingResponse(
        BytesIO(result_xlsx),
        status_code=status.HTTP_201_CREATED,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={
            "Content-Disposition": (f'attachment; filename="{download_filename}"'),
            # 최초 비밀번호가 포함된 파일이므로 브라우저 캐시 금지
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


# MASTER 전용 직원 계정 생성
@router.post(
    "/create-accounts",
    response_model=EmployeeCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee_account(
    request: EmployeeCreateRequest,
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> EmployeeCreateResponse:
    user, temporary_password = create_employee(
        session=session,
        request=request,
        current_master=current_master,
    )

    return EmployeeCreateResponse(
        id=user.id,
        employee_id=user.employee_id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        temporary_password=temporary_password,
        must_change_password=user.must_change_password,
    )


# MASTER 전용 사용자 권한 변경
@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
)
def change_user_role(
    user_id: UUID,
    request: UserRoleUpdateRequest,
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = update_user_role(
        session=session,
        target_user_id=user_id,
        current_master=current_master,
        new_role=request.role,
    )

    return build_user_response(user)


# MASTER 전용 사용자 계정 상태 변경
@router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
)
def change_user_status(
    user_id: UUID,
    request: UserStatusUpdateRequest,
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = update_user_status(
        session=session,
        target_user_id=user_id,
        current_master=current_master,
        new_status=request.status,
    )

    return build_user_response(user)
