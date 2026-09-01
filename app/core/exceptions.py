from fastapi import status


# 애플리케이션의 공통 비즈니스 예외
class AppException(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        error_code: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code
        self.headers = headers

        super().__init__(detail)


# 로그인 정보 오류
class InvalidCredentialsException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사번 또는 비밀번호가 올바르지 않습니다.",
            error_code="INVALID_CREDENTIALS",
        )


# 비활성 계정
class InactiveUserException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다.",
            error_code="INACTIVE_USER",
        )


# 이메일 중복
class DuplicateEmailException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 이메일입니다.",
            error_code="DUPLICATE_EMAIL",
        )


# MASTER 권한 예외 처리
class MasterPermissionRequiredException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MASTER 권한이 필요합니다.",
            error_code="MASTER_PERMISSION_REQUIRED",
        )


# 비밀번호 입력 오류시 예외 처리
class InvalidCurrentPasswordException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="현재 비밀번호가 올바르지 않습니다.",
            error_code="INVALID_CURRENT_PASSWORD",
        )


# 새 비밀번호가 발급된 비밀번호와 같을 시 예외 처리
class SamePasswordException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호는 현재 비밀번호와 달라야 합니다.",
            error_code="SAME_PASSWORD",
        )


# 최초 로그인 후 비밀번호 변경 안 된 경우 예외 처리
class PasswordChangeRequiredException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="최초 로그인 후 비밀번호 변경이 필요합니다.",
            error_code="PASSWORD_CHANGE_REQUIRED",
        )


# 변경 대상 사용자 없음
class UserNotFoundException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="사용자를 찾을 수 없습니다.",
            error_code="USER_NOT_FOUND",
        )


# 관리자가 자기 권한을 직접 낮추는 것 방지
class SelfRoleChangeNotAllowedException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="본인의 권한은 직접 변경할 수 없습니다.",
            error_code="SELF_ROLE_CHANGE_NOT_ALLOWED",
        )


# 관리자가 자기 계정을 직접 비활성화하는 것 방지
class SelfStatusChangeNotAllowedException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="본인의 계정 상태는 직접 변경할 수 없습니다.",
            error_code="SELF_STATUS_CHANGE_NOT_ALLOWED",
        )


# ACTIVE MASTER가 0명이 되는 상황 방지
class LastActiveMasterException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="마지막 활성 MASTER 계정은 강등하거나 비활성화할 수 없습니다.",
            error_code="LAST_ACTIVE_MASTER",
        )


# HITL 관리자 판단 대상 검수 작업을 찾을 수 없는 경우
class HITLJobNotFoundException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="관리자 검토 대상 검수 작업을 찾을 수 없습니다.",
            error_code="HITL_JOB_NOT_FOUND",
        )


# HITL_REQUIRED 상태가 아닌 작업에 관리자 판단을 요청한 경우
class InvalidHITLStateException(AppException):
    def __init__(self, current_status: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"관리자 판단을 처리할 수 없는 검수 상태입니다. current_status={current_status}"),
            error_code="INVALID_HITL_STATE",
        )


# 관리자 판단 이후 Celery 작업 등록에 실패한 경우
class HITLTaskDispatchException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("관리자 판단 후속 작업을 비동기 큐에 등록하지 못했습니다. 잠시 후 다시 시도해 주세요."),
            error_code="HITL_TASK_DISPATCH_FAILED",
            headers={
                "Retry-After": "5",
            },
        )


# ADMIN 권한이 필요한 API에 일반 사용자가 접근한 경우
class AdminPermissionRequiredException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ADMIN 권한이 필요합니다.",
            error_code="ADMIN_PERMISSION_REQUIRED",
        )
