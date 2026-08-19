import { api } from './api'

const QUEUE_KEY = 'evoagent-frontend-telemetry-queue-v1'
type EventPayload = {
  event_type: string
  module?: string
  resource_type?: string
  resource_id?: string | null
  success?: boolean
  duration_ms?: number
  detail?: Record<string, unknown>
}

function readQueue(): EventPayload[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(QUEUE_KEY) || '[]')
    return Array.isArray(value) ? value.slice(-500) : []
  } catch {
    return []
  }
}

function writeQueue(values: EventPayload[]) {
  window.localStorage.setItem(QUEUE_KEY, JSON.stringify(values.slice(-500)))
}

async function send(value: EventPayload) {
  await api.post('/telemetry/events', {
    module: 'frontend', success: true, detail: {}, ...value,
  })
}

export const telemetry = {
  async track(value: EventPayload) {
    try {
      await send(value)
    } catch {
      writeQueue([...readQueue(), value])
    }
  },
  async flush() {
    const queue = readQueue()
    if (queue.length) {
      let sent = 0
      for (const value of queue) {
        try { await send(value); sent += 1 } catch { break }
      }
      writeQueue(queue.slice(sent))
    }
    try { return await api.post('/telemetry/sync') } catch { return null }
  },
}
