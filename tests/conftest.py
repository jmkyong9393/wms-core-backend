"""테스트 전역 설정.

Settings의 INITIAL_MASTER_* 는 기본값 없는 필수 항목이라 .env가 없으면 임포트 시점에
실패한다. 테스트가 로컬 .env에 의존하지 않도록 더미 값을 주입한다.
Settings에 기본값을 두지 않는 것은 운영에 그대로 뜨는 것을 막기 위함이다.
"""

import os

# 테스트 전용 더미 값
_TEST_ENV_DEFAULTS = {
    "INITIAL_MASTER_EMPLOYEE_ID": "TEST00000",
    "INITIAL_MASTER_NAME": "테스트관리자",
    "INITIAL_MASTER_PASSWORD": "TestOnly1234!",
}

# 로컬 .env 등 이미 설정된 값은 덮어쓰지 않는다.
for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)
