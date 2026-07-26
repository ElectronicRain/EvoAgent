<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Activity, Bot, Check, ChevronDown, Clock3, Copy, Database, Download,
  ExternalLink, FileText, FolderLock, Files, Globe2, HardDrive, Maximize2,
  MessageSquarePlus, Minimize2, Minus, Save, Send, Settings2, Shield,
  Sparkles, UserRound, X,
} from 'lucide-vue-next'
import DocumentClassroom from './DocumentClassroom.vue'
import RichAgentMessage from './RichAgentMessage.vue'
import StatusBadge from './StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAgentChatStore } from '../stores/agentChat'
import { useAppStore } from '../stores/app'

const chat = useAgentChatStore()
const app = useAppStore()
const props = defineProps<{ windowId: string }>()
const conversations = ref<Entity[]>([])
const activeConversation = ref<Entity | null>(null)
const messages = ref<Entity[]>([])
const steps = ref<Entity[]>([])
const artifacts = ref<Entity[]>([])
const sourceReviews = ref<Entity[]>([])
const selectedWebSource = ref<Entity | null>(null)
const chatInput = ref('')
const chatRunning = ref(false)
const currentRunId = ref('')
const currentRunStatus = ref('idle')
const panelTab = ref<'steps'|'web'|'rag'|'docs'>('steps')
const documentFocus = ref(false)
const fullscreen = ref(false)
const selectedArtifactId = ref('')
const securityRuntime = ref<Entity | null>(null)
const securityProfile = ref('default')
const securityMenu = ref(false)
const messagePane = ref<HTMLElement | null>(null)
const knowledgeBases = ref<Entity[]>([])
const knowledgeGroups = ref<Entity[]>([])
const selectedKnowledgeBases = ref<string[]>([])
const ragSaving = ref(false)
const ragSavedAt = ref('')
const ragForm = reactive({
  enabled: true,
  knowledge_group_ids: [] as string[],
  similarity_threshold: 0,
  dense_weight: 0.65,
  lexical_weight: 0.35,
  candidate_k: 30,
  rerank_k: 12,
  top_k: 6,
  context_char_budget: 12000,
  query_rewrite: true,
  multi_turn: true,
  max_history_messages: 8,
  cross_language: false,
  knowledge_graph: false,
  parent_expansion: true,
  complete_list_expansion: true,
  rerank_model: '',
})
const drag = reactive({ active: false, moved: false, offsetX: 0, offsetY: 0 })
let pollTimer: number | undefined

const chatWindow = computed(() => chat.windows.find(item => item.id === props.windowId) || null)
const agent = computed(() => chatWindow.value?.agent || null)
const agentGeneration = computed(() => parseObject(agent.value?.generation_config_json || '{}'))
const openingMessage = computed(() => agentGeneration.value.opening_message || `你好，我是 ${agent.value?.name || 'Agent'}。告诉我你的目标，我会基于可用证据完成任务。`)
const suggestedQuestions = computed<string[]>(() => agentGeneration.value.suggested_questions || [])
const webEvents = computed(() => steps.value.filter(item => ['web_search_started','web_search_results','research_sources_selected','web_fetch_started','web_page_fetched','web_research_empty'].includes(item.type)))
const activeArtifact = computed(() =>
  artifacts.value.find(item => item.id === selectedArtifactId.value) || artifacts.value[0] || null,
)
const artifactCharacters = computed(() =>
  artifacts.value.reduce((total, item) => total + Number(item.content_characters || String(item.content || '').length), 0),
)
const sourceDecision = computed(() => Object.fromEntries(sourceReviews.value.map(item => [item.url, item.decision])))
const windowStyle = computed(() => {
  if (fullscreen.value) {
    return { left: '0px', top: '0px', width: '100vw', height: '100vh', zIndex: chatWindow.value?.zIndex || 920 }
  }
  const width = Math.min(1400, Math.max(0, window.innerWidth - 24))
  const height = Math.min(880, Math.max(0, window.innerHeight - 24))
  const x = Math.min(
    Math.max(12, chatWindow.value?.position.x || 12),
    Math.max(12, window.innerWidth - width - 12),
  )
  const y = Math.min(
    Math.max(12, chatWindow.value?.position.y || 12),
    Math.max(12, window.innerHeight - height - 12),
  )
  return { left: `${x}px`, top: `${y}px`, zIndex: chatWindow.value?.zIndex || 920 }
})
const securityProfiles = [
  { value: 'default', label: '继承安全治理', description: '使用全局工作区与审批设置', icon: Shield },
  { value: 'read_only', label: '继承范围 · 只读', description: '沿用全局路径范围，只能浏览、搜索和读取', icon: FolderLock },
  { value: 'workspace_ask', label: '工作区 · 逐项确认', description: '授权目录内写入和命令均需确认', icon: Shield },
  { value: 'workspace_auto', label: '工作区 · 自动执行', description: '授权目录内自动完成任务', icon: Shield },
  { value: 'custom_ask', label: '指定项目 · 逐项确认', description: '指定项目路径内变更均需确认', icon: FolderLock },
  { value: 'custom_auto', label: '指定项目 · 自动执行', description: '指定项目路径内自动完成任务', icon: FolderLock },
  { value: 'unrestricted_ask', label: '全盘 · 逐项确认', description: '可访问任意路径，变更前确认', icon: HardDrive },
  { value: 'unrestricted_auto', label: '全盘 · 自动执行', description: '最高权限，请谨慎使用', icon: HardDrive },
]
const activeSecurityProfile = computed(() => securityProfiles.find(item => item.value === securityProfile.value) || securityProfiles[0])

function parse(value: string) { try { return JSON.parse(value || '[]') } catch { return [] } }
function parseObject(value: string) { try { return JSON.parse(value || '{}') } catch { return {} } }
function toggleValue(list: string[], value: string) {
  const index = list.indexOf(value)
  index >= 0 ? list.splice(index, 1) : list.push(value)
}
function loadRagSettings(nextAgent: Entity) {
  Object.assign(ragForm, {
    enabled: true,
    knowledge_group_ids: [],
    similarity_threshold: 0,
    dense_weight: 0.65,
    lexical_weight: 0.35,
    candidate_k: 30,
    rerank_k: 12,
    top_k: 6,
    context_char_budget: 12000,
    query_rewrite: true,
    multi_turn: true,
    max_history_messages: 8,
    cross_language: false,
    knowledge_graph: false,
    parent_expansion: true,
    complete_list_expansion: true,
    rerank_model: '',
    ...parseObject(nextAgent.rag_config_json || '{}'),
  })
  selectedKnowledgeBases.value = parse(nextAgent.knowledge_bases_json || '[]')
  ragSavedAt.value = ''
}
function stopPolling() {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
  pollTimer = undefined
}
function resetConversationState() {
  stopPolling()
  conversations.value = []
  activeConversation.value = null
  messages.value = []
  steps.value = []
  artifacts.value = []
  selectedArtifactId.value = ''
  documentFocus.value = false
  sourceReviews.value = []
  selectedWebSource.value = null
  chatInput.value = ''
  chatRunning.value = false
  currentRunId.value = ''
  currentRunStatus.value = 'idle'
}

async function loadAgent(nextAgent: Entity | null) {
  resetConversationState()
  if (!nextAgent) return
  const permissions = parseObject(nextAgent.permissions_json)
  loadRagSettings(nextAgent)
  securityProfile.value = permissions.security_profile || 'default'
  try {
    const [items, runtime, bases, groups] = await Promise.all([
      api.get<Entity[]>(`/agents/${nextAgent.id}/conversations`),
      api.get<Entity>('/security/runtime'),
      api.get<Entity[]>('/knowledge-bases'),
      api.get<Entity[]>('/knowledge-groups'),
    ])
    if (agent.value?.id !== nextAgent.id) return
    conversations.value = items
    securityRuntime.value = runtime
    knowledgeBases.value = bases
    knowledgeGroups.value = groups
    if (items.length) await openConversation(items[0])
  } catch (error: any) {
    app.notify(error.message, 'error')
  }
}

async function saveRagSettings() {
  if (!agent.value || ragSaving.value) return
  if (ragForm.dense_weight + ragForm.lexical_weight <= 0) {
    return app.notify('向量与全文检索权重不能同时为 0', 'error')
  }
  ragSaving.value = true
  try {
    const updated = await api.patch<Entity>(`/agents/${agent.value.id}`, {
      knowledge_bases: selectedKnowledgeBases.value,
      rag_config: { ...ragForm },
    })
    chat.updateAgent(props.windowId, updated)
    loadRagSettings(updated)
    ragSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    app.notify('RAG 设置已保存，将从下一轮对话开始生效')
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    ragSaving.value = false
  }
}

async function createConversation() {
  if (!agent.value) return null
  const conversation: Entity = await api.post(`/agents/${agent.value.id}/conversations`, { title: '新会话' })
  conversations.value.unshift(conversation)
  activeConversation.value = conversation
  messages.value = []
  steps.value = []
  artifacts.value = []
  sourceReviews.value = []
  selectedWebSource.value = null
  currentRunId.value = ''
  currentRunStatus.value = 'idle'
  return conversation
}

