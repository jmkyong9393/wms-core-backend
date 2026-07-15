from fastapi import status

# 애플리케이션의 공통 비즈니스 예외
class AppException(Exception):
        def __init__(
                        self,
                        *,
                        status_code: int,
                        detail: str,
                        error_code: str,
        ) -> None:
                self.status_code = status_code
                self.detail = detail
                self.error_code = error_code
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

# 사번 중복
class DuplicateEmployeeIdException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 사번입니다.",
            error_code="DUPLICATE_EMPLOYEE_ID",
        )


# 이메일 중복
class DuplicateEmailException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 이메일입니다.",
            error_code="DUPLICATE_EMAIL",
        )


# 가입 코드 오류
class InvalidSignupCodeException(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="가입 코드가 올바르지 않습니다.",
            error_code="INVALID_SIGNUP_CODE",
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