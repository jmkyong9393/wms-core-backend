import os
from dotenv import load_dotenv
import chromadb
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

# 1. 환경 변수 로드 (.env 파일에서 CHROMA_SERVER_PORT 등 불러오기)
load_dotenv()

CHROMA_HOST = os.getenv("CHROMA_SERVER_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_SERVER_PORT", 8000))
COLLECTION_NAME = "wms_return_policies"

def test_search(query: str):
    print(f"\n🔍 검색어: '{query}'")
    
    # 2. ChromaDB 클라이언트 및 임베딩 모델 연결
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    
    # 3. 기존에 적재된 컬렉션에 연결
    vectorstore = Chroma(
        client=chroma_client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings
    )
    
    # 4. 유사도 검색 (Similarity Search) 수행 - 가장 연관성 높은 5개 청크(문서) 추출
    # TO-DO authority_level에 따른 차등 검색 추가 필요
    results = vectorstore.similarity_search(query, k=5)
    
    # 5. 결과 출력
    if not results:
        print("❌ 검색 결과가 없습니다.")
        return
        
    for i, doc in enumerate(results, 1):
        print(f"\n================ [결과 {i}] ================")
        print(f"🏢 플랫폼: {doc.metadata.get('platform', 'N/A')}")
        print(f"📂 카테고리: {doc.metadata.get('category', 'N/A')}")
        print(f"📖 문서 조항: {doc.metadata.get('doc_title', 'N/A')} - {doc.metadata.get('clause_ref', '')}")
        print(f"\n📝 내용:\n{doc.page_content}")
        print("============================================")

if __name__ == "__main__":
    print("🚀 ChromaDB RAG 검색 테스트 시작")
    
    # 여기에 다양한 질문을 넣어서 테스트해 보세요!
    test_search("청약철회 가능한 기한")