async function openConversation(conversation: Entity) {
  stopPolling()
  activeConversation.value = conversation
  await refreshConversation(conversation)
  await scrollMessages()
}

function startPolling(conversation: Entity) {
  if (pollTimer !== undefined) return
  pollTimer = window.setInterval(() => {
    if (activeConversation.value?.id === conversation.id) void refreshConversation(conversation, true)
  }, 1000)
}

async function refreshConversation(conversation: Entity, silent = false) {
  try {
    const [freshMessages, freshArtifacts, freshReviews] = await Promise.all([
      api.get<Entity[]>(`/conversations/${conversation.id}/messages`),
      api.get<Entity[]>(`/conversations/${conversation.id}/artifacts`),
      api.get<Entity[]>(`/conversations/${conversation.id}/source-reviews`),
    ])
    if (activeConversation.value?.id !== conversation.id) return
    messages.value = freshMessages
    artifacts.value = freshArtifacts
    sourceReviews.value = freshReviews
    const latestMessage = freshMessages[freshMessages.length - 1]
    const activeMessage = [...freshMessages].reverse().find(item => item.role === 'user' && item.run_status === 'running')
    const awaitingAssistant = latestMessage?.role === 'user' && latestMessage?.run_id
    if (activeMessage || awaitingAssistant) {
      const trackedMessage = activeMessage || latestMessage
      chatRunning.value = true
      currentRunId.value = trackedMessage.run_id || ''
      currentRunStatus.value = trackedMessage.run_status || 'running'
      const persisted = parse(trackedMessage.run_trace_json)
      if (persisted.length) steps.value = persisted
      startPolling(conversation)
    } else {
      const wasRunning = chatRunning.value
      chatRunning.value = false
      stopPolling()
      const latestWithRun = [...freshMessages].reverse().find(item => item.run_id)
      currentRunId.value = latestWithRun?.run_id || ''
      currentRunStatus.value = latestWithRun?.run_status || (freshMessages.length ? 'completed' : 'idle')
      const latestWithTrace = [...freshMessages].reverse().find(item => item.role === 'assistant' && item.trace_json)
      if (latestWithTrace) steps.value = parse(latestWithTrace.trace_json)
      if (wasRunning && !silent) app.notify('Agent 本轮执行完成')
    }
  } catch (error: any) {
    if (!silent) app.notify(error.message, 'error')
  }
}

async function scrollMessages() {
  await nextTick()
  if (messagePane.value) messagePane.value.scrollTop = messagePane.value.scrollHeight
}

function databasePersistence(traceValue: string | Entity[] | undefined) {
  const trace = Array.isArray(traceValue) ? traceValue : parse(traceValue || '[]')
  return [...trace].reverse().find(item => item.type === 'database_persisted')
}

function artifactLength(artifact: Entity) {
  return Number(artifact.content_characters || String(artifact.content || '').length)
}

function formatCount(value: number) {
  return value > 9999 ? `${(value / 10000).toFixed(1)} 万` : value.toLocaleString('zh-CN')
}

