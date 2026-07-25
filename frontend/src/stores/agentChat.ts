import { defineStore } from 'pinia'
import type { Entity } from '../services/api'

export type ChatPosition = { x: number; y: number }
export type AgentChatWindow = {
  id: string
  agent: Entity
  minimized: boolean
  position: ChatPosition
  zIndex: number
}
export type BackgroundAgentTask = {
  id: string
  conversationId: string
  agent: Entity
  input: string
  status: 'running' | 'completed' | 'failed'
  runId: string
  startedAt: string
  finishedAt?: string
  knowledgeBaseNames: string[]
  detail: string
}

function savedPosition(agentId: string, index: number): ChatPosition {
  try {
    const saved = JSON.parse(localStorage.getItem('evoagent-chat-window-positions') || '{}')
    const position = saved[agentId]
    if (Number.isFinite(position?.x) && Number.isFinite(position?.y)) return position
  } catch {
    // Ignore stale local UI state.
  }
  const windowWidth = Math.min(1120, window.innerWidth - 40)
  const windowHeight = Math.min(740, window.innerHeight - 40)
  const offset = (index % 6) * 28
  return {
    x: Math.min(28 + offset, Math.max(12, window.innerWidth - windowWidth - 12)),
    y: Math.min(76 + offset, Math.max(64, window.innerHeight - windowHeight - 12)),
  }
}

function persistPosition(agentId: string, position: ChatPosition) {
  let saved: Record<string, ChatPosition> = {}
  try {
    saved = JSON.parse(localStorage.getItem('evoagent-chat-window-positions') || '{}')
  } catch {
    // Replace invalid local UI state.
  }
  saved[agentId] = position
  localStorage.setItem('evoagent-chat-window-positions', JSON.stringify(saved))
}

export const useAgentChatStore = defineStore('agent-chat', {
  state: () => ({
    windows: [] as AgentChatWindow[],
    zCounter: 920,
    tasks: [] as BackgroundAgentTask[],
  }),
  getters: {
    open: state => state.windows.length > 0,
    minimizedWindows: state => state.windows.filter(item => item.minimized),
    expandedWindows: state => state.windows.filter(item => !item.minimized),
    runningTasks: state => state.tasks.filter(item => item.status === 'running'),
  },
  actions: {
    openAgent(agent: Entity) {
      const existing = this.windows.find(item => item.agent.id === agent.id)
      if (existing) {
        existing.agent = agent
        existing.minimized = false
        this.focus(existing.id)
        return existing.id
      }
      this.zCounter += 1
      const id = `agent-window:${agent.id}`
      this.windows.push({
        id,
        agent,
        minimized: false,
        position: savedPosition(agent.id, this.windows.length),
        zIndex: this.zCounter,
      })
      return id
    },
    minimize(id: string) {
      const item = this.windows.find(windowItem => windowItem.id === id)
      if (item) item.minimized = true
    },
    minimizeAll() {
      this.windows.forEach(item => { item.minimized = true })
    },
    restore(id: string) {
      const item = this.windows.find(windowItem => windowItem.id === id)
      if (!item) return
      item.minimized = false
      this.focus(id)
    },
    restoreAll() {
      this.windows.forEach(item => {
        item.minimized = false
        this.zCounter += 1
        item.zIndex = this.zCounter
      })
    },
    closeWindow(id: string) {
      this.windows = this.windows.filter(item => item.id !== id)
    },
    focus(id: string) {
      const item = this.windows.find(windowItem => windowItem.id === id)
      if (!item) return
      this.zCounter += 1
      item.zIndex = this.zCounter
    },
    move(id: string, position: ChatPosition) {
      const item = this.windows.find(windowItem => windowItem.id === id)
      if (!item) return
      item.position = position
      persistPosition(item.agent.id, position)
    },
    trackTask(task: Pick<BackgroundAgentTask, 'conversationId' | 'agent' | 'input'>) {
      const id = `${task.conversationId}:${Date.now()}`
      this.tasks.unshift({
        ...task,
        id,
        status: 'running',
        runId: '',
        startedAt: new Date().toISOString(),
        knowledgeBaseNames: [],
        detail: '任务正在后台执行',
      })
      this.tasks = this.tasks.slice(0, 30)
      return id
    },
    updateTask(id: string, patch: Partial<BackgroundAgentTask>) {
      const task = this.tasks.find(item => item.id === id)
      if (task) Object.assign(task, patch)
    },
    finishTask(
      id: string,
      status: 'completed' | 'failed',
      patch: Partial<BackgroundAgentTask> = {},
    ) {
      this.updateTask(id, {
        ...patch,
        status,
        finishedAt: new Date().toISOString(),
      })
    },
    restoreTask(task: BackgroundAgentTask) {
      this.openAgent(task.agent)
    },
  },
})
