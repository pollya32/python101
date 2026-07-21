"""
비용 최적화 공용 유틸리티
=========================
챗봇 스크립트들이 공통으로 사용하는 비용 관련 헬퍼 함수 모음입니다.

적용된 최적화 기법:
  1. 작업 난이도별 모델 자동 라우팅 (simple → Haiku / balanced → Sonnet / complex → Opus)
  2. 요청 전 토큰 수 계산 → 예상 비용 사전 확인
  3. 응답 후 실제 사용량(usage)을 바탕으로 비용 및 캐시 적중률 리포트
"""

from __future__ import annotations

import anthropic

client = anthropic.Anthropic()

# 1M 토큰당 가격 (USD, 2026-07 기준 — 가격은 수시로 바뀌므로 필요시 갱신)
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

# 작업 난이도별 추천 모델 — 비용 최적화의 핵심
MODEL_BY_TASK: dict[str, str] = {
    "simple": "claude-haiku-4-5",  # 분류, 짧은 Q&A, 단순 추출 등
    "balanced": "claude-sonnet-5",  # 일반 대화, 요약 등 대부분의 챗봇 응답
    "complex": "claude-opus-4-8",  # 복잡한 추론, 코딩, 심층 분석
}


def pick_model(task_level: str = "balanced") -> str:
    """작업 난이도에 맞는 모델을 선택합니다."""
    return MODEL_BY_TASK.get(task_level, MODEL_BY_TASK["balanced"])


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """예상 비용(USD)을 계산합니다."""
    price = PRICING.get(model, PRICING["claude-sonnet-5"])
    return (input_tokens / 1_000_000 * price["input"]) + (
        output_tokens / 1_000_000 * price["output"]
    )


def count_tokens(model: str, messages: list[dict], system=None) -> int:
    """요청을 보내기 전에 입력 토큰 수를 미리 확인합니다 (예산 초과 방지용)."""
    result = client.messages.count_tokens(model=model, messages=messages, system=system)
    return result.input_tokens


def report_usage(model: str, usage) -> None:
    """응답의 usage 정보를 바탕으로 실제 비용과 캐시 적중 여부를 출력합니다."""
    cost = estimate_cost(model, usage.input_tokens, usage.output_tokens)
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    print(
        f"\n[비용] 모델={model} | 입력={usage.input_tokens} | 출력={usage.output_tokens} "
        f"| 캐시읽기={cache_read} | 캐시쓰기={cache_write} | 예상비용=${cost:.5f}"
    )
