import pytest
from pydantic import ValidationError

from app.schemas.hitl import HITLDecisionRequest


def test_downgrade_requires_target_grade():
    with pytest.raises(ValidationError):
        HITLDecisionRequest(
            action="APPROVE_DOWNGRADE",
            reviewer_reason_code="DMG_EXT_TEAR",
        )


def test_downgrade_accepts_target_grade():
    request = HITLDecisionRequest(
        action="APPROVE_DOWNGRADE",
        reviewer_reason_code="DMG_EXT_TEAR",
        target_grade="NORMAL",
    )

    assert request.action.value == "APPROVE_DOWNGRADE"
    assert request.target_grade.value == "NORMAL"


def test_target_grade_is_rejected_for_other_actions():
    with pytest.raises(ValidationError):
        HITLDecisionRequest(
            action="REJECT_RETURN",
            reviewer_reason_code="DMG_EXT_TEAR",
            target_grade="NORMAL",
        )


def test_return_and_discard_actions_are_distinct():
    return_request = HITLDecisionRequest(
        action="REJECT_RETURN",
        reviewer_reason_code="DMG_EXT_TEAR",
    )
    discard_request = HITLDecisionRequest(
        action="REJECT_DISCARD",
        reviewer_reason_code="DMG_EXT_TEAR",
    )

    assert return_request.action.value == "REJECT_RETURN"
    assert discard_request.action.value == "REJECT_DISCARD"


def test_invalid_reason_code_is_rejected():
    with pytest.raises(ValidationError):
        HITLDecisionRequest(
            action="REJECT_RETURN",
            reviewer_reason_code="INVALID_REASON",
        )


def test_comment_length_is_limited():
    with pytest.raises(ValidationError):
        HITLDecisionRequest(
            action="RE_CHECK",
            reviewer_reason_code="SYS_BLURRY",
            comment="a" * 1001,
        )