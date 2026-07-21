/**
 * CLI 챗봇 — 비용 최적화 버전 (TypeScript)
 * 설치: npm install
 * 실행: npm run chat
 * 환경변수: ANTHROPIC_API_KEY 필요
 */

import Anthropic from "@anthropic-ai/sdk";
import readline from "node:readline/promises";
import { pickModel, estimateCost } from "./costUtils.js";

const client = new Anthropic();
const SYSTEM_PROMPT = "당신은 친절하고 정확한 한국어 AI 어시스턴트입니다.";

const messages: Anthropic.MessageParam[] = [];
const model = pickModel("balanced"); // simple | balanced | complex

async function send(userInput: string): Promise<void> {
  messages.push({ role: "user", content: userInput });

  const stream = client.messages.stream({
    model,
    max_tokens: 4096,
    system: [
      { type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } },
    ],
    thinking: { type: "adaptive" },
    output_config: { effort: "medium" },
    messages,
  });

  stream.on("text", (delta) => process.stdout.write(delta));

  const finalMessage = await stream.finalMessage();
  const textBlock = finalMessage.content.find(
    (b): b is Anthropic.TextBlock => b.type === "text",
  );
  messages.push({ role: "assistant", content: textBlock?.text ?? "" });

  const cost = estimateCost(
    model,
    finalMessage.usage.input_tokens,
    finalMessage.usage.output_tokens,
  );
  console.log(
    `\n\n[비용] 모델=${model} 입력=${finalMessage.usage.input_tokens} ` +
      `출력=${finalMessage.usage.output_tokens} 예상비용=$${cost.toFixed(5)}`,
  );
}

async function main(): Promise<void> {
  console.log(`모델: ${model} (비용 최적화 모드 — 종료하려면 'exit' 입력)`);
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

  while (true) {
    const userInput = await rl.question("\n사용자: ");
    if (["exit", "quit", "종료"].includes(userInput.trim().toLowerCase())) break;
    if (!userInput.trim()) continue;

    process.stdout.write("Claude: ");
    await send(userInput);
  }
  rl.close();
}

main();
