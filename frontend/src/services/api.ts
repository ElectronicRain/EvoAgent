const API_BASE = import.meta.env.VITE_API_BASE || (
  '__TAURI_INTERNALS__' in window
    ? 'http://127.0.0.1:8000/api'
    : ['5173', '5174'].includes(window.location.port)
      ? `${window.location.protocol}//${window.location.hostname}:8000/api`
      : `${window.location.origin}/api`
)
export const API_ORIGIN = API_BASE.replace(/\/api\/?$/, '')
const AUTH_TOKEN_KEY = 'evoagent-auth-token'

function attachAuth(headers: Headers) {
  const token = window.localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) headers.set('Authorization', `Bearer ${token}`)
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const labels: Record<string, string> = {
      name: '名称', slug: '唯一标识', system_prompt: '系统提示词',
      base_url: 'Base URL', default_model: '默认模型', input: '输入内容',
    }
    return detail.map(item => {
      const field = String(item?.loc?.at?.(-1) || '字段')
      let message = String(item?.msg || '格式不正确')
      message = message
        .replace('Field required', '不能为空')
        .replace(/String should have at least (\d+) characters?/, '至少需要 $1 个字符')
        .replace('String should match pattern', '格式不符合要求')
      return `${labels[field] || field}：${message}`
    }).join('；')
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return `请求失败 (${status})`
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  attachAuth(headers)
  let response: Response | undefined
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      response = await fetch(`${API_BASE}${path}`, {
        cache: 'no-store',
        ...options,
        headers,
      })
      break
    } catch {
      if (attempt === 2) {
        throw new ApiError(0, '本地服务尚未就绪，请稍后重试')
      }
      await new Promise(resolve => window.setTimeout(resolve, 500))
    }
  }
  if (!response) throw new ApiError(0, '本地服务尚未就绪，请稍后重试')
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, errorMessage(body.detail, response.status))
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

async function stream(
  path: string,
  data: unknown,
  onEvent: (event: any) => void,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  attachAuth(headers)
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, errorMessage(body.detail, response.status))
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedDone = false
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      const dataLine = chunk.split('\n').find(line => line.startsWith('data: '))
      if (dataLine) {
        const event = JSON.parse(dataLine.slice(6))
        if (event.type === 'done') receivedDone = true
        onEvent(event)
      }
    }
    if (done) break
  }
  if (!receivedDone) throw new ApiError(0, 'Agent 执行流意外中断，请重试')
}

async function streamEvents(
  path: string,
  data: unknown,
  onEvent: (event: any) => void,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  attachAuth(headers)
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST', headers, body: JSON.stringify(data),
  })
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, errorMessage(body.detail, response.status))
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      const dataLine = chunk.split('\n').find(line => line.startsWith('data: '))
      if (dataLine) onEvent(JSON.parse(dataLine.slice(6)))
    }
    if (done) break
  }
}

async function blob(path: string, data: unknown): Promise<Blob> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  attachAuth(headers)
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, errorMessage(body.detail, response.status))
  }
  return response.blob()
}

export const api = {
  get: <T = any>(path: string) => request<T>(path),
  post: <T = any>(path: string, data?: unknown) => request<T>(path, {
    method: 'POST',
    body: data === undefined ? undefined : JSON.stringify(data),
  }),
  put: <T = any>(path: string, data: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: <T = any>(path: string, data: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: <T = any>(path: string) => request<T>(path, { method: 'DELETE' }),
  stream,
  streamEvents,
  blob,
  upload: <T = any>(path: string, file: File, fields: Record<string, string> = {}) => {
    const form = new FormData()
    form.append('file', file)
    for (const [key, value] of Object.entries(fields)) form.append(key, value)
    return request<T>(path, { method: 'POST', body: form })
  },
  uploadFiles: <T = any>(
    path: string,
    files: File[],
    fields: Record<string, string> = {},
    relativePaths: string[] = [],
  ) => {
    const form = new FormData()
    files.forEach(file => form.append('files', file, file.name))
    if (relativePaths.length) form.append('paths_json', JSON.stringify(relativePaths))
    for (const [key, value] of Object.entries(fields)) form.append(key, value)
    return request<T>(path, { method: 'POST', body: form })
  },
}

export type Entity = Record<string, any>
