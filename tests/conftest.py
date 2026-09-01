"""테스트 전역 설정.

`Settings`는 INITIAL_MASTER_* 3개를 기본값 없는 필수 항목으로 두고 `.env`에서 읽는다.
`.env`는 Git에 올라가지 않으므로 CI나 새로 클론한 개발자 환경에서는 값이 없고,
`app.core.config`를 임포트하는 순간 ValidationError로 **테스트 수집 자체가 실패**한다
(실측: CI에서 32개 모듈 collection error).

기본값을 Settings에 넣는 방식은 쓰지 않는다 — 초기 관리자 비밀번호에 기본값을 두면
운영에서 그대로 뜰 수 있다. 대신 테스트 전용 더미 값을 여기서 주입해
테스트 스위트를 개발자 로컬 `.env`로부터 독립시킨다.

이 파일은 app 임포트보다 먼저 로드되어야 하므로 모듈 최상단에서 환경변수를 설정한다.
"""

import os

# 테스트에서만 쓰는 더미 자격 증명. 실제 계정 생성에 쓰이지 않는다.
_TEST_ENV_DEFAULTS = {
    "INITIAL_MASTER_EMPLOYEE_ID": "TEST00000",
    "INITIAL_MASTER_NAME": "테스트관리자",
    "INITIAL_MASTER_PASSWORD": "TestOnly1234!",
}

# 이미 설정된 값(로컬 .env 등)은 덮어쓰지 않는다.
for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)
