from langchain_core.messages import AIMessage
from .state import WMSInspectionState

def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    1. Vision Agent
    TODO: GPT-4o Vision API를 호출하여 이미지에서 BBox 추출 및 결함(Mint) 여부를 판단하세요.
    - 핵심: 사진 촬영 거리/구도에 영향을 받지 않도록, 전체 책 면적 대비 결함의 '상대 비율(Relative Ratio)'을 추출해야 합니다.
    - 입력: state["messages"] 내의 이미지 URL
    - 출력: is_mint (bool), defects (list of relative ratios)
    """
    print("[Agent] Vision Agent 스켈레톤 로직 실행...")
    #raise NotImplementedError("Vision Agent 로직을 구현해주세요.")
    dummy_is_mint = False
    dummy_defects = [{"type": "표지 찢김", "ratio": 15}]

    return{
        "is_mint": dummy_is_mint,
        "defects": dummy_defects,
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
    #raise NotImplementedError("Policy Agent 로직을 구현해주세요.")
    dummy_ubci_score = 150

    return{
        "ubci_score" : dummy_ubci_score,
        "reason_code": None,
        "messages": [
            AIMessage(content="[Policy Agent] UBCI 점수 산정 완료")
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
    #raise NotImplementedError("Critic Agent 로직을 구현해주세요.")
    
    revision_count = state.get("revision_count",0)
    ubci_score = state.get("ubci_score")
    
    if ubci_score is None:
        return{
            "reason_code" : "UBCI_POLICY_VIOLATION",
            "repair_directive" : "UBCI 점수가 없어 Policy Agent 재실행이 필요합니다.",
            "revision_count": revision_count + 1,
            "messages":[
                AIMessage(content="[Critic Agent] 검증 실패 - revision_count 증가")
            ],
        }
    
    if ubci_score < 0 or ubci_score > 100:
        return {
            "reason_code": "UBCI_POLICY_VIOLATION",
            "repair_directive": "UBCI 점수는 0~100 사이여야 합니다.",
            "revision_count": revision_count + 1,
            "messages": [
                AIMessage(content="[Critic Agent] 검증 실패 - UBCI 점수 범위 오류")
            ],
        }

    
    return{
        "reason_code" : "OK",
        "repair_directive" : None,
        "revision_count" : revision_count,
        "messages": [
            AIMessage(content="[Critic Agent] 검증 통과")
        ],
    }

def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    4. Auto-Refund Agent (Fast-track)
    TODO: MINT 등급의 새 책에 대한 환불 승인 사유서(JSON)를 작성하세요.
    - 출력: final_report (str, JSON format)
    """
    print("[Agent] Auto Refund Agent 스켈레톤 로직 실행...")
    #raise NotImplementedError("Auto Refund Agent 로직을 구현해주세요.")
    dummy_report = '{"result" : "AUTO_REFUND_APPROVED", "reason": "MINT 자동 승인"}'

    return{
        "final_report": dummy_report,
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
    #raise NotImplementedError("Report Agent 로직을 구현해주세요.")
    dummy_report = '{"result": "INSPECTION_COMPLETED", "message": "검수 완료"}'

    return{
        "final_report" : dummy_report,
        "messages": [
            AIMessage(content="[Report Agent] 최종 리포트 생성 완료")
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

    if human_feedback == "approve":
        return {
            "reason_code": "OK",
            "repair_directive": None,
            "revision_count": 0,
            "human_feedback": None,
            "messages": [
                AIMessage(content="[Human Node] 관리자 승인 완료 - revision_count 초기화")
            ],
        }

    if human_feedback == "recalculate":
        return {
            "ubci_score": None,
            "reason_code": None,
            "repair_directive": "관리자 수정 요청으로 Policy Agent 재실행 필요",
            "revision_count": 0,
            "human_feedback": None,
            "messages": [
                AIMessage(content="[Human Node] 관리자 수정 요청 완료 - revision_count 초기화")
            ],
        }
    return state
