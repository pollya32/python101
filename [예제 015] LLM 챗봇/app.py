"""
FastAPI 웹 챗봇 서버 (SSE 스트리밍)
=====================================
설치: pip install -r requirements.txt
실행: uvicorn app:app --reload
접속: http://localhost:8000

프런트엔드에서 task_level(simple/balanced/complex)을 선택해
비용 최적화 라우팅을 직접 체험할 수 있습니다.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from cost_utils import client, pick_model

app = FastAPI(title="LLM 챗봇 (비용 최적화)")

SYSTEM_PROMPT = "당신은 친절하고 정확한 한국어 AI 어시스턴트입니다."

# 세션별 대화 히스토리 (메모리 저장 — 운영 환경에서는 Redis/DB 등으로 교체 권장)
SESSIONS: dict[str, list[dict]] = {}

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.post("/chat/{session_id}")
async def chat(session_id: str, request: Request) -> StreamingResponse:
    body = await request.json()
    user_input: str = body["message"]
    task_level: str = body.get("task_level", "balanced")

    history = SESSIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": user_input})

    model = pick_model(task_level)

    def event_stream():
        full_response = ""
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=history,
        ) as stream:
            for text in stream.text_stream:
                full_response += text
                yield f"data: {json.dumps({'text': text})}\n\n"
            final = stream.get_final_message()

        history.append({"role": "assistant", "content": full_response})
        yield (
            "data: "
            + json.dumps(
                {
                    "done": True,
                    "model": model,
                    "usage": {
                        "input": final.usage.input_tokens,
                        "output": final.usage.output_tokens,
                        "cache_read": final.usage.cache_read_input_tokens or 0,
                    },
                }
            )
            + "\n\n"
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
