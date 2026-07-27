import pytest

from app.services.certificate_service import extract_report_summary


@pytest.mark.parametrize(
    ("final_report", "expected"),
    [
        (
            '{"message": "경미한 모서리 찍힘이 있습니다.", '
            '"result": "INSPECTION_COMPLETED"}',
            "경미한 모서리 찍힘이 있습니다.",
        ),
        (
            '{"result": "INSPECTION_COMPLETED"}',
            "INSPECTION_COMPLETED",
        ),
        (
            "전체 상태가 우수합니다.",
            "전체 상태가 우수합니다.",
        ),
        (None, None),
        ("  ", None),
        ('{"defects": []}', None),
    ],
)
def test_extract_report_summary(final_report, expected):
    assert extract_report_summary(final_report) == expected
