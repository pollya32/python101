/**
 * 비용 최적화 공용 유틸리티 (TypeScript 버전)
 * Python의 cost_utils.py와 동일한 로직입니다.
 */

export const PRICING: Record<string, { input: number; output: number }> = {
  "claude-opus-4-8": { input: 5.0, output: 25.0 },
  "claude-sonnet-5": { input: 3.0, output: 15.0 },
  "claude-haiku-4-5": { input: 1.0, output: 5.0 },
};

export const MODEL_BY_TASK: Record<string, string> = {
  simple: "claude-haiku-4-5",
  balanced: "claude-sonnet-5",
  complex: "claude-opus-4-8",
};

export function pickModel(taskLevel: string = "balanced"): string {
  return MODEL_BY_TASK[taskLevel] ?? MODEL_BY_TASK.balanced;
}

export function estimateCost(model: string, inputTokens: number, outputTokens: number): number {
  const price = PRICING[model] ?? PRICING["claude-sonnet-5"];
  return (inputTokens / 1_000_000) * price.input + (outputTokens / 1_000_000) * price.output;
}
