from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

REPO_ROOT = Path(__file__).resolve().parents[3]

load_dotenv(
    REPO_ROOT / ".env"
)

UBCI_POLICY_VERSION = (
    "UBCI_SPEC_V2.0.0.0"
)

UBCI_POLICY_FILE = (
    REPO_ROOT
    / "docs"
    / "ai_knowledge_base"
    / "UBCI_Specification_v2.0.0.0.md"
)
STANDARD_POLICY_FILE = (
    REPO_ROOT
    / "docs"
    / "ai_knowledge_base"
    / "WMS_표준_운영_정책서.md"
)

POLICY_FILES = (
    (
        UBCI_POLICY_FILE,
        "UBCI",
        UBCI_POLICY_VERSION,
    ),
    (
        STANDARD_POLICY_FILE,
        "WMS_OPERATION",
        "WMS_OPERATION_POLICY",
    ),
)

UBCI_COLLECTION_NAME = os.getenv(
    "UBCI_POLICY_COLLECTION_NAME",
    "wms_ubci_policies",
)
MAX_POLICY_CHUNK_LENGTH = 1200
MAX_POLICY_TOP_K = 20


@lru_cache(maxsize=1)
def get_policy_vectorstore() -> Chroma:
    """UBCI 전용 ChromaDB 연결."""

    client = chromadb.HttpClient(
        host=os.getenv(
            "CHROMA_SERVER_HOST",
            "localhost",
        ),
        port=int(
            os.getenv(
                "CHROMA_SERVER_PORT",
                "8002",
            )
        ),
    )

    return Chroma(
        client=client,
        collection_name=(
            UBCI_COLLECTION_NAME
        ),
        embedding_function=OpenAIEmbeddings(
            model=os.getenv(
                "OPENAI_EMBEDDING_MODEL",
                "text-embedding-3-small",
            )
        ),
    )


def _split_policy_document(
    content: str,
    max_length: int = MAX_POLICY_CHUNK_LENGTH,
) -> list[str]:
    """UBCI 문서를 검색 가능한 크기로 분할."""

    if not isinstance(content, str):
        raise TypeError("정책 문서는 문자열이어야 합니다.")
    if type(max_length) is not int or not 1 <= max_length <= 10_000:
        raise ValueError(
            "max_length는 1과 10000 사이의 정수여야 합니다."
        )

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    blocks = [
        block[start:start + max_length]
        for raw_block in content.split("\n\n")
        if (block := raw_block.strip())
        for start in range(0, len(block), max_length)
    ]

    for block in blocks:
        separator_length = 2 if current else 0

        if (
            current
            and current_length + separator_length + len(block)
            > max_length
        ):
            chunks.append(
                "\n\n".join(current)
            )
            current = []
            current_length = 0

        current.append(block)
        current_length += separator_length + len(block)

    if current:
        chunks.append(
            "\n\n".join(current)
        )

    return chunks


def _policy_clause_ref(
    chunk: str,
    fallback: str,
) -> str:
    """내부 청크 ID 대신 공개 가능한 문서 제목을 반환."""

    for line in chunk.splitlines():
        normalized = line.strip()
        if normalized.startswith("#"):
            title = normalized.lstrip("#").strip()
            if title:
                return title

    return fallback


def sync_ubci_policy() -> int:
    vectorstore = get_policy_vectorstore()
    documents = []
    ids = []
    new_ids_by_domain: dict[str, set[str]] = {}
    existing_ids_by_domain: dict[str, set[str]] = {}

    for path, domain, version in POLICY_FILES:
        if not path.exists():
            raise FileNotFoundError(
                f"정책 파일을 찾을 수 없습니다: {path}"
            )

        text = path.read_text(encoding="utf-8")
        chunks = _split_policy_document(text)

        for index, chunk in enumerate(chunks, start=1):
            chunk_id = f"{domain}_{index:03d}"

            documents.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "chunk_id": chunk_id,
                        "policy_domain": domain,
                        "policy_version": version,
                        "doc_title": path.stem,
                        "clause_ref": _policy_clause_ref(
                            chunk,
                            path.stem,
                        ),
                        "source": path.name,
                    },
                )
            )
            ids.append(chunk_id)
            new_ids_by_domain.setdefault(
                domain,
                set(),
            ).add(chunk_id)

    for _, domain, _ in POLICY_FILES:
        existing = vectorstore.get(
            where={"policy_domain": domain}
        )
        existing_ids_by_domain[domain] = set(
            existing.get("ids") or []
        )

    # 먼저 새 문서의 Embedding과 upsert를 끝냅니다.
    # 실패하더라도 기존 정책은 그대로 남습니다.
    vectorstore.add_documents(
        documents=documents,
        ids=ids,
    )

    for _, domain, _ in POLICY_FILES:
        stale_ids = sorted(
            existing_ids_by_domain[domain]
            - new_ids_by_domain.get(domain, set())
        )
        if stale_ids:
            vectorstore.delete(ids=stale_ids)

    return len(documents)


