import json
from langchain_core.messages import AIMessage
from .state import WMSInspectionState


MIN_VISION_CONFIDENCE = 0.80
MIN_POLICY_CONFIDENCE = 0.75


def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    1. Vision Agent
    TODO: GPT-4o Vision API를 호출하여 이미지에서 BBox 추출 및 결함(Mint) 여부를 판단하세요.
    - 핵심: 사진 촬영 거리/구도에 영향을 받지 않도록, 전체 책 면적 대비 결함의 '상대 비율(Relative Ratio)'을 추출해야 합니다.
    - 입력: state["messages"] 내의 이미지 URL
    - 출력: is_mint (bool), defects (list of relative ratios)
    """
    print("[Agent] Vision Agent 스켈레톤 로직 실행...")

    vision_result = {
        "is_mint": False,
        "defects": [{"type": "표지 찢김", "ratio": 15}],
        "vision_confidence": 0.92,
    }

    is_mint = vision_result.get("is_mint")
    defects = vision_result.get("defects")
    vision_confidence = vision_result.get("vision_confidence")
    revision_count = state.get("revision_count", 0)
    reason_code = None
    repair_directive = None

    if type(is_mint) is not bool or type(defects) is not list:
        reason_code = "QUALITY_ERROR"
        repair_directive = "Vision Agent 출력 형식이 올바르지 않습니다."
    elif type(vision_confidence) not in (int, float) or not 0 <= vision_confidence <= 1:
        reason_code = "QUALITY_ERROR"
        repair_directive = "vision_confidence는 0~1 범위의 숫자여야 합니다."
    elif is_mint and not defects and vision_confidence < MIN_VISION_CONFIDENCE:
        reason_code = "VISION_LOW_CONFIDENCE"
        repair_directive = "MINT 판정 신뢰도가 기준보다 낮습니다."

    if reason_code is not None:
        revision_count += 1

    return {
        "is_mint": is_mint,
        "defects": defects,
        "vision_confidence": vision_confidence,
        "ubci_score": None,
        "rule_reference": None,
        "policy_confidence": None,
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "overall_confidence": None,
        "human_feedback": None,
        "final_report": None,
        "messages": [
            AIMessage(content="[Vision Agent] 이미지 판독 완료")
        ],
    }

def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    2. Policy Agent (RAG 적용)
    TODO: Vision이 넘겨준 상대 비율(예: 가로 15% 찢김)을 바탕으로 Vector DB(RAG)를 검색하여 UBCI 규정을 찾아오세요.
    - 핵심: RAG로 검색된 규정(ex. 10~20% 찢김 감점)을 기반으로 수학적인 감점 점수를 계산하고 사유를 작성합니다.
    - 입력: state["defects"] (상대 비율 데이터)
    - 출력: ubci_score (int), rule_reference (str)
    """
    print("[Agent] Policy Agent 스켈레톤 로직 실행...")

    # 실제 연동 시 이 결과만 Vector DB 검색 및 UBCI 계산 결과로 교체합니다.
    policy_result = {
        "ubci_score": 85,
        "rule_reference": "WMS_POLICY_ARTICLE_5",
        "policy_confidence": 0.88,
    }

    ubci_score = policy_result.get("ubci_score")
    rule_reference = policy_result.get("rule_reference")
    policy_confidence = policy_result.get("policy_confidence")
    output_missing = (
        ubci_score is None
        or not isinstance(rule_reference, str)
        or not rule_reference.strip()
        or policy_confidence is None
    )
    revision_count = state.get("revision_count", 0)

    if output_missing:
        revision_count += 1

    return {
        "ubci_score": ubci_score,
        "rule_reference": rule_reference,
        "policy_confidence": policy_confidence,
        "reason_code": "UBCI_POLICY_VIOLATION" if output_missing else None,
        "repair_directive": "Policy Agent 출력값이 누락되었습니다." if output_missing else None,
        "revision_count": revision_count,
        "overall_confidence": None,
        "human_feedback": None,
        "final_report": None,
        "messages": [
            AIMessage(content="[Policy Agent] 정책 적용 및 UBCI 점수 산정 완료")
        ],
    }


