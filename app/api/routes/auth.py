from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from sqlmodel import Session

from app.core.config import settings
from app.core.database import engine
from app.core.security import create_access_token
from app.models.wms import UserStatus, User
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse
from app.services.auth_service import (
    authenticate_user,
    create_user,
    get_user_by_email,
    get_user_by_employee_id,
    validate_signup_code,
)
from app.api.dependencies.auth import get_current_user


router = APIRouter()

# 회원가입 api
# 역할별 가입 코드를 검증하고 새로운 사용자를 생성함.
@router.post("/signup", status_code = status.HTTP_201_CREATED,)
def signup(request: SignupRequest):

        # 1. 선택한 role과 가입 코드가 일치하는지 확인
        if not validate_signup_code(
                role = request.role,
                security_code=request.security_code,
        ):
                raise HTTPException(
                        status_code = status.HTTP_403_FORBIDDEN,
                        detail = "가입 제한 코드가 올바르지 않습니다.",
                )
        
        with Session(engine) as session:
                
                # 2. 사번 중복 확인
                existing_employee = get_user_by_employee_id(
                        session = session,
                        employee_id = request.employee_id,
                )

                if existing_employee is not None:
                        raise HTTPException(
                                status_code = status.HTTP_409_CONFLICT,
                                detail="이미 사용 중인 사번입니다.",
                        )
                
                # 3. 이메일을 입력한 경우 이메일 중복 확인
                if request.email is not None:
                        existing_email = get_user_by_email(
                            session = session,
                            email = str(request.email),
                        )

                        if existing_email is not None:
                                raise HTTPException(
                                        status_code = status.HTTP_409_CONFLICT,
                                        detail="이미 사용 중인 이메일입니다.",
                                )
                        
                # 4. 비밀번호 해시 후 사용자 DB 저장
                user = create_user(
                        session = session,
                        request = request,
                )

                # 5. 비밀번호를 제외한 사용자 정보 반환
                return {
                        "message": "회원가입이 완료되었습니다.",
                        "user": {
                                "id": str(user.id),
                                "employee_id": user.employee_id,
                                "email": user.email,
                                "name": user.name,
                                "role": user.role,
                                "status": user.status,
                        },
                }
        

# 로그인 api
# 사번과 비밀번호를 검증하고 JWT Access Token 발급함.
@router.post("/login", response_model = TokenResponse,)
def login(request: LoginRequest):
        
        with Session(engine) as session:
                
                # 1. 사번과 비밀번호 검증
                user = authenticate_user(
                        session = session,
                        employee_id = request.employee_id,
                        password =  request.password,
                )

                if user is None:
                        raise HTTPException(
                                status_code = status.HTTP_401_UNAUTHORIZED,
                                detail = "사번 또는 비밀번호가 올바르지 않습니다.",
                                headers={"WWW-Authenticate": "Bearer"},
                        )
                
                # 2. 비활성 계정 로그인 차단
                if user.status == UserStatus.INACTIVE:
                        raise HTTPException(
                                status_code = status.HTTP_403_FORBIDDEN,
                                detail = "비활성화된 계정입니다.",
                        )
                
                # 3. JWT Access Token 생성
                access_token = create_access_token(
                        subject = str(user.id),
                        role = user.role.value,
                )

                # 4. 마지막 로그인 시간 저장
                user.last_login = datetime.utcnow()

                session.add(user)
                session.commit()

                # 5. 토큰 반환
                return TokenResponse(
                        access_token = access_token,
                        expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                )
                
# 현재 로그인한 사용자 정보 반환하는 api
@router.get("/me")
def get_me(
        current_user: User = Depends(get_current_user),
):
        return{
                "id": str(current_user.id),
                "employee_id" : current_user.employee_id,
                "email" : current_user.email,
                "name" : current_user.name,
                "role" : current_user.role,
                "status" : current_user.status,
                "last_login" : current_user.last_login,
        }