def search_policy_rules(
    defects: list[dict[str, Any]],
    policy_version: str = (
        UBCI_POLICY_VERSION
    ),
    k: int = 4,
) -> list[dict[str, Any]]:
    """현재 결함과 관련된 UBCI 정책 검색."""

    if not isinstance(policy_version, str) or not policy_version.strip():
        raise ValueError(
            "policy_version은 비어 있지 않은 문자열이어야 합니다."
        )

    if type(k) is not int or k < 2:
        raise ValueError(
            "k는 2 이상의 정수여야 합니다."
        )
    if k > MAX_POLICY_TOP_K:
        raise ValueError(
            f"k는 {MAX_POLICY_TOP_K} 이하여야 합니다."
        )

    if not isinstance(defects, list) or any(
        not isinstance(defect, dict)
        for defect in defects
    ):
        raise ValueError(
            "defects는 dict 항목으로 구성된 list여야 합니다."
        )

    defect_types = sorted(
        {
            str(
                defect.get("type", "")
            ).strip()
            for defect in defects
            if defect.get("type")
        }
    )

    query = (
        f"도서 결함: {', '.join(defect_types) or '없음'}. "
        "UBCI 감점, 등급, 치명 결함, 관리자 확인, "
        "재촬영 및 운영 처리 규칙"
    )
    if len(defect_types) > 20 or any(
        len(defect_type) > 100
        for defect_type in defect_types
    ):
        raise ValueError(
            "검색할 결함 종류가 허용 범위를 초과했습니다."
        )

    vectorstore = get_policy_vectorstore()
    results: list[tuple[Document, float]] = []

    metadata_filters = (
        (
            "UBCI",
            policy_version.strip(),
            UBCI_POLICY_FILE.name,
            {
                "$and": [
                    {
                        "policy_domain": {
                            "$eq": "UBCI",
                        }
                    },
                    {
                        "policy_version": {
                            "$eq": policy_version.strip(),
                        }
                    },
                ]
            },
        ),
        (
            "WMS_OPERATION",
            "WMS_OPERATION_POLICY",
            STANDARD_POLICY_FILE.name,
            {
                "$and": [
                    {
                        "policy_domain": {
                            "$eq": "WMS_OPERATION",
                        }
                    },
                    {
                        "policy_version": {
                            "$eq": "WMS_OPERATION_POLICY",
                        }
                    },
                ]
            },
        ),
    )

    for (
        domain,
        expected_version,
        expected_source,
        metadata_filter,
    ) in metadata_filters:
        domain_results = (
            vectorstore.similarity_search_with_score(
                query=query,
                k=max(1, (k + 1) // 2),
                filter=metadata_filter,
            )
        )
        validated_domain_results = []
        for document, distance in domain_results:
            metadata = document.metadata
            try:
                safe_distance = float(distance)
            except (TypeError, ValueError):
                continue

            chunk_id = metadata.get("chunk_id")
            clause_ref = metadata.get("clause_ref")
            if (
                metadata.get("policy_domain") != domain
                or metadata.get("policy_version")
                != expected_version
                or metadata.get("source") != expected_source
                or not isinstance(chunk_id, str)
                or not chunk_id.startswith(f"{domain}_")
                or not isinstance(clause_ref, str)
                or not clause_ref.strip()
                or not isinstance(document.page_content, str)
                or not document.page_content.strip()
                or len(document.page_content)
                > MAX_POLICY_CHUNK_LENGTH
                or not math.isfinite(safe_distance)
                or safe_distance < 0
            ):
                continue

            validated_domain_results.append(
                (document, safe_distance)
            )

        if not validated_domain_results:
            raise RuntimeError(
                f"정책 RAG에서 {domain} 도메인을 검색하지 못했습니다."
            )
        results.extend(validated_domain_results)

    results.sort(key=lambda item: item[1])
    results = results[:k]

    return [
        {
            "rule_id": (
                "UBCI_POLICY"
                if document.metadata.get(
                    "policy_domain"
                ) == "UBCI"
                else "WMS_OPERATION_POLICY"
            ),
            "chunk_id": (
                document.metadata.get(
                    "chunk_id"
                )
            ),
            "clause_ref": (
                document.metadata.get(
                    "clause_ref"
                )
            ),
            "policy_version": (
                document.metadata.get(
                    "policy_version"
                )
            ),
            "policy_domain": (
                document.metadata.get(
                    "policy_domain"
                )
            ),
            "source": document.metadata.get(
                "source",
                UBCI_POLICY_FILE.name,
            ),
            "distance": float(distance),
            "content": (
                document.page_content
            ),
        }
        for document, distance in results
    ]


if __name__ == "__main__":
    count = sync_ubci_policy()

    print(
        "[Policy RAG] "
        f"UBCI/WMS 정책 {count}개 청크 "
        "동기화 완료"
    )
