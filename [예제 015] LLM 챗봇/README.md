# LLM 챗봇 (Claude API) — 비용 최적화 포함

Anthropic Claude API를 이용한 LLM 챗봇 환경 구성 예제입니다. 기본 CLI 챗봇부터
웹 UI, 도구 호출(Tool Use), 문서 기반 RAG, Node.js/TypeScript 버전까지 포함하며,
모든 예제에 비용 최적화 기법이 적용되어 있습니다.

## 준비

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-api-key"   # 또는 `ant auth login`
```

## 파일 구성

| 파일 | 설명 |
|---|---|
| `cost_utils.py` | 비용 최적화 공용 유틸 (모델 라우팅, 토큰 계산, 사용량 리포트) |
| `chatbot_cli.py` | 기본 CLI 챗봇 (스트리밍) |
| `app.py` + `templates/index.html` | FastAPI 웹 챗봇 (SSE 스트리밍 UI) |
| `tools_chatbot.py` | Tool Use 예제 (Tool Runner로 함수 자동 호출) |
| `rag_chatbot.py` + `docs_sample/` | 로컬 문서 기반 RAG 챗봇 (TF-IDF 검색) |
| `node/` | 동일 기능의 Node.js/TypeScript 버전 |

## 실행

```bash
# CLI 챗봇
python chatbot_cli.py

# 웹 챗봇 (http://localhost:8000)
uvicorn app:app --reload

# Tool Use 예제
python tools_chatbot.py

# RAG 챗봇
python rag_chatbot.py

# Node.js 버전
cd node && npm install
npm run chat      # CLI
npm run server    # 웹 서버 (http://localhost:3000)
```

## 비용 최적화 전략

이 예제 전반에 적용된 5가지 최적화 기법입니다.

### 1. 작업 난이도별 모델 라우팅

모든 요청에 최고 성능 모델(Opus)을 쓰는 대신, 작업 성격에 맞는 모델을 선택합니다.

```python
MODEL_BY_TASK = {
    "simple":   "claude-haiku-4-5",  # 분류, 짧은 Q&A — 가장 저렴
    "balanced": "claude-sonnet-5", # 일반 대화, 요약 — 대부분의 챗봇
    "complex":  "claude-opus-4-8",   # 복잡한 추론, 코딩 — 가장 정확
}
```

가격 차이가 크므로(예: Haiku $1/$5 vs Opus $5/$25 per 1M 토큰), 트래픽이 많은
챗봇일수록 라우팅만으로도 비용을 크게 절감할 수 있습니다.

### 2. 프롬프트 캐싱 (Prompt Caching)

시스템 프롬프트나 RAG로 검색한 문서처럼 반복 재사용되는 컨텍스트에
`cache_control`을 적용하면, 캐시 적중 시 해당 부분은 약 10분의 1 가격으로
처리됩니다.

```python
system=[{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"},
}]
```

`response.usage.cache_read_input_tokens`로 실제 적중 여부를 확인할 수 있습니다.

### 3. effort 파라미터로 추론 깊이 조절

`output_config.effort`(low/medium/high/max)로 모델이 얼마나 깊이 사고할지
조절합니다. 챗봇처럼 실시간 응답이 중요한 경우 `medium` 이하로 설정하면
토큰 사용량과 지연 시간을 줄일 수 있습니다.

### 4. 요청 전 토큰 수 확인

`count_tokens` 엔드포인트로 실제 요청을 보내기 전에 입력 토큰 수(→예상 비용)를
미리 계산할 수 있습니다. 긴 문서를 다루는 배치 작업 전에 특히 유용합니다.

```python
tokens = client.messages.count_tokens(model=model, messages=messages)
```

### 5. 스트리밍으로 안정적인 응답 수신

`max_tokens`가 클 때 스트리밍 없이 요청하면 타임아웃이 발생할 수 있고,
재시도는 곧 비용 낭비로 이어집니다. 이 예제의 모든 챗봇은 스트리밍을 사용합니다.

### (참고) 대량/비실시간 처리 시 — Batch API

실시간 응답이 필요 없는 대량 작업(예: 수천 건의 문서 분류·요약)은
Message Batches API를 사용하면 **표준 가격의 50%**로 처리할 수 있습니다.
사용자와의 실시간 대화가 아닌 백오피스 작업이라면 이 방식을 고려하세요.
