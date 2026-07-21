"""
CLI 챗봇 — 비용 최적화 버전
============================
설치: pip install -r requirements.txt
실행: python chatbot_cli.py
환경변수: ANTHROPIC_API_KEY 필요 (또는 `ant auth login`으로 로그인)

비용 최적화 포인트:
  1. 시스템 프롬프트에 prompt caching 적용 → 재사용 시 최대 90% 절감
  2. 작업 난이도에 따라 모델 자동 선택 (haiku / sonnet / opus)
  3. output_config.effort 로 추론 깊이 조절 (낮을수록 저렴하고 빠름)
  4. 스트리밍으로 안정적인 응답 수신 (타임아웃/재시도 비용 방지)
  5. 매 응답마다 실제 사용량과 예상 비용을 출력
"""

from __future__ import annotations

import anthropic

from cost_utils import client, pick_model, report_usage

SYSTEM_PROMPT = "당신은 친절하고 정확한 한국어 AI 어시스턴트입니다. 답변은 간결하게 작성하세요."


class ChatBot:
    def __init__(self, task_level: str = "balanced", effort: str = "medium"):
        self.model = pick_model(task_level)
        self.effort = effort
        self.messages: list[dict] = []

    def send(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        full_response = ""
        with client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # 시스템 프롬프트 캐싱
                }
            ],
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},  # low/medium/high — 비용·품질 균형
            messages=self.messages,
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                full_response += text
            final_message = stream.get_final_message()

        self.messages.append({"role": "assistant", "content": full_response})
        report_usage(self.model, final_message.usage)
        return full_response


if __name__ == "__main__":
    bot = ChatBot(task_level="balanced")  # simple | balanced | complex
    print(f"모델: {bot.model} (비용 최적화 모드 — 종료하려면 'exit' 입력)")

    while True:
        try:
            user_input = input("\n\n사용자: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ("exit", "quit", "종료"):
            break
        if not user_input.strip():
            continue

        print("\nClaude: ", end="")
        try:
            bot.send(user_input)
        except anthropic.RateLimitError:
            print("요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
        except anthropic.AuthenticationError:
            print("API 키가 유효하지 않습니다. ANTHROPIC_API_KEY를 확인하세요.")
        except anthropic.APIStatusError as e:
            print(f"API 오류: {e.status_code} - {e.message}")
