import { useEffect, useRef, useState } from 'react'
import { checkOllama, judge, MODEL, OLLAMA, streamChat, type Judgement, type OllamaStatus } from './ollama'
import { buildPrompt, kstNow, loadDocs, loadEmbedder, search, SETTINGS, type Doc, type Hit, type LoadProgress, type SearchResult } from './rag'

type Stage = 'embed' | 'search' | 'generate' | 'judge' | 'done' | 'stopped' | 'error'
const STAGES: { id: Stage; label: string }[] = [
  { id: 'embed', label: '질문 임베딩' },
  { id: 'search', label: '하이브리드 검색' },
  { id: 'generate', label: '답변 생성' },
  { id: 'judge', label: '판정' },
]

interface Turn {
  id: number
  question: string
  askedAt: string
  stage: Stage
  answer: string
  result?: SearchResult
  judgement?: Judgement
  judgeError?: string
  error?: string
  feedback?: 'up' | 'down'
}

const EXAMPLES = [
  '워런 버핏의 최대 보유 종목은?',
  '빌 애크먼이 이번 분기에 새로 산 종목은?',
  '여러 투자자가 함께 새로 매수한 종목은?',
  '13F는 언제까지 제출해야 해?',
  '애플 지금 사도 돼?',
  '내일 날씨 어때?',
]
const LOG_KEY = '13f-rag-log'
// 모델이 [SEC-020] 대신 [SEC-020 | 섹션]처럼 섹션까지 붙여 인용하는 경우도 인용으로 봅니다
const CITE = /\[(SEC-\d{3})[^\]\n]*\]/g

type ModelState =
  | { kind: 'loading'; files: LoadProgress[] }
  | { kind: 'ready'; cached: boolean; docs: number }
  | { kind: 'error'; message: string }

