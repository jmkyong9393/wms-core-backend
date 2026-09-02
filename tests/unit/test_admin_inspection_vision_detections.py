from app.domains.admin.admin_inspection_service import (
    _build_confirmed_defects,
    _build_vision_detections,
    _build_yolo_candidates,
)


def build_valid_vision_defect():
    return {
        "image_index": 0,
        "image_view": "FRONT",
        "image_url": "https://example.com/front.jpg",
        "type": "COVER_TEAR",
        "defect_type": "COVER_TEAR",
        "ratio": 15,
        "confidence": 0.91,
        "yolo_confidence": 0.84,
        "bbox": [0.12, 0.08, 0.31, 0.27],
        "coordinate_space": "ORIGINAL_IMAGE_NORMALIZED",
    }


def test_builds_vision_detection_from_complete_agent_defect():
    detections = _build_vision_detections([build_valid_vision_defect()])

    assert len(detections) == 1

    detection = detections[0]
    assert detection.image_index == 0
    assert detection.image_view == "FRONT"
    assert detection.type == "COVER_TEAR"
    assert detection.defect_type == "COVER_TEAR"
    assert detection.ratio == 15
    assert detection.confidence == 0.91
    assert detection.yolo_confidence == 0.84
    assert detection.bbox == [0.12, 0.08, 0.31, 0.27]
    assert detection.coordinate_space == "ORIGINAL_IMAGE_NORMALIZED"


def test_ignores_legacy_defect_without_overlay_fields():
    legacy_defect = {
        "type": "COVER_TEAR",
        "ratio": 15,
    }

    assert _build_vision_detections([legacy_defect]) == []


def test_ignores_invalid_normalized_bbox():
    invalid_defect = build_valid_vision_defect()
    invalid_defect["bbox"] = [0.8, 0.1, 0.2, 0.4]

    assert _build_vision_detections([invalid_defect]) == []


def build_valid_yolo_candidate():
    return {
        "candidate_id": 0,
        "image_index": 0,
        "image_view": "FRONT",
        "image_url": "https://example.com/front.jpg",
        "defect_type": "COVER_TEAR",
        "confidence": 0.73,
        "bbox": [0.12, 0.08, 0.31, 0.27],
        "coordinate_space": "ORIGINAL_IMAGE_NORMALIZED",
        "source_model": "general_binary",
        "review_decision": "CONFIRMED",
        "reject_reason": None,
    }


def test_builds_all_yolo_candidates_including_rejected_candidate():
    confirmed = build_valid_yolo_candidate()

    rejected = build_valid_yolo_candidate()
    rejected["candidate_id"] = 1
    rejected["defect_type"] = "OTHER_VISIBLE_DAMAGE"
    rejected["review_decision"] = "REJECTED"
    rejected["reject_reason"] = "COVER_PATTERN"

    candidates = _build_yolo_candidates([confirmed, rejected])

    assert len(candidates) == 2
    assert candidates[0].review_decision == "CONFIRMED"
    assert candidates[1].review_decision == "REJECTED"
    assert candidates[1].reject_reason == "COVER_PATTERN"


def test_builds_confirmed_defect_and_normalizes_legacy_inside_view():
    defect = build_valid_yolo_candidate()
    defect["image_view"] = "INSIDE"

    confirmed_defects = _build_confirmed_defects([defect])

    assert len(confirmed_defects) == 1
    assert confirmed_defects[0].candidate_id == 0
    assert confirmed_defects[0].image_view == "INNER"
    assert confirmed_defects[0].defect_type == "COVER_TEAR"


def test_excludes_rejected_candidate_from_confirmed_defects():
    rejected = build_valid_yolo_candidate()
    rejected["review_decision"] = "REJECTED"

    assert _build_confirmed_defects([rejected]) == []


def test_ignores_invalid_yolo_candidate_bbox():
    candidate = build_valid_yolo_candidate()
    candidate["bbox"] = [0.8, 0.1, 0.2, 0.4]

    assert _build_yolo_candidates([candidate]) == []
