# Celery 진입 모듈 (celery -A app.worker). Task 정의는 tasks.py에 있으며,
# import 시점에 celery_app에 등록되도록 여기서 불러온다.
from app.core.celery_app import celery_app  # noqa: F401
from app.worker.tasks import *  # noqa: F401,F403