def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    3. Critic Agent
    TODO: Policy Agent가 RAG에서 가져온 규정과 실제 감점 연산이 타당한지 교차 검증하세요.
    - 핵심: 검증 실패 시 Policy로 되돌려보냅니다. 여러 번 실패할 경우 HITL(수동 개입)으로 에스컬레이션합니다.
    - 출력: reason_code ("OK", "REJECT"), revision_count 증가
    """
    print("[Agent] Critic Agent 스켈레톤 로직 실행...")

    revision_count = state.get("revision_count", 0)
    is_mint = state.get("is_mint")
    defects = state.get("defects")
    vision_confidence = state.get("vision_confidence")
    ubci_score = state.get("ubci_score")
    rule_reference = state.get("rule_reference")
    policy_confidence = state.get("policy_confidence")
    reason_code = "OK"
    repair_directive = None
    overall_confidence = None

    # revision_count 타입 검증
    raw_revision_count = state.get("revision_count", 0)
    if type(raw_revision_count) is not int or raw_revision_count < 0:
        reason_code = "QUALITY_ERROR"
        repair_directive = "revision_count는 0 이상의 정수여야 합니다."

    # Vision 출력 타입 검증
    elif type(is_mint) is not bool:
        reason_code = "QUALITY_ERROR"
        repair_directive = "is_mint는 bool이어야 합니다."

    elif type(defects) is not list:
        reason_code = "QUALITY_ERROR"
        repair_directive = "defects는 list여야 합니다."

    # 각 결함의 필수 값 검증
    else:
        for defect in defects:
            if type(defect) is not dict:
                reason_code = "QUALITY_ERROR"
                repair_directive = "defects의 각 항목은 dict여야 합니다."
                break

            defect_type = defect.get("type")
            ratio = defect.get("ratio")

            if type(defect_type) is not str or not defect_type.strip():
                reason_code = "QUALITY_ERROR"
                repair_directive = "결함 type은 비어 있지 않은 문자열이어야 합니다."
                break

            if type(ratio) not in (int, float) or not 0 <= ratio <= 100:
                reason_code = "QUALITY_ERROR"
                repair_directive = "결함 ratio는 0~100 범위의 숫자여야 합니다."
                break

    # Vision 판정과 결함 데이터의 모순 검증
    if reason_code == "OK" and is_mint is True and defects:
        reason_code = "BBOX_MISMATCH"
        repair_directive = "MINT 판정과 결함 데이터가 서로 모순됩니다."

    elif reason_code == "OK" and is_mint is False and not defects:
        reason_code = "BBOX_MISMATCH"
        repair_directive = "비정상품 판정에는 한 개 이상의 결함이 필요합니다."

    # Vision 신뢰도 검증
    elif reason_code == "OK" and (type(vision_confidence) not in (int, float) or not 0 <= vision_confidence <= 1):
        reason_code = "QUALITY_ERROR"
        repair_directive = "vision_confidence는 0~1 범위의 숫자여야 합니다."

    elif reason_code == "OK" and vision_confidence < MIN_VISION_CONFIDENCE:
        reason_code = "VISION_LOW_CONFIDENCE"
        repair_directive = "Vision 판정 신뢰도가 기준보다 낮습니다."

    # UBCI 점수 검증
    elif reason_code == "OK" and (type(ubci_score) is not int or not 0 <= ubci_score <= 100):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "ubci_score는 0~100 범위의 정수여야 합니다."

    # 정책 근거 검증
    elif reason_code == "OK" and (type(rule_reference) is not str or not rule_reference.strip()):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "rule_reference는 비어 있지 않은 문자열이어야 합니다."

    # Policy 신뢰도 검증
    elif reason_code == "OK" and (type(policy_confidence) not in (int, float) or not 0 <= policy_confidence <= 1):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "policy_confidence는 0~1 범위의 숫자여야 합니다."

    elif reason_code == "OK" and policy_confidence < MIN_POLICY_CONFIDENCE:
        reason_code = "POLICY_LOW_CONFIDENCE"
        repair_directive = "Policy 검색 및 계산 신뢰도가 기준보다 낮습니다."

    if reason_code == "OK":
        overall_confidence = min(vision_confidence, policy_confidence)
    else:
        revision_count += 1

    return {
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "overall_confidence": overall_confidence,
        "final_report": None,
        "messages": [
            AIMessage(content=f"[Critic Agent] 검증 결과 - {reason_code}")
        ],
    }

def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    4. Auto-Refund Agent (Fast-track)
    TODO: MINT 등급의 새 책에 대한 환불 승인 사유서(JSON)를 작성하세요.
    - 출력: final_report (str, JSON format)
    """
    print("[Agent] Auto Refund Agent 스켈레톤 로직 실행...")
    dummy_report = {
        "result": "AUTO_REFUND_APPROVED",
        "reason": "MINT 자동 승인",
        "vision_confidence": state.get("vision_confidence"),
    }

    return {
        "final_report": json.dumps(dummy_report, ensure_ascii=False),
        "overall_confidence": state.get("vision_confidence"),
        "human_feedback": None,
        "messages": [
            AIMessage(content="[Auto Refund Agent] 자동 환불 승인 리포트 생성 완료")
        ],
    }

