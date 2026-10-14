// 사용자 컴퓨터의 로컬 Ollama 호출. 배포된 페이지에서도 브라우저가 직접 localhost:11434로 요청하므로
// Ollama 쪽에서 OLLAMA_ORIGINS로 페이지 출처를 허용해야 합니다.
export const OLLAMA = 'http://localhost:11434'
export const MODEL = 'qwen3.5:2b'
const NUM_CTX = 16384 // 검색 조각 15개가 잘리지 않도록 기본값(4096)보다 넓힙니다

export type OllamaStatus = { ok: true } | { ok: false; reason: 'offline' | 'no-model' }

export async function checkOllama(): Promise<OllamaStatus> {
  try {
    const r = await fetch(`${OLLAMA}/api/tags`, { signal: AbortSignal.timeout(3000) })
    if (!r.ok) return { ok: false, reason: 'offline' }
    const { models } = (await r.json()) as { models: { name: string }[] }
    return models.some((m) => m.name === MODEL) ? { ok: true } : { ok: false, reason: 'no-model' }
  } catch {
    return { ok: false, reason: 'offline' }
  }
}

/** /api/chat 스트리밍 응답(줄바꿈으로 구분된 JSON 조각)을 읽어 텍스트 조각마다 onToken을 부릅니다. */
export async function streamChat(prompt: string, signal: AbortSignal, onToken: (t: string) => void): Promise<void> {
  const r = await fetch(`${OLLAMA}/api/chat`, {
    method: 'POST',
    signal,
    body: JSON.stringify({
      model: MODEL, stream: true, think: false,
      options: { num_ctx: NUM_CTX },
      messages: [{ role: 'user', content: prompt }],
    }),
  })
  if (!r.ok || !r.body) throw new Error(`Ollama 응답 오류: HTTP ${r.status}`)
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += value
    const lines = buf.split('\n')
    buf = lines.pop()! // 아직 끝나지 않은 줄은 다음 조각과 합칩니다
    for (const line of lines) {
      if (!line.trim()) continue
      const msg = JSON.parse(line)
      if (msg.error) throw new Error(msg.error)
      if (msg.message?.content) onToken(msg.message.content)
    }
  }
}

// ---------------------------------------------------------------- LLM-as-a-Judge

export interface Judgement {
  grounded: boolean
  noHalluc: boolean
  cited: boolean
  refusal: boolean
  score: number
  comment: string
}

export function judgePrompt(question: string, evidence: string, answer: string): string {
  return `당신은 RAG 챗봇 답변의 평가자입니다. 아래 [질문], [근거자료], [답변]을 읽고 다음 기준으로 JSON만 출력합니다.
grounded: 답변 내용이 근거자료에서 나왔는가 (true/false)
noHalluc: 근거에 없는 사실을 지어내지 않았는가 (true/false)
cited: 답변 안에 근거 조각의 [ID] 표시가 있는가 (true/false)
refusal: 근거에 답이 없어서 '없다'고 답한 경우 true, 그 외 false
score: 0-100 정수 (grounded·noHalluc·cited 반영)
comment: 한두 문장 평어 (한국어)
출력 형식: {"grounded":bool,"noHalluc":bool,"cited":bool,"refusal":bool,"score":int,"comment":"..."} — JSON 외 텍스트 금지.

[질문] ${question}

[근거자료] ${evidence}

[답변] ${answer}`
}

/** 답변 모델과 같은 2B 모델이 판정하므로 독립 평가가 아니라 교육용의 거친 신호입니다. */
export async function judge(question: string, evidence: string, answer: string, signal?: AbortSignal): Promise<Judgement> {
  const r = await fetch(`${OLLAMA}/api/chat`, {
    method: 'POST',
    signal,
    body: JSON.stringify({
      model: MODEL, stream: false, think: false, format: 'json',
      options: { temperature: 0, num_ctx: NUM_CTX },
      messages: [{ role: 'user', content: judgePrompt(question, evidence, answer) }],
    }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const raw = JSON.parse((await r.json()).message.content)
  const bool = (k: string) => {
    if (typeof raw[k] !== 'boolean') throw new Error(`${k} 필드가 없습니다`)
    return raw[k] as boolean
  }
  let score = Number(raw.score)
  if (!Number.isFinite(score)) throw new Error('score 필드가 없습니다')
  if (score <= 5) score = (score / 5) * 100 // 가끔 5점 만점으로 답하는 경우 환산
  return {
    grounded: bool('grounded'), noHalluc: bool('noHalluc'), cited: bool('cited'), refusal: bool('refusal'),
    score: Math.round(Math.min(100, Math.max(0, score))), comment: String(raw.comment ?? ''),
  }
}