export default function Chat() {
  const [model, setModel] = useState<ModelState>({ kind: 'loading', files: [] })
  const [ollama, setOllama] = useState<OllamaStatus | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [modal, setModal] = useState<Hit | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const busy = turns.some((t) => ['embed', 'search', 'generate', 'judge'].includes(t.stage))

  function loadModel() {
    setModel({ kind: 'loading', files: [] })
    let files: LoadProgress[] = []
    Promise.all([
      loadEmbedder((fs) => {
        files = fs
        setModel({ kind: 'loading', files: fs })
      }),
      loadDocs(),
    ])
      .then(([, docs]) => setModel({ kind: 'ready', cached: files.length > 0 && files.every((f) => f.cached), docs: docs.length }))
      .catch((e) => setModel({ kind: 'error', message: String(e?.message ?? e) }))
  }

  const recheck = () => { setOllama(null); checkOllama().then(setOllama) }

  useEffect(() => {
    loadModel()
    checkOllama().then(setOllama)
  }, [])

  function update(id: number, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) {
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...(typeof patch === 'function' ? patch(t) : patch) } : t)))
  }

  async function ask(question: string) {
    question = question.trim()
    if (!question || busy) return
    setInput('')
    const id = Date.now()
    setTurns((ts) => [...ts, { id, question, askedAt: kstNow(), stage: 'embed', answer: '' }])
    const controller = new AbortController()
    abortRef.current = controller

    let result: SearchResult
    let answer = ''
    try {
      const status = await checkOllama()
      setOllama(status)
      if (!status.ok) throw new Error(status.reason === 'no-model' ? `Ollama에 ${MODEL} 모델이 없습니다.` : 'Ollama에 연결할 수 없습니다.')

      const [embed, docs] = await Promise.all([loadEmbedder(), loadDocs()])
      const [qvec] = await embed([question])
      update(id, { stage: 'search' })
      result = search(docs, qvec, question)
      update(id, { stage: 'generate', result })

      await streamChat(buildPrompt(question, result), controller.signal, (tok) => {
        answer += tok
        update(id, (t) => ({ answer: t.answer + tok }))
      })
    } catch (e) {
      const aborted = controller.signal.aborted
      // 이미 받은 답변과 검색 근거는 지우지 않습니다
      update(id, { stage: aborted ? 'stopped' : 'error', error: aborted ? undefined : String((e as Error)?.message ?? e) })
      if (aborted && answer) saveLog({ question, answer, stage: 'stopped' })
      return
    }

    update(id, { stage: 'judge' })
    const evidence = result.hits.map((h) => `[${h.chunk.id}] ${h.chunk.text}`).join('\n')
    try {
      const judgement = await judge(question, evidence, answer, controller.signal)
      update(id, { stage: 'done', judgement })
      saveLog({ question, answer, judgement, result })
    } catch (e) {
      // 판정이 실패해도 답변과 출처는 그대로 둡니다
      const judgeError = String((e as Error)?.message ?? e)
      update(id, { stage: 'done', judgeError })
      saveLog({ question, answer, judgeError, result })
    }
  }

  function setFeedback(id: number, fb: 'up' | 'down') {
    update(id, (t) => ({ feedback: t.feedback === fb ? undefined : fb }))
    const t = turns.find((x) => x.id === id)
    if (t) saveLog({ question: t.question, feedback: t.feedback === fb ? null : fb })
  }

  return (
    <section className="chat">
      <StatusBar model={model} ollama={ollama} onRetryModel={loadModel} onRecheck={recheck} />

      <div className="turns">
        {turns.length === 0 && (
          <div className="card panel">
            <p className="muted small">
              추적 중인 16명의 최신 13F와 13F 제도 설명({model.kind === 'ready' ? `${model.docs}개 조각` : '조각'})에서만 답합니다.
              답변 안의 [SEC-xxx]와 아래 출처 칩을 누르면 근거 원문을 확인할 수 있습니다. 종목 추천이나 실시간 주가는 답하지 않습니다.
            </p>
            <div className="chips examples">
              {EXAMPLES.map((q) => (
                <button key={q} className="chip button" onClick={() => ask(q)} disabled={busy}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t) => (
          <TurnView key={t.id} turn={t} onOpen={setModal} onFeedback={(fb) => setFeedback(t.id, fb)} />
        ))}
      </div>

      <form className="ask" onSubmit={(e) => { e.preventDefault(); ask(input) }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="예: 드러켄밀러가 전량 매도한 종목은?" disabled={busy} />
        {busy ? (
          <button type="button" className="stop" onClick={() => abortRef.current?.abort()}>중지</button>
        ) : (
          <button type="submit" disabled={!input.trim()}>질문</button>
        )}
      </form>
      <div className="row small muted">
        <span>엔진: 로컬 Ollama {MODEL} · 검색: 코사인 상위 {SETTINGS.vectorK} + BM25 상위 {SETTINGS.bm25K} · 약한 근거 기준 {SETTINGS.weakThreshold}</span>
        <button className="link" onClick={exportLog}>실험 기록 내보내기 (JSON)</button>
      </div>

      {modal && <SourceModal hit={modal} onClose={() => setModal(null)} />}
    </section>
  )
}

function StatusBar({ model, ollama, onRetryModel, onRecheck }: {
  model: ModelState; ollama: OllamaStatus | null; onRetryModel: () => void; onRecheck: () => void
}) {
  let modelText: string
  if (model.kind === 'ready') modelText = `임베딩 모델 준비 완료${model.cached ? ' (캐시에서 불러옴)' : ''}`
  else if (model.kind === 'error') modelText = `임베딩 모델 오류: ${model.message}`
  else {
    const f = model.files[model.files.length - 1]
    modelText = !f ? '임베딩 모델 준비 중…'
      : f.cached ? `임베딩 모델을 캐시에서 불러오는 중… (${f.file})`
      : `임베딩 모델 내려받는 중… ${f.file} ${(f.loaded / 1e6).toFixed(0)}${f.total ? ` / ${(f.total / 1e6).toFixed(0)}` : ''}MB (첫 방문 1회, 전체 약 200MB)`
  }
  return (
    <div className="status">
      <div className={`banner small ${model.kind === 'error' ? 'error' : model.kind === 'ready' ? 'ok' : ''}`}>
        {modelText}
        {model.kind === 'error' && <button className="link" onClick={onRetryModel}> 다시 시도</button>}
      </div>
      {ollama && !ollama.ok && (
        <div className="banner error small">
          {ollama.reason === 'no-model' ? (
            <>Ollama는 실행 중이지만 <code>{MODEL}</code> 모델이 없습니다. 터미널에서 <code>ollama pull {MODEL}</code>를 실행하세요.</>
          ) : (
            <>
              로컬 Ollama({OLLAMA})에 연결할 수 없습니다. Ollama를 실행하세요.
              {location.hostname !== 'localhost' && <> 배포 주소에서 쓰려면 <code>OLLAMA_ORIGINS</code>에 <code>{location.origin}</code>을 허용한 뒤 Ollama를 재시작해야 합니다.</>}
            </>
          )}
          <button className="link" onClick={onRecheck}> 다시 확인</button>
        </div>
      )}
      {ollama?.ok && <div className="banner ok small">Ollama 연결됨 · {MODEL}</div>}
      <p className="muted small">Chrome·Edge를 권장합니다. Safari에서는 Ollama 연결과 임베딩(WASM)이 불안정할 수 있습니다.</p>
    </div>
  )
}

function TurnView({ turn, onOpen, onFeedback }: { turn: Turn; onOpen: (h: Hit) => void; onFeedback: (fb: 'up' | 'down') => void }) {
  const hits = turn.result?.hits ?? []
  const cited = new Set([...turn.answer.matchAll(CITE)].map((m) => m[1]))
  const activeIdx = STAGES.findIndex((s) => s.id === turn.stage)
  return (
    <article className="turn">
      <div className="q"><strong>{turn.question}</strong> <span className="muted small">{turn.askedAt}</span></div>
      <div className="card panel">
        <ol className="stages small">
          {STAGES.map((s, i) => (
            <li key={s.id} className={activeIdx === -1 || i < activeIdx ? 'done' : i === activeIdx ? 'active' : ''}>{s.label}</li>
          ))}
        </ol>
        {turn.result?.weak && (
          <div className="banner warn small">
            약한 근거: 가장 가까운 조각의 유사도가 {turn.result.topScore.toFixed(2)}로 기준({SETTINGS.weakThreshold})보다 낮습니다. 답변을 그대로 믿기 전에 출처를 확인하세요.
          </div>
        )}
        <div className="answer">
          {renderAnswer(turn.answer, hits, onOpen)}
          {turn.stage === 'generate' && <span className="cursor">▍</span>}
        </div>
        {turn.stage === 'stopped' && <p className="muted small">생성을 중지했습니다. 받은 부분까지 보존합니다.</p>}
        {turn.error && <div className="banner error small">{turn.error}</div>}

        {hits.length > 0 && (
          <details className="sources" open={cited.size === 0}>
            <summary className="small">검색 근거 {hits.length}개 (답변에서 인용: {cited.size}개)</summary>
            <div className="chips">
              {hits.map((h) => (
                <button key={h.chunk.id} className={`chip source${cited.has(h.chunk.id) ? ' cited' : ''}`} onClick={() => onOpen(h)}>
                  <b>{h.chunk.id}</b> {h.chunk.section} · <span className={`method ${h.method}`}>{h.method}</span> {h.score.toFixed(2)}
                </button>
              ))}
            </div>
          </details>
        )}

        {(turn.judgement || turn.judgeError || turn.stage === 'judge') && <JudgeBadge turn={turn} />}
        {['done', 'stopped'].includes(turn.stage) && (
          <div className="row feedback small">
            <span className="muted">이 답변이 도움이 됐나요?</span>
            <button className={turn.feedback === 'up' ? 'on' : ''} onClick={() => onFeedback('up')} aria-label="좋아요">👍</button>
            <button className={turn.feedback === 'down' ? 'on' : ''} onClick={() => onFeedback('down')} aria-label="싫어요">👎</button>
          </div>
        )}
      </div>
    </article>
  )
}

function renderAnswer(text: string, hits: Hit[], onOpen: (h: Hit) => void) {
  return text.split(/(\[SEC-\d{3}[^\]\n]*\])/g).map((part, i) => {
    const m = part.match(/^\[(SEC-\d{3})[^\]\n]*\]$/)
    const hit = m && hits.find((h) => h.chunk.id === m[1])
    if (!m) {
      // 모델이 쓰는 **굵게** 표시만 살립니다
      return <span key={i}>{part.split(/\*\*(.+?)\*\*/g).map((s, j) => (j % 2 ? <strong key={j}>{s}</strong> : s))}</span>
    }
    return hit
      ? <button key={i} className="cite" onClick={() => onOpen(hit)}>{m[1]}</button>
      : <span key={i} className="cite missing" title="검색 근거에 없는 ID입니다">{m[1]}</span>
  })
}

