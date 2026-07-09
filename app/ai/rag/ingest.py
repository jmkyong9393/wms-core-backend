import os
import yaml
import chromadb
from typing import List, Dict, Any
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

# 환경 변수 로드 (.env 파일)
load_dotenv()

# 상수 정의
YAML_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "docs", "ai_knowledge_base", "policy_data_master.yaml"
)
CHROMA_HOST = os.getenv("CHROMA_SERVER_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_SERVER_PORT", 8000))
COLLECTION_NAME = "wms_return_policies"

#TO-DO 한 번에 처리하지 않고 Batch단위로 처리하도록 수정해야함. 정책이 늘어날 경우를 대비
def load_yaml_data(file_path: str) -> List[Dict[str, Any]]:
    """YAML 파일을 읽어서 리스트 형태로 반환합니다."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"YAML 파일을 찾을 수 없습니다: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
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
            "category": category_str
        }
        
        doc = Document(page_content=text_content, metadata=metadata)
        documents.append(doc)
        
    # 만약 본문이 너무 길 경우를 대비해 스플리터 적용
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        length_function=len
    )
    
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
    # ALLOW_RESET=TRUE 환경 변수가 켜져있다면, 기존 컬렉션을 삭제하고 새로 만들 수 있습니다.
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
        print(f"기존 컬렉션 '{COLLECTION_NAME}'을(를) 삭제하고 새로 생성합니다.")
    except Exception:
        pass # 컬렉션이 없으면 무시
        
    vectorstore = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        client=chroma_client,
        collection_name=COLLECTION_NAME
    )
    
    print(f"✅ ChromaDB 적재 완료! (Collection: {COLLECTION_NAME})")

def main():
    print("🚀 정책 데이터 파싱 및 임베딩 파이프라인 시작")
    try:
        # 1. 데이터 로드
        raw_data = load_yaml_data(YAML_FILE_PATH)
        print(f"YAML 파일 로드 완료: {len(raw_data)} 개의 조항 발견")
        
        # 2. 전처리 및 Document 변환
        documents = process_documents(raw_data)
        print(f"텍스트 분할 완료: {len(documents)} 개의 청크 생성")
        
        # 3. DB 적재
        ingest_to_chroma(documents)
        
    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    main()
