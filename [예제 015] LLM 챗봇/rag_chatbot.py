"""
RAG(검색 증강 생성) 챗봇 예제
================================
로컬 문서(docs_sample/*.txt)에서 질문과 관련된 내용을 TF-IDF 유사도로 검색한 뒤,
그 내용만을 근거로 Claude가 답변하도록 합니다. (환각 방지 + 최신/사내 정보 반영)

설치: pip install -r requirements.txt
실행: python rag_chatbot.py

비용 최적화: 검색된 문서 컨텍스트를 시스템 프롬프트에 캐싱하여, 같은 문서 세트로
반복 질문할 때 입력 토큰 비용을 크게 줄입니다.
"""

from __future__ import annotations

import glob
import os

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from cost_utils import client

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs_sample")


class DocumentStore:
    """텍스트 파일들을 로드하고 TF-IDF 기반 유사도 검색을 제공합니다."""

    def __init__(self, docs_dir: str = DOCS_DIR):
        self.paths = sorted(glob.glob(os.path.join(docs_dir, "*.txt")))
        self.texts = [open(p, encoding="utf-8").read() for p in self.paths]
        self.vectorizer = TfidfVectorizer()
        self.matrix = self.vectorizer.fit_transform(self.texts) if self.texts else None

    def search(self, query: str, top_k: int = 2) -> list[str]:
        if not self.texts:
            return []
        query_vec = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self.matrix)[0]
        top_indices = scores.argsort()[::-1][:top_k]
        return [self.texts[i] for i in top_indices if scores[i] > 0]


def answer_with_rag(question: str, store: DocumentStore, model: str = "claude-opus-4-8") -> str:
    relevant_docs = store.search(question)
    context = "\n\n---\n\n".join(relevant_docs) or "관련 문서를 찾지 못했습니다."

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": (
                    "다음은 검색된 문서 내용입니다. 이 내용을 근거로만 답변하세요. "
                    "문서에 없는 내용에 대해 질문받으면 '문서에서 찾을 수 없습니다'라고 "
                    "답하고 추측하지 마세요.\n\n" + context
                ),
                "cache_control": {"type": "ephemeral"},  # 문서 컨텍스트 캐싱
            }
        ],
        messages=[{"role": "user", "content": question}],
    )
    return next(b.text for b in response.content if b.type == "text")


if __name__ == "__main__":
    store = DocumentStore()
    questions = [
        "환불은 며칠 안에 신청해야 100% 받을 수 있어?",
        "프로 플랜은 한 달에 얼마야?",
        "이 서비스는 배송도 해주나요?",  # 문서에 없는 질문 → 환각 방지 테스트
    ]
    for q in questions:
        print(f"Q: {q}")
        print(f"A: {answer_with_rag(q, store)}\n")