function JudgeBadge({ turn }: { turn: Turn }) {
  if (turn.stage === 'judge') return <div className="judge small muted">판정 중…</div>
  if (turn.judgeError) return <div className="judge small"><span className="tag bad">judgeError</span> 판정에 실패했습니다: {turn.judgeError}</div>
  const j = turn.judgement!
  const flag = (k: keyof Judgement, label: string) => (
    <span className={`tag ${j[k] ? 'good' : 'bad'}`}>{label}:{String(j[k])}</span>
  )
  return (
    <div className="judge small">
      {/* score는 grounded·cited를 반영하므로 정당한 거부는 낮게 나옵니다. 실패처럼 보이지 않게 따로 표시합니다 */}
      <span className={`score ${j.refusal ? 'refusal' : j.score >= 70 ? 'good' : j.score >= 40 ? 'mid' : 'bad'}`}>
        {j.score}{j.refusal && ' · 정당한 거부'}
      </span>
      {flag('grounded', 'grounded')}
      {flag('noHalluc', 'noHalluc')}
      {flag('cited', 'cited')}
      <span className={`tag ${j.refusal ? 'refusal' : ''}`}>refusal:{String(j.refusal)}</span>
      <span className="muted">{j.comment}</span>
      <span className="muted note">로컬 자기평가(같은 2B 모델)라 독립 평가가 아닙니다.</span>
    </div>
  )
}

