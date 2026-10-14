// 문서 임베딩(Node, scripts/embed-docs.ts)과 질문 임베딩(브라우저, rag.ts)이 함께 쓰는 계산 경로입니다.
// 두 쪽이 같은 모델 파일, 같은 토크나이저, 같은 mean pooling·L2 정규화를 거쳐야 코사인 비교가 성립합니다.
//
// 기본 pipeline()의 q4 모델은 WASM ONNX Runtime이 지원하지 않는 GatherBlockQuantized 연산을 써서 실패하므로,
// 토크나이저와 추론 세션을 분리하고 model_no_gather_q4.onnx를 WASM 실행 공급자로 직접 엽니다.
import type { Tokenizer } from '@huggingface/tokenizers'
import type * as Ort from 'onnxruntime-web'

export const MODEL_ID = 'onnx-community/embeddinggemma-300m-ONNX'
export const HF_BASE = `https://huggingface.co/${MODEL_ID}/resolve/main/`
export const FILES = {
  tokenizer: 'tokenizer.json',
  tokenizerConfig: 'tokenizer_config.json',
  model: 'onnx/model_no_gather_q4.onnx',
  modelData: 'onnx/model_no_gather_q4.onnx_data',
} as const
export const DIM = 768
const MAX_TOKENS = 1024
const PAD_ID = 0

export type Embed = (texts: string[]) => Promise<Float32Array[]>

export async function createEmbedder(
  ort: typeof Ort,
  tokenizer: Tokenizer,
  model: Uint8Array,
  modelData: Uint8Array,
): Promise<Embed> {
  const session = await ort.InferenceSession.create(model, {
    executionProviders: ['wasm'],
    externalData: [{ path: 'model_no_gather_q4.onnx_data', data: modelData }],
  })

  return async (texts) => {
    const ids = texts.map((t) => tokenizer.encode(t).ids.slice(0, MAX_TOKENS))
    const len = Math.max(...ids.map((x) => x.length))
    const inputIds = new BigInt64Array(texts.length * len)
    const mask = new BigInt64Array(texts.length * len)
    ids.forEach((row, b) => {
      for (let i = 0; i < len; i++) {
        inputIds[b * len + i] = BigInt(i < row.length ? row[i] : PAD_ID)
        mask[b * len + i] = i < row.length ? 1n : 0n
      }
    })
    const dims = [texts.length, len]
    const out = await session.run({
      input_ids: new ort.Tensor('int64', inputIds, dims),
      attention_mask: new ort.Tensor('int64', mask, dims),
    })
    const hidden = out.last_hidden_state
    const [batch, seq, dim] = hidden.dims as number[]
    const h = hidden.data as Float32Array

    // attention_mask가 1인 실제 토큰만 평균낸 뒤(mean pooling) 길이를 1로 맞춥니다(L2 정규화).
    const vectors: Float32Array[] = []
    for (let b = 0; b < batch; b++) {
      const v = new Float32Array(dim)
      let count = 0
      for (let s = 0; s < seq; s++) {
        if (mask[b * len + s] === 0n) continue
        count++
        const off = (b * seq + s) * dim
        for (let d = 0; d < dim; d++) v[d] += h[off + d]
      }
      let norm = 0
      for (let d = 0; d < dim; d++) {
        v[d] /= Math.max(count, 1)
        norm += v[d] * v[d]
      }
      norm = Math.sqrt(norm) || 1
      for (let d = 0; d < dim; d++) v[d] /= norm
      vectors.push(v)
    }
    return vectors
  }
}
