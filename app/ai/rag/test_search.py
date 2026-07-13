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
    
    # 4. 유사도 검색 (Similarity Search) 수행
    # 후보군을 10개 넉넉하게 추출하여 점수(Score)와 함께 반환 (ChromaDB의 기본 점수는 L2 거리이므로 작을수록 유사함)
    results_with_scores = vectorstore.similarity_search_with_score(query, k=10)
    
    if not results_with_scores:
        print("❌ 검색 결과가 없습니다.")
        return

    # 5. 권위 등급(authority_level)에 따른 가중치(Re-ranking) 적용
    final_results = []
    for doc, score in results_with_scores:
        level = doc.metadata.get("authority_level", "None")
        
        # 권위 등급에 따른 거리(Distance) 조정 가중치 (값이 작아져야 상위 노출)
        if level == "Statute":
            weight = 0.7   # 법령: 매우 강력한 가산점 (-30% 거리 축소)
        elif level == "Contract":
            weight = 0.85  # 약관: 강력한 가산점 (-15% 거리 축소)
        elif level == "Policy":
            weight = 1.0   # 운영정책: 기본값
        elif level == "Guideline":
            weight = 1.15  # 가이드라인: 감점 (+15% 거리 증가)
        else:
            weight = 1.0
            
        adjusted_score = score * weight
        final_results.append((doc, adjusted_score, score, level))
        
    # 최종 조정된 점수를 기준으로 오름차순 정렬 (값이 작을수록 상위 노출)
    final_results.sort(key=lambda x: x[1])
    
    # 상위 5개 추출
    top_5_results = final_results[:5]

    # 6. 결과 출력
    for i, (doc, adjusted_score, original_score, level) in enumerate(top_5_results, 1):
        print(f"\n================ [결과 {i}] ================")
        print(f"🏢 플랫폼: {doc.metadata.get('platform', 'N/A')}")
        print(f"📂 카테고리: {doc.metadata.get('category', 'N/A')}")
        print(f"⚖️ 권위 등급: {level} (원래 점수: {original_score:.4f} -> 최종 점수: {adjusted_score:.4f})")
        print(f"📖 문서 조항: {doc.metadata.get('doc_title', 'N/A')} - {doc.metadata.get('clause_ref', '')}")
        print(f"\n📝 내용:\n{doc.page_content}")
        print("============================================")

if __name__ == "__main__":
    print("🚀 ChromaDB RAG 검색 테스트 시작")
    
    # 여기에 다양한 질문을 넣어서 테스트해 보세요!
    test_search("청약철회 가능한 기한")
