// public/data/chunks.json의 청크마다 768차원 vector를 붙여 public/data/docs.json(벡터스토어)을 만듭니다.
//   npm run embed
// 모델 파일은 .model-cache/에 한 번만 받아 둡니다. 질문 임베딩(src/rag.ts)과 같은 src/embedCore.ts를 씁니다.
import { Tokenizer } from '@huggingface/tokenizers'
import * as ort from 'onnxruntime-web'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createEmbedder, DIM, FILES, HF_BASE, MODEL_ID } from '../src/embedCore.ts'

const APP = join(dirname(fileURLToPath(import.meta.url)), '..')
const CACHE = join(APP, '.model-cache')
const SRC = join(APP, 'public', 'data', 'chunks.json')
const OUT = join(APP, 'public', 'data', 'docs.json')
const BATCH = 4

interface Chunk { id: string; text: string; url: string; section: string }

async function file(name: string): Promise<Uint8Array> {
  const path = join(CACHE, name.replace('/', '__'))
  if (!existsSync(path)) {
    console.log(`내려받는 중: ${name}`)
    const r = await fetch(HF_BASE + name)
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`)
    mkdirSync(CACHE, { recursive: true })
    writeFileSync(path, new Uint8Array(await r.arrayBuffer()))
  }
  return new Uint8Array(readFileSync(path))
}

const json = async (name: string) => JSON.parse(new TextDecoder().decode(await file(name)))

ort.env.wasm.numThreads = 1
const tokenizer = new Tokenizer(await json(FILES.tokenizer), await json(FILES.tokenizerConfig))
const embed = await createEmbedder(ort, tokenizer, await file(FILES.model), await file(FILES.modelData))

const chunks: Chunk[] = JSON.parse(readFileSync(SRC, 'utf-8'))
const docs = []
for (let i = 0; i < chunks.length; i += BATCH) {
  const batch = chunks.slice(i, i + BATCH)
  const vectors = await embed(batch.map((c) => c.text))
  batch.forEach((c, j) => docs.push({ ...c, vector: Array.from(vectors[j], (x) => Number(x.toFixed(6))) }))
  console.log(`${Math.min(i + BATCH, chunks.length)} / ${chunks.length}`)
}

// 검증: 모든 vector가 768차원이고 길이가 1인지 확인합니다.
for (const d of docs) {
  const norm = Math.sqrt(d.vector.reduce((s: number, x: number) => s + x * x, 0))
  if (d.vector.length !== DIM || Math.abs(norm - 1) > 1e-3) {
    throw new Error(`${d.id}: 차원 ${d.vector.length}, 길이 ${norm.toFixed(4)}`)
  }
}
writeFileSync(OUT, JSON.stringify({ model: MODEL_ID, file: FILES.model, pooling: 'mean', normalize: true, dim: DIM, docs }))
console.log(`저장: ${OUT} (청크 ${docs.length}개, ${DIM}차원 검증 통과)`)
