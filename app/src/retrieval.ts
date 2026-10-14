// 하이브리드 검색과 프롬프트 조립. 브라우저(rag.ts)와 점검 스크립트(scripts/check-retrieval.ts)가 함께 씁니다.

export interface Doc { id: string; text: string; url: string; section: string; vector: number[] }
export interface Hit { chunk: Doc; score: number; method: 'vector' | 'bm25' }
export interface SearchResult { hits: Hit[]; topScore: number; weak: boolean }

/** 실험에서 바꿀 수 있는 검색 설정 */
export const SETTINGS = { vectorK: 10, bm25K: 5, weakThreshold: 0.55 }

// ---------------------------------------------------------------- 하이브리드 검색

/** 영문·숫자는 단어 단위로, 한글은 조사가 붙어도 맞도록 단어와 두 글자 묶음(bigram)으로 자릅니다. */
export function tokenize(text: string): string[] {
  const out: string[] = []
  for (const w of text.toLowerCase().match(/[a-z0-9.]+|[가-힣]+/g) ?? []) {
    out.push(w)
    if (/[가-힣]/.test(w)) for (let i = 0; i + 1 < w.length; i++) out.push(w.slice(i, i + 2))
  }
  return out
}

function bm25(docs: Doc[], query: string): number[] {
  const k1 = 1.2, b = 0.75
  const toks = docs.map((d) => tokenize(`${d.section} ${d.text}`))
  const avg = toks.reduce((s, t) => s + t.length, 0) / toks.length
  const df = new Map<string, number>()
  for (const t of toks) for (const w of new Set(t)) df.set(w, (df.get(w) ?? 0) + 1)
  const q = [...new Set(tokenize(query))]
  return toks.map((t) => {
    const tf = new Map<string, number>()
    for (const w of t) tf.set(w, (tf.get(w) ?? 0) + 1)
    let s = 0
    for (const w of q) {
      const f = tf.get(w)
      if (!f) continue
      const idf = Math.log(1 + (docs.length - df.get(w)! + 0.5) / (df.get(w)! + 0.5))
      s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + (b * t.length) / avg))
    }
    return s
  })
}

/** 코사인 상위 vectorK개 + 아직 들어오지 않은 BM25 상위 bm25K개. BM25 점수는 이번 검색의 최고점으로 나눠 0~1로 맞춥니다. */
export function search(docs: Doc[], qvec: Float32Array, query: string): SearchResult {
  const cos = docs.map((d) => d.vector.reduce((s, x, i) => s + x * qvec[i], 0))
  const byVec = docs.map((d, i) => ({ chunk: d, score: cos[i], method: 'vector' as const }))
    .sort((a, b) => b.score - a.score)
    .slice(0, SETTINGS.vectorK)
  const taken = new Set(byVec.map((h) => h.chunk.id))
  const raw = bm25(docs, query)
  const max = Math.max(...raw)
  const byBm25 = max > 0
    ? docs.map((d, i) => ({ chunk: d, score: raw[i] / max, method: 'bm25' as const }))
      .filter((h) => h.score > 0 && !taken.has(h.chunk.id))
      .sort((a, b) => b.score - a.score)
      .slice(0, SETTINGS.bm25K)
    : []
  const topScore = byVec[0]?.score ?? 0
  return { hits: [...byVec, ...byBm25], topScore, weak: topScore < SETTINGS.weakThreshold }
}

// ---------------------------------------------------------------- 프롬프트

export const REFUSAL = '제공된 13F 자료로는 답할 수 없습니다.'

export function kstNow(): string {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit',
  }).format(new Date())
}

export function buildPrompt(question: string, result: SearchResult): string {
  const lines = [
    "다음 자료는 미국 SEC 13F 공시(대형 투자자가 분기 말 보유 주식을 신고하는 보고서)와 13F 제도 설명에서 뽑은 조각입니다.",
    '근거 원칙: [자료]에 있는 내용만으로 한국어로 답합니다. 자료에 없는 사실, 수치, 종목, 날짜를 보태지 않습니다.',
    `자료로 답할 수 없는 질문이거나, 종목 추천·매수/매도 판단·주가 예측·실시간 시세를 묻는 질문이면 다른 말 없이 "${REFUSAL}"라고만 답합니다.`,
  ]
  if (result.weak) {
    lines.push('주의: 검색된 조각의 유사도가 낮습니다. 질문과 완전히 맞는 근거가 아닐 수 있으니, 근거에 있는 내용만 짧게 답하고 자료에 없는 부분은 없다고 말합니다.')
  }
  lines.push(
    '근거가 된 조각의 [ID]를 답 안에서 표시합니다. 예: [SEC-001]',
    `현재 시각은 ${kstNow()} (KST)입니다. '지금', '이번 분기', '올해' 같은 상대 표현은 이 시각을 기준으로 해석하되, 13F는 분기 말 기준이며 분기가 끝나고 최대 45일 뒤에 공개된다는 점을 고려합니다.`,
    '[자료]',
    ...result.hits.map((h) => `[${h.chunk.id} | ${h.chunk.section}] ${h.chunk.text}`),
    '[질문]',
    question,
  )
  return lines.join('\n')
}
