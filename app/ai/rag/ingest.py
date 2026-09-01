# [미사용/legacy] 이 스크립트가 적재하는 컬렉션(wms_return_policies)은 Policy Agent가 읽지 않는다.
# 정식 시딩 절차는 `python -m app.ai.rag.policy_search` (컬렉션 wms_ubci_policies)이며,
# docker-compose의 rag-seed 서비스가 이를 실행한다. 반품 약관 컬렉션 확장 실험용으로 존치.
import os
from typing import Any, Dict, List

import chromadb
import yaml
from dotenv import load_dotenv
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 환경 변수 로드 (.env 파일)
load_dotenv()

# 상수 정의
YAML_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "docs",
    "ai_knowledge_base",
    "policy_data_master.yaml",
)
CHROMA_HOST = os.getenv("CHROMA_SERVER_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_SERVER_PORT", 8000))
COLLECTION_NAME = "wms_return_policies"


def load_yaml_data(file_path: str) -> List[Dict[str, Any]]:
    """YAML 파일을 읽어서 리스트 형태로 반환합니다."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"YAML 파일을 찾을 수 없습니다: {file_path}")

    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def process_documents(raw_data: List[Dict[str, Any]]) -> List[Document]:
    """원시 데이터를 LangChain Document 객체로 변환하고 메타데이터를 분리합니다."""
    documents = []

    for item in raw_data:
        # 본문 텍스트 구성 (parent_context + content)
        text_content = f"[{item.get('parent_context', '')}]\n{item.get('content', '')}"

        # 메타데이터 구성 (ChromaDB는 list 형태의 메타데이터 값을 지원하지 않으므로 문자열로 변환)
        category_list = item.get("category", [])
        category_str = ", ".join(category_list) if isinstance(category_list, list) else str(category_list)

        metadata = {
            "chunk_id": item.get("chunk_id", ""),
            "platform": item.get("platform", ""),
            "authority_level": item.get("authority_level", ""),
            "doc_title": item.get("doc_title", ""),
            "clause_ref": item.get("clause_ref", ""),
            "category": category_str,
        }

        doc = Document(page_content=text_content, metadata=metadata)
        documents.append(doc)

    # 만약 본문이 너무 길 경우를 대비해 스플리터 적용
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100, length_function=len)

    split_docs = text_splitter.split_documents(documents)
    return split_docs


def ingest_to_chroma(documents: List[Document]):
    """Document 객체들을 ChromaDB에 적재(Upsert)합니다."""
    print(f"총 {len(documents)}개의 청크를 임베딩 및 적재합니다...")

    # 1. ChromaDB 클라이언트 연결 (Docker 컨테이너)
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

    # 2. 임베딩 모델 초기화
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    # 3. LangChain Chroma 래퍼를 사용하여 적재
    Chroma.from_documents(
        documents=documents, embedding=embeddings, client=chroma_client, collection_name=COLLECTION_NAME
    )

    print(f"✅ ChromaDB 적재 완료! (Collection: {COLLECTION_NAME})")


def main():
    print("🚀 정책 데이터 파싱 및 임베딩 파이프라인 시작")
    try:
        # 0. 기존 컬렉션 초기화 (전체 배치 시작 전 한 번만 실행)
        chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        try:
            chroma_client.delete_collection(COLLECTION_NAME)
            print(f"기존 컬렉션 '{COLLECTION_NAME}'을(를) 삭제하고 초기화합니다.")
        except Exception:
            pass

        # 1. 데이터 로드
        raw_data = load_yaml_data(YAML_FILE_PATH)
        print(f"YAML 파일 로드 완료: {len(raw_data)} 개의 조항 발견")

        # 2. 배치 단위로 분할하여 전처리 및 DB 적재
        BATCH_SIZE = 30  # 한 번에 처리할 조항 수

        for i in range(0, len(raw_data), BATCH_SIZE):
            batch = raw_data[i : i + BATCH_SIZE]
            batch_no = i // BATCH_SIZE + 1
            batch_end = min(i + BATCH_SIZE, len(raw_data))
            print(f"\n📦 [Batch {batch_no}] {i + 1} ~ {batch_end} 번째 조항 처리 중...")

            documents = process_documents(batch)
            print(f"텍스트 분할 완료: {len(documents)} 개의 청크 생성")
            ingest_to_chroma(documents)

        print("\n🎉 모든 데이터의 BATCH 적재가 완료되었습니다!")

    except Exception as e:
        print(f"❌ 에러 발생: {e}")


if __name__ == "__main__":
    main()
