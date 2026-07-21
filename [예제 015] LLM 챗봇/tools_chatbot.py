"""
Tool Use 챗봇 예제
====================
Claude가 필요하다고 판단하면 자동으로 함수를 호출하도록 하는 예제입니다.
Tool Runner(beta)가 "호출 → 실행 → 결과 전달" 루프를 자동으로 처리합니다.

설치: pip install -r requirements.txt
실행: python tools_chatbot.py
"""

from __future__ import annotations

import datetime

from anthropic import beta_tool

from cost_utils import client


@beta_tool
def get_current_time() -> str:
    """현재 날짜와 시간을 반환합니다."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@beta_tool
def calculate(expression: str) -> str:
    """사칙연산 수식을 계산합니다.

    Args:
        expression: 계산할 수식 (예: "12 * (3 + 4)"). 숫자와 + - * / ( ) 공백만 허용됩니다.
    """
    allowed_chars = set("0123456789+-*/(). ")
    if not expression or not set(expression) <= allowed_chars:
        return "오류: 숫자와 사칙연산 기호(+ - * / ( ))만 사용할 수 있습니다."
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:  # noqa: BLE001 - 사용자에게 계산 오류를 그대로 안내
        return f"계산 오류: {e}"


def chat_with_tools(user_input: str, model: str = "claude-opus-4-8") -> str:
    """도구 호출 루프를 자동 처리하며 최종 답변 텍스트를 반환합니다."""
    runner = client.beta.messages.tool_runner(
        model=model,
        max_tokens=4096,
        tools=[get_current_time, calculate],
        messages=[{"role": "user", "content": user_input}],
    )

    final_text = ""
    for message in runner:
        for block in message.content:
            if block.type == "text":
                final_text = block.text
    return final_text


if __name__ == "__main__":
    question = "지금 몇 시야? 그리고 25 곱하기 4는 얼마야?"
    print(f"질문: {question}\n")
    print(chat_with_tools(question))
