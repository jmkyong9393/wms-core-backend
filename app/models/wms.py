# 도메인별 모델 분할 후의 집약(re-export) 모듈.
# 기존 `from app.models.wms import X` 임포트와 alembic env를 그대로 유지한다.
# 테이블 정의 순서는 FK 의존을 따른다: enums -> tenancy/catalog -> inbound -> inventory ...
from app.models.enums import *  # noqa: F401,F403
from app.models.tenancy import *  # noqa: F401,F403
from app.models.catalog import *  # noqa: F401,F403
from app.models.inbound import *  # noqa: F401,F403
from app.models.inventory import *  # noqa: F401,F403
from app.models.orders import *  # noqa: F401,F403
from app.models.inspection import *  # noqa: F401,F403
from app.models.restock import *  # noqa: F401,F403
from app.models.analytics import *  # noqa: F401,F403
from app.models.board import *  # noqa: F401,F403
