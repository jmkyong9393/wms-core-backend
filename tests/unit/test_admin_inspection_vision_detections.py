from app.services.admin_inspection_service import (
    _build_vision_detections,
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
    detections = _build_vision_detections(
        [build_valid_vision_defect()]
    )

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
    assert (
        detection.coordinate_space
        == "ORIGINAL_IMAGE_NORMALIZED"
    )


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