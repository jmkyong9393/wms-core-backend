# 도메인별 모델 분할 후의 집약(re-export) 모듈.
# 기존 `from app.models.wms import X` 임포트와 alembic env를 그대로 유지한다.
# 테이블 정의 순서는 FK 의존을 따른다: enums -> tenancy/catalog -> inbound -> inventory ...
from app.models.analytics import *
from app.models.board import *
from app.models.catalog import *
from app.models.enums import *
from app.models.inbound import *
from app.models.inspection import *
from app.models.inventory import *
from app.models.orders import *
from app.models.restock import *
from app.models.tenancy import *