function artifactDate(artifact: Entity) {
  if (!artifact.created_at) return '刚刚生成'
  return new Date(artifact.created_at).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

function artifactFormat(artifact: Entity) {
  return String(artifact.format || artifact.kind || 'MARKDOWN').toUpperCase()
}

async function copyArtifact() {
  if (!activeArtifact.value) return
  try {
    await navigator.clipboard.writeText(String(activeArtifact.value.content || ''))
    app.notify('文档正文已复制')
  } catch {
    app.notify('复制失败，请检查系统剪贴板权限', 'error')
  }
}

function downloadArtifact() {
  if (!activeArtifact.value) return
  const filename = String(activeArtifact.value.title || 'Agent-产出文档.md')
    .replace(/[\\/:*?"<>|]/g, '-')
  const blob = new Blob([String(activeArtifact.value.content || '')], {
    type: 'text/markdown;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename.toLowerCase().endsWith('.md') ? filename : `${filename}.md`
  link.click()
  URL.revokeObjectURL(url)
  app.notify('文档已导出为 Markdown')
}

async function monitorBackgroundTask(taskId: string) {
  while (chat.tasks.find(item => item.id === taskId)?.status === 'running') {
    const task = chat.tasks.find(item => item.id === taskId)
    if (!task) return
    try {
      const freshMessages = await api.get<Entity[]>(`/conversations/${task.conversationId}/messages`)
      const userMessage = [...freshMessages].reverse().find(item =>
        item.role === 'user'
        && (task.runId ? item.run_id === task.runId : item.content === task.input),
      )
      if (userMessage?.run_id && userMessage.run_id !== task.runId) {
        chat.updateTask(taskId, { runId: userMessage.run_id })
      }
      const runId = userMessage?.run_id || task.runId
      const assistantMessage = [...freshMessages].reverse().find(item =>
        item.role === 'assistant' && (!runId || item.run_id === runId),
      )
      if (assistantMessage && assistantMessage.run_status !== 'running') {
        const persisted = databasePersistence(assistantMessage.trace_json)
        const artifactCount = Number(persisted?.artifact_count || 0)
        if (assistantMessage.run_status === 'completed') {
          chat.finishTask(taskId, 'completed', {
            runId: assistantMessage.run_id || runId,
            artifactCount,
            detail: artifactCount
              ? `任务已完成，${artifactCount} 份产出文档已保存到数据库`
              : '任务已完成，结果已保存到数据库',
          })
          app.notify(artifactCount
            ? `Agent 后台任务已完成，${artifactCount} 份文档已保存到数据库`
            : 'Agent 后台任务已完成，结果已保存到数据库')
        } else {
          chat.finishTask(taskId, 'failed', {
            runId: assistantMessage.run_id || runId,
            detail: assistantMessage.run_error || '任务执行失败',
          })
          app.notify(`Agent 后台任务失败：${assistantMessage.run_error || '未知错误'}`, 'error')
        }
        if (activeConversation.value?.id === task.conversationId) {
          await refreshConversation(activeConversation.value, true)
        }
        return
      }
    } catch {
      // A transient connection loss does not cancel the persisted backend task.
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000))
  }
}

async function sendMessage() {
  const content = chatInput.value.trim()
  if (!agent.value || !content || chatRunning.value) return
  const agentSnapshot: Entity = { ...agent.value }
  const conversation = activeConversation.value || await createConversation()
  if (!conversation) return
  const taskId = chat.trackTask({
    conversationId: conversation.id,
    agent: agentSnapshot,
    input: content,
  })
  let persistedArtifactCount = 0
  let serverError = ''
  let completedRunStatus = 'completed'
  let completedRunError = ''
  messages.value.push({ id: `local-${Date.now()}`, role: 'user', content })
  chatInput.value = ''
  steps.value = [{ type: 'request_submitted', at: new Date().toISOString() }]
  chatRunning.value = true
  currentRunStatus.value = 'running'
  await scrollMessages()
  try {
    await api.stream(`/conversations/${conversation.id}/messages/stream`, { content, security_profile: securityProfile.value }, event => {
      if (event.type === 'step') {
        if (event.step.run_id) {
          chat.updateTask(taskId, { runId: event.step.run_id })
          if (activeConversation.value?.id === conversation.id) currentRunId.value = event.step.run_id
        }
        if (event.step.type === 'database_persisted') {
          persistedArtifactCount = Number(event.step.artifact_count || 0)
          chat.updateTask(taskId, {
            artifactCount: persistedArtifactCount,
            detail: persistedArtifactCount
              ? `${persistedArtifactCount} 份产出文档已保存到数据库`
              : '本轮结果已保存到数据库',
          })
        }
        if (activeConversation.value?.id === conversation.id) {
          if (event.step.type === 'stream_connected') {
            const pending = steps.value.find(item => item.type === 'request_submitted')
            if (pending) Object.assign(pending, event.step)
            else steps.value.push(event.step)
          } else if (event.step.type === 'model_waiting') {
            const waiting = steps.value.find(item => item.type === 'model_waiting')
            if (waiting) Object.assign(waiting, event.step)
            else steps.value.push(event.step)
          } else steps.value.push(event.step)
          if (['web_search_started','web_search_results','web_fetch_started','web_page_fetched'].includes(event.step.type)) panelTab.value = 'web'
          if (event.step.type === 'approval_required') panelTab.value = 'steps'
          if (event.step.type === 'artifact_created') {
            panelTab.value = 'docs'
            void api.get<Entity[]>(`/conversations/${conversation.id}/artifacts`).then(value => {
              if (activeConversation.value?.id === conversation.id) artifacts.value = value
            })
          }
        }
      }
      if (event.type === 'assistant') {
        completedRunStatus = event.run?.status || 'completed'
        completedRunError = event.run?.error || ''
        if (activeConversation.value?.id === conversation.id) messages.value.push(event.message)
      }
      if (event.type === 'error') {
        serverError = event.message
        throw new Error(event.message)
      }
      if (activeConversation.value?.id === conversation.id) void scrollMessages()
    })
    if (completedRunStatus === 'completed') {
      chat.finishTask(taskId, 'completed', {
        artifactCount: persistedArtifactCount,
        detail: persistedArtifactCount
          ? `任务已完成，${persistedArtifactCount} 份产出文档已保存到数据库`
          : '任务已完成，结果已保存到数据库',
      })
      app.notify(persistedArtifactCount
        ? `Agent 本轮完成，${persistedArtifactCount} 份文档已保存到数据库`
        : 'Agent 本轮执行完成，结果已保存到数据库')
    } else {
      chat.finishTask(taskId, 'failed', { detail: completedRunError || '任务执行失败' })
      app.notify(`Agent 任务失败：${completedRunError || '未知错误'}`, 'error')
    }
  } catch (error: any) {
    if (serverError) {
      chat.finishTask(taskId, 'failed', { detail: serverError })
      app.notify(serverError, 'error')
    } else {
      chat.updateTask(taskId, { detail: '对话连接已断开，任务继续在后台执行' })
      app.notify('对话已转入后台，任务会继续执行并保存到业务数据库')
      void monitorBackgroundTask(taskId)
    }
  } finally {
    if (activeConversation.value?.id === conversation.id) await refreshConversation(conversation, true)
    if (agent.value?.id === agentSnapshot.id) {
      conversations.value = await api.get(`/agents/${agentSnapshot.id}/conversations`)
      activeConversation.value = conversations.value.find(item => item.id === conversation.id) || conversation
    }
  }
}

async function decideInChat(step: Entity, approved: boolean) {
  if (!step.approval_id || step.deciding) return
  step.deciding = true
  try {
    await api.post(`/approvals/${step.approval_id}/decide`, { approved, decided_by: 'conversation-user' })
    step.status = approved ? 'approved' : 'rejected'
    app.notify(approved ? '已批准，Agent 将继续本轮任务' : '已拒绝，Agent 将调整执行方案')
  } catch (error: any) { app.notify(error.message, 'error') }
  finally { step.deciding = false }
}

function showMessageTrace(message: Entity) {
  if (message.role === 'assistant' && message.trace_json) {
    steps.value = parse(message.trace_json)
    currentRunId.value = message.run_id || ''
    currentRunStatus.value = message.run_status || 'completed'
    panelTab.value = 'steps'
  }
}
async function reviewSource(source: Entity, decision: 'confirmed'|'excluded') {
  if (!activeConversation.value || !source.url) return
  try {
    await api.post(`/conversations/${activeConversation.value.id}/source-reviews`, {
      run_id: currentRunId.value || null, url: source.url, title: source.title || source.url,
      decision, credibility: source.credibility || {},
    })
    sourceReviews.value = await api.get(`/conversations/${activeConversation.value.id}/source-reviews`)
    app.notify(decision === 'confirmed' ? '已确认采用该来源' : '已排除该来源')
  } catch (error: any) { app.notify(error.message, 'error') }
}
function askTeacher(question: string) {
  chatInput.value = question
  documentFocus.value = false
  panelTab.value = 'steps'
  void sendMessage()
}
function useSuggestedQuestion(question: string) {
  chatInput.value = question
  void sendMessage()
}

function selectPanel(tab: 'steps'|'web'|'rag'|'docs') {
  panelTab.value = tab
  if (tab !== 'docs') documentFocus.value = false
}

function openDocuments(artifactId?: string) {
  if (artifactId) selectedArtifactId.value = artifactId
  else if (!selectedArtifactId.value && artifacts.value.length) selectedArtifactId.value = artifacts.value[0].id
  panelTab.value = 'docs'
  documentFocus.value = true
}

function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
  documentFocus.value = false
  chat.focus(props.windowId)
}

function stepTitle(step: Entity) {
  const labels: Record<string, string> = {
    request_submitted: '任务已提交', stream_connected: '已连接 Agent 执行引擎',
    context_ready: '会话上下文已装载', research_planning: '生成检索计划',
    research_sources_selected: `选定 ${step.count || 0} 条高相关来源`,
    web_research_empty: '联网研究未取得来源', research_synthesis_started: '开始多来源综合',
    quality_review_started: '第二轮质量审校', quality_review_skipped: '质量审校已降级',
    model_waiting: '正在等待模型响应', run_completed: '运行完成',
    database_persisted: '任务成果已保存到数据库',
    database_persistence_failed: '数据库持久化失败',
    rag_query_condensed: '多轮问题已改写为独立查询',
    rag_query_rewrite_started: '开始生成互补检索查询',
    rag_query_rewritten: '检索查询改写完成',
    rag_hybrid_retrieval_started: '向量与全文混合召回',
    rag_hybrid_retrieval_completed: '混合召回完成',
    rag_knowledge_graph_expanded: '知识图谱邻接扩展',
    rag_fusion_completed: '加权融合完成',
    rag_rerank_started: '开始证据重排',
    rag_rerank_completed: '证据重排与阈值过滤完成',
    rag_context_assembled: '引用上下文已组装',
    generation_verification_started: '开始生成结果校验',
    generation_repair_started: '校验未通过，自动修复',
    generation_repaired: '自动修复完成',
    generation_verified: '引用与完整性校验完成',
  }
  if (labels[step.type]) return labels[step.type]
  if (step.type === 'run_started') return `启动 ${step.agent}`
  if (step.type === 'intent_detected') return `识别意图 · ${step.category}`
  if (step.type === 'local_intent_detected') return `识别本地任务 · ${step.tool}`
  if (step.type === 'mcp_unavailable') return '部分 MCP 服务暂不可用'
  if (step.type === 'approval_required') return `等待批准 · ${step.tool}`
  if (step.type === 'approval_resolved') return `审批已处理 · ${step.tool}`
  if (step.type === 'web_search_started') return `搜索：${step.query}`
  if (step.type === 'web_search_results') return `找到 ${step.count || 0} 条候选来源`
  if (step.type === 'web_fetch_started') return `抓取网页 ${step.index}`
  if (step.type === 'web_page_fetched') return `网页 ${step.index} 已解析`
  if (step.type === 'artifact_created') return `已生成 ${step.title}`
  if (step.type === 'model_response') return step.tool_calls?.length ? `模型请求调用 ${step.tool_calls.join('、')}` : '模型生成回复'
  if (step.type === 'tool_result') return `工具 ${step.tool} · ${step.status}`
  if (step.type === 'error') return `运行异常：${step.message}`
  return step.type
}
function stepMeta(step: Entity) {
  if (step.type === 'request_submitted') return '正在建立本地执行连接'
  if (step.type === 'run_started') return `${step.security?.filesystem_mode || 'workspace'} · ${step.security?.command_mode || 'risk_based'}`
  if (step.type === 'intent_detected') return `${step.actions?.join('、') || '回答'} · 置信度 ${Math.round((step.confidence || 0) * 100)}%`
  if (step.type === 'local_intent_detected') return step.allowed ? `优先访问本地路径：${JSON.stringify(step.arguments || {})}` : '当前 Agent 未启用所需本地工具'
  if (step.type === 'mcp_unavailable') return step.errors?.join('；') || '连接失败，Agent 将使用其余可用能力'
  if (step.type === 'approval_required') return `${step.risk || '高'}风险操作已暂停，请批准或拒绝`
  if (step.type === 'approval_resolved') return step.status === 'completed' ? '操作已执行，Agent 继续工作' : '操作未执行'
  if (step.type === 'stream_connected') return '运行轨迹将实时写入 SQLite'
  if (step.type === 'context_ready') return `历史消息 ${step.history_messages || 0} 条`
  if (step.type === 'rag_query_condensed') return step.changed ? step.standalone_query : '当前问题已经可以独立检索'
  if (step.type === 'rag_query_rewritten') return `${step.query_count || 0} 条查询 · ${step.cross_language ? '含跨语言扩展' : '同语言检索'}`
  if (step.type === 'rag_hybrid_retrieval_completed') return `向量 ${step.dense_candidates || 0} · 全文 ${step.lexical_candidates || 0}`
  if (step.type === 'rag_knowledge_graph_expanded') return `${step.entities || 0} 个实体 · ${step.edges || 0} 条邻接边`
  if (step.type === 'rag_fusion_completed') return `${step.method} · ${step.fused_candidates || 0} 个候选`
  if (step.type === 'rag_rerank_completed') return `保留 ${step.selected || 0} 条 · 阈值过滤 ${step.filtered_by_threshold || 0} 条`
  if (step.type === 'rag_context_assembled') return `${step.context_chars || 0} 字符 · ${step.citation_count || 0} 个引用 · ${step.numbered_list_items || 0} 个列表项`
  if (step.type === 'generation_verified') return step.passed ? `校验通过 · ${step.citation_count || 0} 个引用` : `仍有问题：${step.issues?.join('；') || '待人工复核'}`
  if (step.type === 'generation_repair_started') return step.issues?.join('；') || '正在修复引用与完整性'
  if (step.type === 'model_response') return `第 ${step.iteration} 轮模型响应`
  if (step.type === 'web_search_results') return `${step.results?.slice(0,2).map((item:Entity)=>item.title).join('；') || '没有通过过滤的结果'}`
  if (step.type === 'web_page_fetched') return `${step.status} · ${step.title || step.url}`
  if (step.type === 'model_waiting') return `已等待 ${step.elapsed_seconds || 0} 秒`
  if (step.type === 'run_completed') return `${step.duration_ms} ms · ${step.token_usage} tokens`
  if (step.type === 'database_persisted') {
    return step.artifact_count
      ? `消息与 ${step.artifact_count} 份产出文档已持久化 · 未修改知识库`
      : '对话消息与运行记录已持久化 · 未修改知识库'
  }
  if (step.type === 'database_persistence_failed') return step.message || '请检查业务数据库连接'
  return '可审计执行事件'
}

function startDrag(event: PointerEvent) {
  if (!chatWindow.value || fullscreen.value) return
  chat.focus(props.windowId)
  drag.active = true
  drag.moved = false
  drag.offsetX = event.clientX - chatWindow.value.position.x
  drag.offsetY = event.clientY - chatWindow.value.position.y
  window.addEventListener('pointermove', moveDrag)
  window.addEventListener('pointerup', endDrag, { once: true })
}
function moveDrag(event: PointerEvent) {
  if (!drag.active) return
  drag.moved = true
  const width = Math.min(1400, window.innerWidth - 24)
  const height = Math.min(880, window.innerHeight - 24)
  chat.move(props.windowId, {
    x: Math.min(Math.max(12, event.clientX - drag.offsetX), window.innerWidth - width - 12),
    y: Math.min(Math.max(12, event.clientY - drag.offsetY), window.innerHeight - height - 12),
  })
}
function endDrag() {
  drag.active = false
  window.removeEventListener('pointermove', moveDrag)
}
function endChat() {
  stopPolling()
  chat.closeWindow(props.windowId)
}

watch(() => agent.value?.id, () => { void loadAgent(agent.value) }, { immediate: true })
watch(artifacts, items => {
  if (!items.some(item => item.id === selectedArtifactId.value)) {
    selectedArtifactId.value = items[0]?.id || ''
  }
})
onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('pointermove', moveDrag)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="agent-dialog">
      <div
        v-if="chatWindow && !chatWindow.minimized && agent"
        class="agent-dialog-layer"
        :class="{ 'is-fullscreen': fullscreen }"
        :style="windowStyle"
        @pointerdown="chat.focus(windowId)"
      >
        <section class="agent-dialog" role="dialog" :aria-label="`与 ${agent.name} 对话`">
          <header class="agent-dialog-header" @pointerdown="startDrag">
            <div class="agent-dialog-identity"><span class="agent-dialog-avatar"><Bot :size="19" /></span><div><small>AGENT CONVERSATION</small><strong>{{ agent.name }}</strong><span>{{ chatRunning ? '正在执行任务' : '会话已就绪' }}</span></div></div>
            <div class="agent-dialog-actions" @pointerdown.stop><StatusBadge v-if="currentRunStatus!=='idle'" :status="currentRunStatus" /><button class="document-shortcut" :class="{ active: documentFocus }" :title="`打开文档工作区（${artifacts.length}）`" @click="openDocuments()"><FileText :size="16" /><b v-if="artifacts.length">{{ artifacts.length }}</b></button><button :title="fullscreen ? '退出全屏' : '全屏对话'" @click="toggleFullscreen"><Minimize2 v-if="fullscreen" :size="17" /><Maximize2 v-else :size="17" /></button><button title="最小化到 Agent 堆叠区" @click="chat.minimize(windowId)"><Minus :size="17" /></button><button title="关闭对话，当前任务仍会后台完成" @click="endChat"><X :size="17" /></button></div>
          </header>
          <div class="agent-chat-layout overlay-layout">
            <aside class="conversation-sidebar">
              <button class="btn btn-primary btn-sm" style="width:100%" @click="createConversation"><MessageSquarePlus :size="14" />新建会话</button>
              <div class="conversation-list">
                <button v-for="item in conversations" :key="item.id" class="conversation-item" :class="{ active: activeConversation?.id === item.id }" @click="openConversation(item)">
                  <span>{{ item.title }}</span><small>{{ item.run_status==='running' ? '● 执行中 · ' : '' }}{{ new Date(item.updated_at).toLocaleString() }}</small>
                </button>
                <div v-if="!conversations.length" class="empty compact">尚无会话</div>
              </div>
            </aside>

            <div class="chat-panel">
              <div ref="messagePane" class="message-pane">
                <div v-if="!messages.length" class="chat-empty agent-welcome"><Sparkles :size="25" /><strong>开始与 {{ agent.name }} 对话</strong><span>{{ openingMessage }}</span><div v-if="suggestedQuestions.length" class="suggested-questions"><button v-for="question in suggestedQuestions" :key="question" @click="useSuggestedQuestion(question)">{{ question }}</button></div><small>支持连续追问；每一轮都会重新检索并记录证据链路。</small></div>
                <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role" @click="showMessageTrace(message)">
                  <div class="message-avatar"><UserRound v-if="message.role==='user'" :size="15" /><Bot v-else :size="15" /></div>
                  <div class="message-copy">
                    <strong>{{ message.role==='user' ? '你' : agent.name }}{{ message.role==='assistant' && message.trace_json ? ' · 点击回放步骤' : '' }}</strong>
                    <RichAgentMessage v-if="message.role==='assistant'" class="message-bubble markdown-message" :content="message.content" />
                    <p v-else class="message-bubble plain-message">{{ message.content }}</p>
                  </div>
                </article>
                <article v-if="chatRunning" class="chat-message assistant"><div class="message-avatar"><Bot :size="15" /></div><div class="message-copy"><strong>{{ agent.name }}</strong><p class="message-bubble typing">正在执行任务并整理回复</p></div></article>
              </div>
              <div class="chat-composer">
                <div class="composer-security"><Shield :size="12" /><span>本轮使用“{{ activeSecurityProfile.label }}”</span><button @click="securityMenu=!securityMenu">调整</button></div>
                <textarea v-model="chatInput" class="textarea" placeholder="输入消息；Ctrl + Enter 发送" @keydown.ctrl.enter.prevent="sendMessage" />
                <button class="btn btn-primary" :disabled="chatRunning || !chatInput.trim()" @click="sendMessage"><Send :size="15" />{{ chatRunning ? '执行中' : '发送' }}</button>
                <div v-if="securityMenu" class="security-menu composer-menu"><button v-for="item in securityProfiles" :key="item.value" :class="{active:securityProfile===item.value}" @click="securityProfile=item.value;securityMenu=false"><component :is="item.icon" :size="15" /><span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span><Check v-if="securityProfile===item.value" :size="14" /></button><p v-if="securityRuntime">全局授权目录 {{ securityRuntime.workspace_roots?.length || 0 }} 个 · 关键命令{{ securityRuntime.block_critical_commands ? '始终拦截' : '允许按规则执行' }}</p></div>
              </div>
            </div>

            <aside class="execution-panel" :class="{ 'document-focused': documentFocus }">
              <div class="execution-title">
                <span><FileText v-if="documentFocus" :size="15" /><Settings2 v-else-if="panelTab==='rag'" :size="15" /><Activity v-else :size="15" />{{ documentFocus ? '文档工作区' : panelTab==='rag' ? 'RAG 设置' : '运行与交付' }}</span>
                <button v-if="panelTab==='docs'" class="document-focus-toggle" :title="documentFocus ? '返回对话并排视图' : '展开文档工作区'" @click="documentFocus=!documentFocus"><Minimize2 v-if="documentFocus" :size="14" /><Maximize2 v-else :size="14" />{{ documentFocus ? '返回对话' : '展开阅读' }}</button>
                <span v-else-if="panelTab==='rag'" class="rag-mode-badge">{{ ragForm.enabled ? 'ENABLED' : 'DISABLED' }}</span>
                <span v-else class="live-dot" :class="{ running: chatRunning }">{{ chatRunning ? 'LIVE' : 'TRACE' }}</span>
              </div>
              <div class="execution-tabs"><button :class="{active:panelTab==='steps'}" @click="selectPanel('steps')"><Activity :size="12" />步骤</button><button :class="{active:panelTab==='web'}" @click="selectPanel('web')"><Globe2 :size="12" />网页 {{ webEvents.length }}</button><button :class="{active:panelTab==='rag'}" @click="selectPanel('rag')"><Settings2 :size="12" />RAG</button><button :class="{active:panelTab==='docs'}" @click="openDocuments()"><FileText :size="12" />文档 {{ artifacts.length }}</button></div>
              <div v-if="panelTab==='steps'" class="step-list">
                <div v-for="(step,index) in steps" :key="index" class="step-item" :class="{ error: step.type==='error' }"><span class="step-index">{{ index + 1 }}</span><div><strong>{{ stepTitle(step) }}</strong><p>{{ stepMeta(step) }}</p><div v-if="step.type==='approval_required' && !step.status" class="inline-approval"><button :disabled="step.deciding" @click="decideInChat(step,true)"><Check :size="11" />批准执行</button><button :disabled="step.deciding" class="reject" @click="decideInChat(step,false)"><X :size="11" />拒绝</button></div><span v-else-if="step.type==='approval_required' && step.status" class="approval-state">{{ step.status==='approved' ? '已批准' : '已拒绝' }}</span></div></div>
                <div v-if="!steps.length" class="empty compact">发送消息后，这里会实时显示模型、工具、MCP、Agent 联动和审批事件。</div>
              </div>
              <div v-else-if="panelTab==='web'" class="research-list">
                <section v-if="selectedWebSource" class="web-preview"><header><strong>{{ selectedWebSource.title || '来源网页' }}</strong><button @click="selectedWebSource=null"><X :size="13" /></button></header><div class="preview-actions"><a :href="selectedWebSource.url" target="_blank" rel="noreferrer"><ExternalLink :size="11" />浏览器打开原文</a><button class="confirm" @click="reviewSource(selectedWebSource,'confirmed')"><Check :size="11" />确认采用</button><button class="exclude" @click="reviewSource(selectedWebSource,'excluded')"><X :size="11" />排除</button></div><iframe :src="selectedWebSource.url" :title="selectedWebSource.title" sandbox="allow-scripts allow-same-origin allow-popups" /></section>
                <article v-for="(event,index) in webEvents" :key="index" class="research-card"><div><Globe2 :size="13" /><strong>{{ stepTitle(event) }}</strong></div><button v-if="event.url" class="source-link" @click="selectedWebSource=event"><ExternalLink :size="11" />{{ event.title || event.url }}</button><div v-if="event.url" class="source-review-actions"><button @click="reviewSource(event,'confirmed')"><Check :size="10" />确认</button><button @click="reviewSource(event,'excluded')"><X :size="10" />排除</button><span v-if="sourceDecision[event.url]">{{ sourceDecision[event.url]==='confirmed' ? '已采用' : '已排除' }}</span></div></article>
                <div v-if="!webEvents.length" class="empty compact">研究类任务开始后，这里会显示检索、抓取和来源审查。</div>
              </div>
              <div v-else-if="panelTab==='rag'" class="rag-settings-panel">
                <section class="rag-settings-intro">
                  <div><span><Database :size="15" /></span><div><strong>本 Agent 的检索链路</strong><small>保存后从下一轮对话开始生效</small></div></div>
                  <label class="rag-master-switch"><input v-model="ragForm.enabled" type="checkbox"><i /><b>{{ ragForm.enabled ? '启用' : '关闭' }}</b></label>
                </section>

                <section class="rag-setting-card">
                  <header><strong>知识范围</strong><span>{{ selectedKnowledgeBases.length }} 个知识库</span></header>
                  <div class="rag-choice-list">
                    <button v-for="item in knowledgeBases" :key="item.id" :class="{ active: selectedKnowledgeBases.includes(item.id) }" @click="toggleValue(selectedKnowledgeBases,item.id)"><Database :size="11" />{{ item.name }}<Check v-if="selectedKnowledgeBases.includes(item.id)" :size="10" /></button>
                    <small v-if="!knowledgeBases.length">还没有可绑定的知识库</small>
                  </div>
                  <template v-if="knowledgeGroups.length">
                    <label class="rag-field-label">知识库分组</label>
                    <div class="rag-choice-list groups">
                      <button v-for="item in knowledgeGroups" :key="item.id" :class="{ active: ragForm.knowledge_group_ids.includes(item.id) }" @click="toggleValue(ragForm.knowledge_group_ids,item.id)">{{ item.name }}<Check v-if="ragForm.knowledge_group_ids.includes(item.id)" :size="10" /></button>
                    </div>
                  </template>
                </section>

                <section class="rag-setting-card">
                  <header><strong>混合检索</strong><span>Weighted RRF</span></header>
                  <label class="rag-range-field"><span>相似度阈值 <b>{{ Number(ragForm.similarity_threshold).toFixed(2) }}</b></span><input v-model.number="ragForm.similarity_threshold" type="range" min="0" max="1" step="0.05"></label>
                  <div class="rag-two-columns">
                    <label><span>向量权重</span><input v-model.number="ragForm.dense_weight" type="number" min="0" max="1" step="0.05"></label>
                    <label><span>全文权重</span><input v-model.number="ragForm.lexical_weight" type="number" min="0" max="1" step="0.05"></label>
                  </div>
                  <div class="rag-three-columns">
                    <label><span>召回</span><input v-model.number="ragForm.candidate_k" type="number" min="5" max="100"></label>
                    <label><span>重排</span><input v-model.number="ragForm.rerank_k" type="number" min="1" max="50"></label>
                    <label><span>证据</span><input v-model.number="ragForm.top_k" type="number" min="1" max="20"></label>
                  </div>
                  <label class="rag-wide-input"><span>上下文字符预算</span><input v-model.number="ragForm.context_char_budget" type="number" min="1000" max="100000" step="1000"></label>
                  <label class="rag-wide-input"><span>Rerank 模型覆盖</span><input v-model="ragForm.rerank_model" type="text" placeholder="留空继承全局配置"></label>
                </section>

                <section class="rag-setting-card">
                  <header><strong>检索增强</strong><span>按需开启</span></header>
                  <div class="rag-toggle-grid">
                    <label><input v-model="ragForm.query_rewrite" type="checkbox"><span><b>多查询改写</b><small>生成互补查询</small></span></label>
                    <label><input v-model="ragForm.multi_turn" type="checkbox"><span><b>多轮优化</b><small>追问独立化</small></span></label>
                    <label><input v-model="ragForm.cross_language" type="checkbox"><span><b>跨语言</b><small>中英互译召回</small></span></label>
                    <label><input v-model="ragForm.knowledge_graph" type="checkbox"><span><b>知识图谱</b><small>实体邻接扩展</small></span></label>
                    <label><input v-model="ragForm.parent_expansion" type="checkbox"><span><b>父块扩展</b><small>补全章节语境</small></span></label>
                    <label><input v-model="ragForm.complete_list_expansion" type="checkbox"><span><b>完整列表</b><small>防止五点缺项</small></span></label>
                  </div>
                  <label class="rag-wide-input history-input"><span>多轮历史消息数</span><input v-model.number="ragForm.max_history_messages" type="number" min="0" max="20"></label>
                </section>

                <footer class="rag-settings-footer">
                  <span><Check v-if="ragSavedAt" :size="11" />{{ ragSavedAt ? `${ragSavedAt} 已保存` : '修改不会影响正在执行的任务' }}</span>
                  <button :disabled="ragSaving" @click="saveRagSettings"><Save :size="13" />{{ ragSaving ? '保存中' : '保存 RAG 设置' }}</button>
                </footer>
              </div>
              <div v-else class="document-workspace">
                <aside v-if="artifacts.length" class="document-index">
                  <header class="document-library-head">
                    <div class="document-library-icon"><Files :size="17" /></div>
                    <div><strong>产出文档</strong><span>本次会话的完整交付物</span></div>
                    <span class="database-badge"><Database :size="11" />数据库</span>
                  </header>
                  <div class="document-summary">
                    <div><strong>{{ artifacts.length }}</strong><span>份文档</span></div>
                    <i />
                    <div><strong>{{ formatCount(artifactCharacters) }}</strong><span>总字符</span></div>
                  </div>
                  <div class="document-card-list">
                    <button
                      v-for="(artifact,index) in artifacts"
                      :key="artifact.id"
                      :class="{ active: activeArtifact?.id===artifact.id }"
                      @click="selectedArtifactId=artifact.id"
                    >
                      <span class="document-file-mark">{{ String(index + 1).padStart(2, '0') }}</span>
                      <span class="document-card-copy">
                        <strong>{{ artifact.title }}</strong>
                        <small><Clock3 :size="10" />{{ artifactDate(artifact) }} · {{ artifactFormat(artifact) }} · {{ formatCount(artifactLength(artifact)) }} 字符</small>
                      </span>
                      <Check v-if="activeArtifact?.id===artifact.id" :size="14" class="document-selected-check" />
                    </button>
                  </div>
                  <div v-if="!documentFocus && activeArtifact" class="document-compact-preview">
                    <span>已选文档</span>
                    <p>{{ String(activeArtifact.content || '').replace(/[#>*_`]/g, '').slice(0, 120) }}{{ String(activeArtifact.content || '').length > 120 ? '…' : '' }}</p>
                    <button @click="documentFocus=true"><Maximize2 :size="12" />进入沉浸阅读</button>
                  </div>
                </aside>
                <main v-if="activeArtifact" class="document-reader">
                  <header class="document-reader-header">
                    <div class="document-reader-title">
                      <span>AGENT DELIVERABLE / {{ artifactFormat(activeArtifact) }}</span>
                      <strong>{{ activeArtifact.title }}</strong>
                      <small><Database :size="11" />已保存到业务数据库 · {{ artifactDate(activeArtifact) }} · {{ formatCount(artifactLength(activeArtifact)) }} 字符</small>
                    </div>
                    <div class="document-reader-actions">
                      <span class="document-ready"><Check :size="12" />已持久化</span>
                      <button title="复制文档正文" @click="copyArtifact"><Copy :size="13" />复制</button>
                      <button title="导出 Markdown 文件" @click="downloadArtifact"><Download :size="13" />导出</button>
                    </div>
                  </header>
                  <DocumentClassroom :artifact="activeArtifact" :agent-name="agent.name" :conversation-id="activeConversation?.id || ''" @ask="askTeacher" />
                </main>
                <div v-if="!artifacts.length" class="empty compact document-empty">
                  <span class="document-empty-icon"><FileText :size="27" /></span>
                  <strong>等待第一份产出文档</strong>
                  <span>研究与报告类任务完成后，文档会保存到业务数据库并显示在这里，不会自动写入知识库。</span>
                </div>
              </div>
              <div v-if="panelTab==='steps' || panelTab==='web'" class="trace-note">仅展示操作轨迹与依据，不展示模型内部隐式推理。</div>
            </aside>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.agent-dialog-layer{position:fixed;inset:0;z-index:900;display:flex;align-items:center;justify-content:center;padding:32px}.agent-dialog-backdrop{position:absolute;inset:0;border:0;background:rgba(8,28,48,.46);backdrop-filter:blur(5px);cursor:default}.agent-dialog{position:relative;width:min(1440px,calc(100vw - 64px));height:min(820px,calc(100vh - 64px));overflow:hidden;border:1px solid #bdd4e5;border-radius:16px;background:#fff;box-shadow:0 30px 90px rgba(8,31,52,.32)}.agent-dialog-header{height:68px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #dde8f0;background:linear-gradient(120deg,#f8fbfe,#eef7fd)}.agent-dialog-identity{display:flex;align-items:center;gap:11px}.agent-dialog-avatar{display:grid;width:39px;height:39px;place-items:center;border-radius:11px;color:#fff;background:linear-gradient(135deg,#1266bb,#22a0c4)}.agent-dialog-identity>div{display:grid;grid-template-columns:auto auto;align-items:end;gap:2px 8px}.agent-dialog-identity small{grid-column:1/-1;font-size:8px;letter-spacing:1.4px;color:#6e879b}.agent-dialog-identity strong{font-size:15px;color:#153b62}.agent-dialog-identity span{font-size:9px;color:#7890a5}.agent-dialog-actions{display:flex;align-items:center;gap:7px}.agent-dialog-actions>button{position:relative;display:grid;width:31px;height:31px;place-items:center;border:1px solid #cbdce9;border-radius:8px;color:#587289;background:#fff;cursor:pointer}.agent-dialog-actions>button:hover,.agent-dialog-actions>button.active{color:#1269bd;border-color:#8fbee0;background:#f1f8fd}.document-shortcut b{position:absolute;right:-5px;top:-6px;min-width:16px;height:16px;padding:0 4px;display:grid;place-items:center;border:2px solid #f4f9fd;border-radius:99px;color:#fff;background:#1769c2;font-size:8px}.overlay-layout{height:calc(100% - 68px);min-height:0;grid-template-columns:190px minmax(360px,1fr) 370px}.overlay-layout :deep(.message-pane){max-height:none}.chat-composer{position:relative;padding-top:29px!important}.composer-security{position:absolute;top:7px;left:12px;right:12px;display:flex;align-items:center;gap:6px;color:#617f97;font-size:8px}.composer-security button{margin-left:auto;border:0;color:#1769c2;background:transparent;font-size:8px;cursor:pointer}.security-menu{position:absolute;z-index:40;width:300px;padding:7px;border:1px solid #cbddea;border-radius:10px;background:#fff;box-shadow:0 14px 36px #143d5c2e}.composer-menu{right:12px;bottom:100%}.security-menu>button{display:grid;width:100%;grid-template-columns:auto 1fr auto;align-items:center;gap:9px;padding:9px;border:0;border-radius:7px;text-align:left;color:#6a8296;background:transparent;cursor:pointer}.security-menu>button:hover,.security-menu>button.active{color:#1769c2;background:#edf6fd}.security-menu span{display:flex;flex-direction:column;gap:2px}.security-menu strong{font-size:10px;color:#315875}.security-menu small{font-size:8px}.security-menu>p{margin:6px 5px 2px;padding-top:7px;border-top:1px solid #e3ebf2;color:#7890a3;font-size:8px}.inline-approval{display:flex;gap:5px;margin-top:7px}.inline-approval button{display:inline-flex;align-items:center;gap:3px;padding:4px 7px;border:1px solid #8ac4ac;border-radius:5px;color:#137653;background:#edfaf4;font-size:8px;cursor:pointer}.inline-approval button.reject{border-color:#e3adad;color:#a73c3c;background:#fff5f5}.approval-state{display:inline-block;margin-top:5px;color:#187855;font-size:8px;font-weight:700}.agent-chat-float{position:fixed;z-index:880;width:260px;height:72px;padding:9px 10px 9px 6px;display:flex;align-items:center;gap:9px;border:1px solid #87b8da;border-radius:14px;color:#244c6f;background:rgba(255,255,255,.96);box-shadow:0 14px 38px rgba(17,64,99,.25);backdrop-filter:blur(10px);cursor:grab;touch-action:none;user-select:none}.agent-chat-float.dragging{cursor:grabbing;box-shadow:0 18px 45px rgba(17,64,99,.34)}.float-grip{flex:none;color:#9ab0c1}.float-avatar{position:relative;display:grid;width:39px;height:39px;flex:none;place-items:center;border-radius:11px;color:#fff;background:linear-gradient(135deg,#1769c2,#25a5bc)}.float-avatar i{position:absolute;right:-2px;bottom:-2px;width:9px;height:9px;border:2px solid #fff;border-radius:50%;background:#20b774}.float-copy{display:flex;min-width:0;flex:1;flex-direction:column;text-align:left}.float-copy small{font-size:8px;color:#7991a6}.float-copy strong{overflow:hidden;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.float-open{font-size:8px;font-weight:700;color:#1769c2}.float-close{display:grid;width:22px;height:22px;place-items:center;border-radius:6px;color:#8096a8}.float-close:hover{color:#b44444;background:#fff1f1}.agent-dialog-enter-active,.agent-dialog-leave-active,.agent-float-enter-active,.agent-float-leave-active{transition:opacity .18s ease,transform .18s ease}.agent-dialog-enter-from,.agent-dialog-leave-to{opacity:0}.agent-dialog-enter-from .agent-dialog,.agent-dialog-leave-to .agent-dialog{transform:translateY(12px) scale(.985)}.agent-float-enter-from,.agent-float-leave-to{opacity:0;transform:translateY(8px) scale(.96)}
.execution-panel.document-focused{position:absolute;inset:68px 0 0;z-index:30;border-left:0;background:#f3f7fa;animation:document-in .18s ease}
.document-focus-toggle{margin-left:auto;padding:6px 9px;border:1px solid #b9d4e8;border-radius:7px;display:flex;align-items:center;gap:5px;color:#1769c2;background:#fff;font-size:9px;font-weight:700;cursor:pointer}
.document-workspace{min-height:0;flex:1;display:grid;grid-template-columns:1fr;overflow:hidden;background:#f3f7fa}
.document-index{min-width:0;padding:14px;overflow:auto;background:linear-gradient(180deg,#f7fafc,#edf4f8)}
.document-library-head{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:9px;margin-bottom:12px}
.document-library-icon{display:grid;width:34px;height:34px;place-items:center;border-radius:10px;color:#fff;background:linear-gradient(135deg,#145da5,#278fae);box-shadow:0 6px 16px rgba(20,93,165,.2)}
.document-library-head>div:nth-child(2){display:flex;min-width:0;flex-direction:column;gap:2px}
.document-library-head strong{color:#193f5d;font-size:11px}
.document-library-head>div:nth-child(2)>span{color:#7b91a2;font-size:8px}
.database-badge{padding:4px 6px;display:flex;align-items:center;gap:3px;border:1px solid #bde2d0;border-radius:99px;color:#11714f;background:#eaf8f1;font-size:7px;font-weight:700}
.document-summary{height:50px;margin-bottom:12px;padding:0 13px;display:flex;align-items:center;justify-content:space-around;border:1px solid #d6e4ed;border-radius:11px;background:rgba(255,255,255,.84);box-shadow:0 5px 16px rgba(24,67,96,.05)}
.document-summary>div{display:flex;align-items:baseline;gap:5px}.document-summary strong{color:#174f7b;font-size:15px}.document-summary span{color:#8194a4;font-size:8px}.document-summary i{width:1px;height:23px;background:#dce6ed}
.document-card-list{display:flex;flex-direction:column;gap:8px}
.document-card-list>button{position:relative;width:100%;padding:10px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:9px;border:1px solid #d6e3ec;border-radius:11px;text-align:left;color:#496a84;background:#fff;box-shadow:0 3px 10px rgba(31,72,101,.04);cursor:pointer;transition:.16s ease}
.document-card-list>button:hover{border-color:#8cb9d8;transform:translateY(-1px);box-shadow:0 8px 20px rgba(20,84,130,.09)}
.document-card-list>button.active{border-color:#5b9dcc;background:linear-gradient(135deg,#fff,#edf7fd);box-shadow:0 8px 22px rgba(20,91,143,.12)}
.document-file-mark{display:grid;width:31px;height:37px;place-items:center;border-radius:7px;color:#39739e;background:#e8f3fa;font-size:9px;font-weight:800}
.document-card-list>button.active .document-file-mark{color:#fff;background:linear-gradient(145deg,#1769b5,#2c94ad)}
.document-card-copy{display:flex;min-width:0;flex-direction:column;gap:5px}.document-card-copy strong{overflow:hidden;color:#264e6a;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.document-card-copy small{display:flex;align-items:center;gap:3px;overflow:hidden;color:#8093a2;font-size:7px;text-overflow:ellipsis;white-space:nowrap}.document-selected-check{color:#187a59}
.document-compact-preview{margin-top:12px;padding:11px;border:1px solid #d4e2ec;border-radius:10px;background:rgba(255,255,255,.72)}.document-compact-preview>span{color:#6f8798;font-size:7px;font-weight:800;letter-spacing:.7px}.document-compact-preview p{display:-webkit-box;margin:6px 0 9px;overflow:hidden;color:#4c687d;font-size:8px;line-height:1.65;-webkit-box-orient:vertical;-webkit-line-clamp:3}.document-compact-preview button{padding:0;border:0;display:flex;align-items:center;gap:4px;color:#1769b5;background:transparent;font-size:8px;font-weight:700;cursor:pointer}
.document-reader{display:none;min-width:0;min-height:0;overflow:hidden;background:#fff}
.document-reader-header{width:100%;min-width:0;min-height:72px;box-sizing:border-box;padding:10px 18px;display:flex;align-items:center;justify-content:space-between;gap:14px;overflow:hidden;border-bottom:1px solid #d9e4eb;background:rgba(255,255,255,.96);box-shadow:0 3px 14px rgba(28,67,94,.04)}
.document-reader-title{display:flex;min-width:0;flex-direction:column;gap:3px}.document-reader-title>span{color:#6f92aa;font-size:7px;font-weight:800;letter-spacing:1px}.document-reader-title strong{overflow:hidden;color:#183f5d;font-size:14px;text-overflow:ellipsis;white-space:nowrap}.document-reader-title small{display:flex;align-items:center;gap:4px;overflow:hidden;color:#7890a1;font-size:8px;text-overflow:ellipsis;white-space:nowrap}
.document-reader-actions{display:flex;flex:none;align-items:center;gap:6px}.document-reader-actions>button{height:29px;padding:0 9px;display:flex;align-items:center;gap:4px;border:1px solid #cadbe6;border-radius:7px;color:#3b6784;background:#fff;font-size:8px;font-weight:700;cursor:pointer}.document-reader-actions>button:hover{color:#1269b5;border-color:#86b7d8;background:#f0f8fd}
.document-ready{flex:none;padding:5px 8px;display:flex;align-items:center;gap:4px;border-radius:99px;color:#08734c;background:#e2f5ec;font-size:8px;font-weight:700}
.document-empty{display:grid!important;max-width:310px;margin:auto;place-content:center;justify-items:center;gap:8px;color:#7790a4;text-align:center;line-height:1.6}.document-empty-icon{display:grid;width:58px;height:58px;place-items:center;border:1px solid #cde0ec;border-radius:18px;color:#3480af;background:linear-gradient(145deg,#fff,#eaf5fb);box-shadow:0 10px 30px rgba(30,94,135,.1)}.document-empty strong{color:#315875;font-size:12px}.document-empty>span:last-child{font-size:9px}
.execution-panel.document-focused .document-workspace{grid-template-columns:290px minmax(0,1fr)}
.execution-panel.document-focused .document-index{border-right:1px solid #d5e2ea}
.execution-panel.document-focused .document-reader{display:grid;grid-template-rows:auto minmax(0,1fr)}
.document-reader :deep(.classroom){width:100%;max-width:100%;height:100%;min-width:0;min-height:0;box-sizing:border-box;border:0;border-radius:0;display:grid;grid-template-rows:auto auto auto minmax(0,1fr) auto}
.document-reader :deep(.lesson-stage){min-height:0}.document-reader :deep(.document-surface),.document-reader :deep(.auto-board){max-height:none}.document-reader :deep(.teacher-chat){min-width:0}
@keyframes document-in{from{opacity:.4;transform:translateX(12px)}}
@media(max-width:1100px){.overlay-layout{grid-template-columns:160px minmax(320px,1fr)}.execution-panel{display:none}.execution-panel.document-focused{display:flex}.agent-dialog{width:calc(100vw - 32px);height:calc(100vh - 32px)}.agent-dialog-layer{padding:16px}}
@media(max-width:720px){.conversation-sidebar{display:none}.overlay-layout{grid-template-columns:1fr}.agent-dialog-header{padding:0 10px}.agent-dialog{width:100vw;height:100vh;border-radius:0}.agent-dialog-layer{padding:0}.agent-dialog-identity span{display:none}.execution-panel.document-focused .document-workspace{grid-template-columns:1fr}.execution-panel.document-focused .document-index{max-height:174px;border-right:0;border-bottom:1px solid #dbe7ef}.document-library-head,.document-summary,.document-compact-preview{display:none}.document-card-list{flex-direction:row;overflow-x:auto}.document-card-list>button{min-width:220px}.document-reader-header{min-height:62px;padding:8px 10px}.document-ready{display:none}.document-reader-actions>button{width:29px;padding:0;justify-content:center}.document-reader-actions>button{font-size:0}}
.agent-dialog-layer{inset:auto;width:min(1400px,calc(100vw - 24px));height:min(880px,calc(100vh - 24px));display:block;padding:0}.agent-dialog{width:100%;height:100%}.agent-dialog-header{cursor:move;user-select:none}.agent-dialog-actions,.agent-dialog-actions button{cursor:default}.agent-dialog-actions button{cursor:pointer}@media(max-width:1100px){.agent-dialog-layer{left:16px!important;top:70px!important;width:calc(100vw - 32px);height:calc(100vh - 86px);padding:0}.agent-dialog{width:100%;height:100%}}@media(max-width:720px){.agent-dialog-layer{left:0!important;top:0!important;width:100vw;height:100vh}.agent-dialog{width:100%;height:100%}}
.agent-welcome{max-width:520px!important;padding:28px!important}.agent-welcome>span{max-width:440px;color:#5f7a90!important;line-height:1.7}.agent-welcome>small{color:#91a1ad;font-size:8px}.suggested-questions{width:100%;margin:5px 0;display:flex;flex-wrap:wrap;justify-content:center;gap:7px}.suggested-questions button{max-width:100%;padding:7px 10px;border:1px solid #bcd7e8;border-radius:99px;overflow:hidden;color:#26658e;background:#f3f9fd;font-size:8px;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}.suggested-questions button:hover{color:#fff;border-color:#1769b5;background:#1769b5}
.overlay-layout{align-items:stretch}
.overlay-layout>.chat-panel{height:100%;min-height:0;overflow:hidden}
.overlay-layout>.conversation-sidebar{min-height:0;display:flex;flex-direction:column;overflow:hidden}
.overlay-layout .conversation-list{min-height:0;max-height:none;flex:1}
.overlay-layout>.execution-panel{height:100%;min-height:0;overflow:hidden}
.execution-panel>.execution-title,.execution-panel>.execution-tabs,.execution-panel>.trace-note{flex:0 0 auto}
.overlay-layout .message-pane{height:auto;min-height:0;max-height:none}
.overlay-layout .step-list,.overlay-layout .research-list,.overlay-layout .rag-settings-panel{min-height:0;max-height:none;flex:1 1 0;overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;touch-action:pan-y}
.overlay-layout .document-workspace{min-height:0;flex:1 1 0}
.overlay-layout .step-list,.overlay-layout .research-list,.overlay-layout .rag-settings-panel{scrollbar-width:thin;scrollbar-color:#9db9cc transparent}
.overlay-layout .step-list::-webkit-scrollbar,.overlay-layout .research-list::-webkit-scrollbar,.overlay-layout .rag-settings-panel::-webkit-scrollbar{width:7px}
.overlay-layout .step-list::-webkit-scrollbar-thumb,.overlay-layout .research-list::-webkit-scrollbar-thumb,.overlay-layout .rag-settings-panel::-webkit-scrollbar-thumb{border:2px solid transparent;border-radius:99px;background:#9db9cc;background-clip:padding-box}
.message-copy{width:100%}
.markdown-message{width:100%;box-sizing:border-box;white-space:normal}
.markdown-message :deep(> :first-child){margin-top:0}
.markdown-message :deep(> :last-child){margin-bottom:0}
.markdown-message :deep(h1),.markdown-message :deep(h2),.markdown-message :deep(h3),.markdown-message :deep(h4){margin:17px 0 8px;color:#173f60;line-height:1.35}
.markdown-message :deep(h1){padding-bottom:8px;border-bottom:1px solid #cbdde9;font-size:18px}
.markdown-message :deep(h2){padding-bottom:6px;border-bottom:1px solid #d6e4ed;font-size:16px}
.markdown-message :deep(h3){font-size:14px}.markdown-message :deep(h4){font-size:13px}
.markdown-message :deep(p){margin:0 0 10px;padding:0;border:0;border-radius:0;color:inherit;background:transparent;font-size:12px;line-height:1.75;white-space:normal}
.markdown-message :deep(strong){margin:0;color:#163e5e;font-size:inherit}
.markdown-message :deep(hr){height:1px;margin:15px 0;border:0;background:#cedde7}
.markdown-message :deep(blockquote){margin:10px 0;padding:9px 12px;border-left:3px solid #2b88bd;border-radius:0 7px 7px 0;color:#49697e;background:#e7f2f8}
.markdown-message :deep(blockquote p){margin:0}
.markdown-message :deep(ul),.markdown-message :deep(ol){margin:8px 0 11px;padding-left:23px}
.markdown-message :deep(li){margin:4px 0;padding-left:2px;line-height:1.7}
.markdown-message :deep(table){display:block;width:100%;margin:11px 0;overflow-x:auto;border-collapse:collapse;font-size:11px}
.markdown-message :deep(th),.markdown-message :deep(td){min-width:90px;padding:7px 9px;border:1px solid #bfd1dd;text-align:left;vertical-align:top}
.markdown-message :deep(th){color:#224d69;background:#deedf5;font-weight:750}
.markdown-message :deep(tr:nth-child(even) td){background:rgba(255,255,255,.56)}
.markdown-message :deep(code){padding:2px 5px;border-radius:4px;color:#b13e58;background:#e5edf3;font:10px/1.5 Consolas,monospace}
.markdown-message :deep(pre){margin:10px 0;padding:11px;overflow:auto;border-radius:8px;color:#dce9f2;background:#17364e}
.markdown-message :deep(pre code){padding:0;color:inherit;background:transparent}
.markdown-message :deep(a){color:#126fb5;text-decoration:none}.markdown-message :deep(a:hover){text-decoration:underline}
.agent-dialog-layer.is-fullscreen{left:0!important;top:0!important;width:100vw!important;height:100vh!important;padding:0!important}
.agent-dialog-layer.is-fullscreen .agent-dialog{width:100%;height:100%;border:0;border-radius:0}
.agent-dialog-layer.is-fullscreen .agent-dialog-header{cursor:default}
.agent-dialog-layer.is-fullscreen .overlay-layout{grid-template-columns:210px minmax(420px,1fr) 410px}
.execution-tabs{grid-template-columns:repeat(4,minmax(0,1fr))}
.execution-tabs button{min-width:0;padding-inline:4px}
.rag-mode-badge{margin-left:auto;padding:4px 7px;border-radius:99px;color:#08724f;background:#e2f6ec;font-size:7px;font-weight:800;letter-spacing:.7px}
.rag-settings-panel{min-width:0;min-height:0;flex:1;padding:11px;display:flex;flex-direction:column;gap:9px;overflow-x:hidden;overflow-y:auto;background:linear-gradient(180deg,#f7fafc,#edf4f8)}
.rag-settings-intro{padding:11px;display:flex;align-items:center;justify-content:space-between;gap:8px;border:1px solid #bcd9ea;border-radius:11px;background:linear-gradient(135deg,#fff,#ebf7fd);box-shadow:0 5px 16px rgba(22,91,137,.07)}
.rag-settings-intro>div{display:flex;min-width:0;align-items:center;gap:8px}.rag-settings-intro>div>span{display:grid;width:31px;height:31px;flex:none;place-items:center;border-radius:9px;color:#fff;background:linear-gradient(135deg,#1769b5,#28a0ad)}.rag-settings-intro>div>div{display:flex;min-width:0;flex-direction:column;gap:2px}.rag-settings-intro strong{color:#204d6e;font-size:10px}.rag-settings-intro small{color:#748b9d;font-size:7px}
.rag-master-switch{display:flex;flex:none;align-items:center;gap:5px;color:#517086;font-size:8px;font-weight:700;cursor:pointer}.rag-master-switch input{position:absolute;opacity:0}.rag-master-switch i{position:relative;width:27px;height:15px;border-radius:99px;background:#aebdc8;transition:.16s ease}.rag-master-switch i::after{content:"";position:absolute;left:2px;top:2px;width:11px;height:11px;border-radius:50%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.18);transition:.16s ease}.rag-master-switch input:checked+i{background:#168a75}.rag-master-switch input:checked+i::after{transform:translateX(12px)}
.rag-setting-card{padding:11px;border:1px solid #d3e1ea;border-radius:11px;background:rgba(255,255,255,.94);box-shadow:0 4px 13px rgba(22,61,87,.04)}
.rag-setting-card>header{margin-bottom:9px;display:flex;align-items:center;justify-content:space-between;gap:8px}.rag-setting-card>header strong{color:#244e6b;font-size:9px}.rag-setting-card>header span{color:#8397a6;font-size:7px}
.rag-choice-list{display:flex;flex-wrap:wrap;gap:5px}.rag-choice-list button{max-width:100%;padding:5px 7px;display:flex;align-items:center;gap:4px;border:1px solid #cbdce7;border-radius:7px;overflow:hidden;color:#547289;background:#f8fbfd;font-size:8px;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}.rag-choice-list button:hover,.rag-choice-list button.active{color:#126bb1;border-color:#7db4d8;background:#eaf6fd}.rag-choice-list>small{color:#8b9daa;font-size:8px}.rag-choice-list.groups button{border-radius:99px}
.rag-field-label{margin:10px 0 6px;display:block;color:#687f91;font-size:7px;font-weight:700}
.rag-range-field{display:flex;flex-direction:column;gap:5px}.rag-range-field>span{display:flex;align-items:center;justify-content:space-between;color:#607b8f;font-size:8px}.rag-range-field b{color:#176dae}.rag-range-field input{width:100%;accent-color:#168aa0}
.rag-two-columns,.rag-three-columns{margin-top:8px;display:grid;gap:6px}.rag-two-columns{grid-template-columns:repeat(2,minmax(0,1fr))}.rag-three-columns{grid-template-columns:repeat(3,minmax(0,1fr))}.rag-two-columns label,.rag-three-columns label,.rag-wide-input{display:flex;min-width:0;flex-direction:column;gap:4px}.rag-two-columns span,.rag-three-columns span,.rag-wide-input span{color:#667f91;font-size:7px}.rag-two-columns input,.rag-three-columns input,.rag-wide-input input{width:100%;min-width:0;height:27px;box-sizing:border-box;padding:0 7px;border:1px solid #cadbe6;border-radius:6px;color:#234d69;background:#fbfdfe;font-size:8px;outline:none}.rag-two-columns input:focus,.rag-three-columns input:focus,.rag-wide-input input:focus{border-color:#5fa5d1;box-shadow:0 0 0 2px rgba(76,161,213,.11)}.rag-wide-input{margin-top:8px}.history-input{max-width:120px}
.rag-toggle-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.rag-toggle-grid>label{min-width:0;padding:7px;display:flex;align-items:flex-start;gap:6px;border:1px solid #d8e4ec;border-radius:8px;background:#f9fbfc;cursor:pointer}.rag-toggle-grid input{margin:1px 0 0;accent-color:#168a75}.rag-toggle-grid span{display:flex;min-width:0;flex-direction:column;gap:2px}.rag-toggle-grid b{color:#3d627a;font-size:8px}.rag-toggle-grid small{overflow:hidden;color:#8a9ba8;font-size:7px;text-overflow:ellipsis;white-space:nowrap}
.rag-settings-footer{position:sticky;bottom:-11px;margin:-1px -11px -11px;padding:9px 11px;display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid #d5e2eb;background:rgba(247,250,252,.96);backdrop-filter:blur(8px)}.rag-settings-footer span{display:flex;align-items:center;gap:3px;color:#778e9f;font-size:7px}.rag-settings-footer button{height:29px;padding:0 10px;display:flex;align-items:center;gap:5px;border:0;border-radius:7px;color:#fff;background:linear-gradient(135deg,#1769b5,#168d9b);font-size:8px;font-weight:800;box-shadow:0 5px 13px rgba(23,105,181,.2);cursor:pointer}.rag-settings-footer button:disabled{opacity:.55;cursor:wait}
@media(max-width:1100px){.agent-dialog-layer.is-fullscreen .overlay-layout{grid-template-columns:180px minmax(360px,1fr)}}
@media(max-width:720px){.agent-dialog-layer.is-fullscreen .overlay-layout{grid-template-columns:1fr}.execution-tabs{grid-template-columns:repeat(4,minmax(56px,1fr))}}
</style>
