"""모델 경로 해석이 레포 루트를 가리키는지 고정 (agents 패키지 분할 회귀 방지)."""

from pathlib import Path

from app.ai.agents.common import resolve_model_path


def test_relative_model_path_resolves_to_repo_root_models():
    resolved = resolve_model_path("models/doodle_best.pt")
    repo_root = Path(__file__).resolve().parents[2]
    assert resolved == repo_root / "models" / "doodle_best.pt"


def test_absolute_path_passes_through(tmp_path):
    absolute = str(tmp_path / "x.pt")
    assert resolve_model_path(absolute) == Path(absolute)
