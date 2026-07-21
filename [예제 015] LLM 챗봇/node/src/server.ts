/**
 * Express 웹 챗봇 서버 (SSE 스트리밍, TypeScript)
 * 설치: npm install
 * 실행: npm run server
 * 접속: http://localhost:3000
 *
 * 정적 파일(HTML)은 Python 버전의 ../templates/index.html 을 그대로 재사용합니다.
 */

import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import { pickModel } from "./costUtils.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(express.json());

const client = new Anthropic();
const SYSTEM_PROMPT = "당신은 친절하고 정확한 한국어 AI 어시스턴트입니다.";

// 세션별 대화 히스토리 (메모리 저장 — 운영 환경에서는 Redis/DB 등으로 교체 권장)
const sessions = new Map<string, Anthropic.MessageParam[]>();

app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "..", "..", "templates", "index.html"));
});

app.post("/chat/:sessionId", async (req, res) => {
  const { sessionId } = req.params;
  const { message, task_level: taskLevel = "balanced" } = req.body as {
    message: string;
    task_level?: string;
  };

  const history = sessions.get(sessionId) ?? [];
  history.push({ role: "user", content: message });
  sessions.set(sessionId, history);

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");

  const model = pickModel(taskLevel);
  const stream = client.messages.stream({
    model,
    max_tokens: 4096,
    system: [
      { type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } },
    ],
    thinking: { type: "adaptive" },
    output_config: { effort: "medium" },
    messages: history,
  });

  let fullResponse = "";
  stream.on("text", (delta) => {
    fullResponse += delta;
    res.write(`data: ${JSON.stringify({ text: delta })}\n\n`);
  });

  const finalMessage = await stream.finalMessage();
  history.push({ role: "assistant", content: fullResponse });

  res.write(
    `data: ${JSON.stringify({
      done: true,
      model,
      usage: {
        input: finalMessage.usage.input_tokens,
        output: finalMessage.usage.output_tokens,
        cache_read: finalMessage.usage.cache_read_input_tokens ?? 0,
      },
    })}\n\n`,
  );
  res.end();
});

app.listen(3000, () => console.log("서버 실행: http://localhost:3000"));