def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    5. Report Agent (감성/페르소나 렌더링)
    TODO: 검증이 완료된 기술적 사유를 바탕으로, 상황에 맞는 CS 페르소나(Tone & Manner)를 입혀 고객용 보증서를 생성하세요.
    - 핵심: 파이썬 단순 문자열 조합이 아닌, 결함의 심각도(가벼운 오염 vs 심각한 파손)에 따라 동적으로 다정한 위로나 단호한 매입 불가 안내를 작성해야 LLM을 사용하는 명분이 생깁니다.
    - 출력: final_report (str, JSON format)
    """
    print("[Agent] Report Agent 스켈레톤 로직 실행...")
    human_feedback = state.get("human_feedback")
    result = {
        "APPROVE": "HUMAN_APPROVED",
        "REJECT": "HUMAN_REJECTED",
    }.get(human_feedback, "INSPECTION_COMPLETED")
    message = {
        "APPROVE": "관리자 승인 완료",
        "REJECT": "관리자 반려 완료",
    }.get(human_feedback, "검수 완료")
    dummy_report = {
        "result": result,
        "defects": state.get("defects") or [],
        "ubci_score": state.get("ubci_score"),
        "rule_reference": state.get("rule_reference"),
        "reason_code": state.get("reason_code"),
        "vision_confidence": state.get("vision_confidence"),
        "policy_confidence": state.get("policy_confidence"),
        "overall_confidence": state.get("overall_confidence"),
        "message": message,
    }

    return {
        "final_report": json.dumps(dummy_report, ensure_ascii=False),
        "human_feedback": None,
        "messages": [
            AIMessage(content=f"[Report Agent] {message}")
        ],
    }

def human_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    6. Human-In-The-Loop (HITL) 노드
    TODO: Critic이 반복해서 Policy를 반려하거나 확신할 수 없는 예외 케이스(Outlier)일 경우, 관리자의 수동 개입을 대기합니다.
    - 주의: 이 노드는 MemorySaver에 의해 일시 정지(Pause)를 유발하는 용도이므로 빈 상태로 둡니다.
    """
    print("[Agent] HITL 노드 진입 - 관리자의 수동 개입(승인/수정) 대기 중")
    human_feedback = state.get("human_feedback")

    if human_feedback == "APPROVE":
        return {
            "reason_code": "OK",
            "repair_directive": None,
            "revision_count": 0,
            "final_report": None,
            "messages": [
                AIMessage(content="[Human Node] 관리자 승인 완료 - revision_count 초기화")
            ],
        }

    if human_feedback == "REJECT":
        return {
            "reason_code": state.get("reason_code") or "QUALITY_ERROR",
            "revision_count": 0,
            "human_feedback": "REJECT",
            "final_report": None,
            "messages": [AIMessage(content="[Human Node] 관리자 반려 완료")],
        }

    if human_feedback == "RECALCULATE":
        result = {
            "reason_code": None,
            "repair_directive": "관리자 재검수 요청",
            "revision_count": 0,
            "overall_confidence": None,
            "human_feedback": None,
            "final_report": None,
        }

        if state.get("reason_code") in {
            "QUALITY_ERROR",
            "BBOX_MISMATCH",
            "VISION_LOW_CONFIDENCE",
        }:
            result.update({
                "is_mint": None,
                "defects": None,
                "vision_confidence": None,
            })

        result.update({
            "ubci_score": None,
            "rule_reference": None,
            "policy_confidence": None,
            "messages": [
                AIMessage(content="[Human Node] 관리자 수정 요청 완료 - revision_count 초기화")
            ],
        })
        return result

    return {}
