from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage

# 임시 승인 기준이며 AI 정책 확정 후 조정 필요
APPROVAL_SCORE_THRESHOLD = 70


# LangGraph Supervisor 파이프라인 호출 Wrapper
class LangGraphInspectionWrapper:

    # Supervisor에 전달할 초기 검수 State 생성
    def build_initial_inspection_state(
        self,
        tenant_id: UUID,
        book_id: UUID,
        mode: str,
        image_paths: list[str],
    ) -> dict[str, Any]:
        image_path_text = "\n".join(
            f"- {path}"
            for path in image_paths
        )

        return {
            "messages": [
                HumanMessage(
                    content=(
                        "다음 도서 이미지를 AI 검수해주세요.\n"
                        f"tenant_id: {tenant_id}\n"
                        f"book_id: {book_id}\n"
                        f"mode: {mode}\n"
                        "image_paths:\n"
                        f"{image_path_text}"
                    )
                )
            ],
            "tenant_id": str(tenant_id),
            "book_id": str(book_id),
            "mode": mode,
            "image_paths": image_paths,

            "is_mint": None,
            "defects": None,
            "ubci_score": None,
            "reason_code": None,
            "repair_directive": None,
            "revision_count": 0,
            "human_feedback": None,
            "final_report": None,
        }

    # LangGraph 최종 State를 Worker 판정값으로 변환
    def convert_state_to_decision(
        self,
        final_state: dict[str, Any],
    ) -> str:
        is_mint = final_state.get("is_mint")
        reason_code = final_state.get("reason_code")
        ubci_score = final_state.get("ubci_score")

        # 상태가 완전 양호한 경우 승인
        if is_mint is True:
            return "APPROVE"

        if (
            reason_code == "OK"
            and ubci_score is not None
            and ubci_score >= APPROVAL_SCORE_THRESHOLD
        ):
            return "APPROVE"

        return "REJECT"

    # LangGraph 최종 State를 Worker 결과 형식으로 변환
    def convert_final_state_to_worker_result(
        self,
        final_state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "decision": self.convert_state_to_decision(
                final_state
            ),
            "ubci_score": final_state.get("ubci_score"),
            "final_report": final_state.get(
                "final_report"
            ),
            "agent_logs": {
                "is_mint": final_state.get("is_mint"),
                "defects": final_state.get("defects"),
                "reason_code": final_state.get(
                    "reason_code"
                ),
                "repair_directive": final_state.get(
                    "repair_directive"
                ),
                "revision_count": final_state.get(
                    "revision_count"
                ),
                "human_feedback": final_state.get(
                    "human_feedback"
                ),
            },
        }

    # Supervisor 실행 후 최종 결과를 dict로 반환
    def run_inspection(
            self,
            job_id: UUID,
            inspection_task_id: str,
            tenant_id: UUID,
            book_id: UUID,
            mode: str,
            image_paths: list[str],
        ) -> dict[str, Any]:
        # supervisor가 wrapper를 참조할 수 있어 순환 import를 방지한다.
        from app.ai.supervisor import (
            app_graph,
            build_supervisor_graph,
        )

        graph = app_graph or build_supervisor_graph()

        initial_state = self.build_initial_inspection_state(
            tenant_id=tenant_id,
            book_id=book_id,
            mode=mode,
            image_paths=image_paths,
        )

       # 최초 검수와 관리자 재검수가 서로 다른 체크포인트를 사용하도록
        # Celery Task ID를 LangGraph thread_id에 포함한다.
        thread_id = (
            f"tenant-{tenant_id}"
            f"-inspection-{job_id}"
            f"-task-{inspection_task_id}"
        )

        config = {
            "configurable": {
                "thread_id": thread_id,
            }
        }

        graph.invoke(
            initial_state,
            config=config,
        )

        snapshot = graph.get_state(config)
        current_state = snapshot.values

        # human_node 실행 직전에 멈췄다면 관리자 검토가 필요한 상태
        if "human_node" in snapshot.next:
            return {
                "decision": "HITL_REQUIRED",
                "ubci_score": current_state.get("ubci_score"),
                "final_report": None,
                "agent_logs": {
                    "is_mint": current_state.get("is_mint"),
                    "defects": current_state.get("defects"),
                    "reason_code": current_state.get("reason_code"),
                    "repair_directive": current_state.get(
                        "repair_directive"
                    ),
                    "revision_count": current_state.get(
                        "revision_count"
                    ),
                    "human_feedback": current_state.get(
                        "human_feedback"
                    ),
                    "thread_id": thread_id,
                },
            }

        return self.convert_final_state_to_worker_result(
            current_state
        )