function SourceModal({ hit, onClose }: { hit: Hit; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const c: Doc = hit.chunk
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="row">
          <strong>{c.id}</strong>
          <span className="muted small">{c.section}</span>
          <span className={`method ${hit.method} small`}>{hit.method} {hit.score.toFixed(3)}</span>
          <button className="link close" onClick={onClose}>닫기</button>
        </div>
        <p>{c.text}</p>
        <a href={c.url} target="_blank" rel="noreferrer">원문 열기 ↗</a>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- 실험 기록 (이 브라우저에만 저장)

function saveLog(entry: Record<string, unknown>) {
  try {
    const log = JSON.parse(localStorage.getItem(LOG_KEY) ?? '[]')
    const result = entry.result as SearchResult | undefined
    log.push({
      at: new Date().toISOString(), settings: { ...SETTINGS, model: MODEL }, ...entry,
      result: result && { topScore: result.topScore, weak: result.weak, hits: result.hits.map((h) => `${h.chunk.id}:${h.method}:${h.score.toFixed(3)}`) },
    })
    localStorage.setItem(LOG_KEY, JSON.stringify(log))
  } catch { /* 저장소를 못 쓰면 기록하지 않습니다 */ }
}

function exportLog() {
  let data = '[]'
  try { data = localStorage.getItem(LOG_KEY) ?? '[]' } catch { /* 비어 있는 기록 */ }
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([data], { type: 'application/json' }))
  a.download = `rag-log-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}
