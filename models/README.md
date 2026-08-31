# models/ — YOLO 서빙 가중치 배치 안내

`*.pt`는 Git에 올라가지 않는다 (`.gitignore`). 정본은 `models_registry/serving/`이며,
클론 후 아래 파일을 이 폴더에 배치해야 AI 검수가 동작한다.

| 파일 | 실측 클래스 | 코드 참조 |
|---|---|---|
| `general_binary_team_s3_v2_best.pt` | `Wornout`, `ripped` (소문자 정규화 후 매핑) | `YOLO_MODEL_SPECS` general_binary — 기본 활성 |
| `doodle_best.pt` | `doodle_scribble` | `YOLO_MODEL_SPECS` doodle — 기본 활성 |
| `yolov8x-worldv2.pt` | YOLO-World (프롬프트 "book") | `YOLO_BOOK_MODEL_PATH` 책 영역 탐지 |
| `yolov8_high_precision_base.pt` | `Wornout`, `ripped` | 미참조 — Phase 5 WBF 앙상블 예정분 보관 |

**physical4_best.pt는 존재하지 않는다.** `YOLO_MODEL_SPECS`의 physical4 항목(cover_tear /
edge_wear / general_stain / page_fold 4클래스)에 해당하는 학습본이 아직 없다 (2026-08-31
전체 드라이브 검색 실측). `YOLO_ENABLED_MODELS`에 physical4를 추가하면
FileNotFoundError가 나는 것이 정상이며, 다른 가중치를 그 이름으로 넣지 말 것 —
클래스가 달라 탐지 결과가 조용히 왜곡된다.
