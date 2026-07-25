<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Activity, Bot, Check, ChevronDown, ExternalLink, FileText, FolderLock,
  Globe2, HardDrive, Maximize2, MessageSquarePlus, Minimize2, Minus, Send, Shield,
  Sparkles, UserRound, X,
} from 'lucide-vue-next'
import DocumentClassroom from './DocumentClassroom.vue'
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
const panelTab = ref<'steps'|'web'|'docs'>('steps')
const documentFocus = ref(false)
const selectedArtifactId = ref('')
const securityRuntime = ref<Entity | null>(null)
const securityProfile = ref('default')
const securityMenu = ref(false)
const messagePane = ref<HTMLElement | null>(null)
const drag = reactive({ active: false, moved: false, offsetX: 0, offsetY: 0 })
let pollTimer: number | undefined

const chatWindow = computed(() => chat.windows.find(item => item.id === props.windowId) || null)
const agent = computed(() => chatWindow.value?.agent || null)
const webEvents = computed(() => steps.value.filter(item => ['web_search_started','web_search_results','research_sources_selected','web_fetch_started','web_page_fetched','web_research_empty'].includes(item.type)))
const activeArtifact = computed(() =>
  artifacts.value.find(item => item.id === selectedArtifactId.value) || artifacts.value[0] || null,
)
const sourceDecision = computed(() => Object.fromEntries(sourceReviews.value.map(item => [item.url, item.decision])))
const windowStyle = computed(() => ({
  left: `${chatWindow.value?.position.x || 12}px`,
  top: `${chatWindow.value?.position.y || 64}px`,
  zIndex: chatWindow.value?.zIndex || 920,
}))
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
  securityProfile.value = permissions.security_profile || 'default'
  try {
    const [items, runtime] = await Promise.all([
      api.get<Entity[]>(`/agents/${nextAgent.id}/conversations`),
      api.get<Entity>('/security/runtime'),
    ])
    if (agent.value?.id !== nextAgent.id) return
    conversations.value = items
    securityRuntime.value = runtime
    if (items.length) await openConversation(items[0])
  } catch (error: any) {
    app.notify(error.message, 'error')
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

function knowledgeArchive(traceValue: string | Entity[] | undefined) {
  const trace = Array.isArray(traceValue) ? traceValue : parse(traceValue || '[]')
  return [...trace].reverse().find(item => item.type === 'knowledge_archived')
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
        const archived = knowledgeArchive(assistantMessage.trace_json)
        const knowledgeBaseNames = archived?.knowledge_base_names || []
        if (assistantMessage.run_status === 'completed') {
          chat.finishTask(taskId, 'completed', {
            runId: assistantMessage.run_id || runId,
            knowledgeBaseNames,
            detail: knowledgeBaseNames.length
              ? `已完成并写入：${knowledgeBaseNames.join('、')}`
              : '任务已完成',
          })
          app.notify(knowledgeBaseNames.length
            ? `Agent 任务已完成，已写入知识库：${knowledgeBaseNames.join('、')}`
            : 'Agent 后台任务已完成')
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
  let archivedNames: string[] = []
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
        if (event.step.type === 'knowledge_archived') {
          archivedNames = event.step.knowledge_base_names || []
          chat.updateTask(taskId, {
            knowledgeBaseNames: archivedNames,
            detail: `已向量化写入：${archivedNames.join('、')}`,
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
        knowledgeBaseNames: archivedNames,
        detail: archivedNames.length ? `已完成并写入：${archivedNames.join('、')}` : '任务已完成',
      })
      app.notify(archivedNames.length
        ? `Agent 任务已完成，已写入知识库：${archivedNames.join('、')}`
        : 'Agent 本轮执行完成')
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
      app.notify('对话已转入后台，任务会继续执行并自动写入知识库')
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

function selectPanel(tab: 'steps'|'web'|'docs') {
  panelTab.value = tab
  if (tab !== 'docs') documentFocus.value = false
}

function openDocuments(artifactId?: string) {
  if (artifactId) selectedArtifactId.value = artifactId
  else if (!selectedArtifactId.value && artifacts.value.length) selectedArtifactId.value = artifacts.value[0].id
  panelTab.value = 'docs'
  documentFocus.value = true
}

function stepTitle(step: Entity) {
  const labels: Record<string, string> = {
    request_submitted: '任务已提交', stream_connected: '已连接 Agent 执行引擎',
    context_ready: '会话上下文已装载', research_planning: '生成检索计划',
    research_sources_selected: `选定 ${step.count || 0} 条高相关来源`,
    web_research_empty: '联网研究未取得来源', research_synthesis_started: '开始多来源综合',
    quality_review_started: '第二轮质量审校', quality_review_skipped: '质量审校已降级',
    model_waiting: '正在等待模型响应', run_completed: '运行完成',
    knowledge_archived: '任务成果已写入知识库',
    knowledge_archive_failed: '任务成果入库失败',
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
  if (step.type === 'model_response') return `第 ${step.iteration} 轮模型响应`
  if (step.type === 'web_search_results') return `${step.results?.slice(0,2).map((item:Entity)=>item.title).join('；') || '没有通过过滤的结果'}`
  if (step.type === 'web_page_fetched') return `${step.status} · ${step.title || step.url}`
  if (step.type === 'model_waiting') return `已等待 ${step.elapsed_seconds || 0} 秒`
  if (step.type === 'run_completed') return `${step.duration_ms} ms · ${step.token_usage} tokens`
  if (step.type === 'knowledge_archived') return `${step.knowledge_base_names?.join('、') || '知识库'} · 已完成分块与向量化`
  if (step.type === 'knowledge_archive_failed') return step.message || '请检查知识库向量化配置'
  return '可审计执行事件'
}

function startDrag(event: PointerEvent) {
  if (!chatWindow.value) return
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
  const width = Math.min(1120, window.innerWidth - 40)
  const height = Math.min(740, window.innerHeight - 40)
  chat.move(props.windowId, {
    x: Math.min(Math.max(12, event.clientX - drag.offsetX), window.innerWidth - width - 12),
    y: Math.min(Math.max(64, event.clientY - drag.offsetY), window.innerHeight - height - 12),
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
        :style="windowStyle"
        @pointerdown="chat.focus(windowId)"
      >
        <section class="agent-dialog" role="dialog" :aria-label="`与 ${agent.name} 对话`">
          <header class="agent-dialog-header" @pointerdown="startDrag">
            <div class="agent-dialog-identity"><span class="agent-dialog-avatar"><Bot :size="19" /></span><div><small>AGENT CONVERSATION</small><strong>{{ agent.name }}</strong><span>{{ chatRunning ? '正在执行任务' : '会话已就绪' }}</span></div></div>
            <div class="agent-dialog-actions" @pointerdown.stop><StatusBadge v-if="currentRunStatus!=='idle'" :status="currentRunStatus" /><button class="document-shortcut" :class="{ active: documentFocus }" :title="`打开文档工作区（${artifacts.length}）`" @click="openDocuments()"><FileText :size="16" /><b v-if="artifacts.length">{{ artifacts.length }}</b></button><button title="最小化到 Agent 堆叠区" @click="chat.minimize(windowId)"><Minus :size="17" /></button><button title="关闭对话，当前任务仍会后台完成" @click="endChat"><X :size="17" /></button></div>
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
                <div v-if="!messages.length" class="chat-empty"><Sparkles :size="25" /><strong>开始与 {{ agent.name }} 对话</strong><span>可以连续追问；切换页面时会自动缩成浮窗。</span></div>
                <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role" @click="showMessageTrace(message)">
                  <div class="message-avatar"><UserRound v-if="message.role==='user'" :size="15" /><Bot v-else :size="15" /></div>
                  <div><strong>{{ message.role==='user' ? '你' : agent.name }}{{ message.role==='assistant' && message.trace_json ? ' · 点击回放步骤' : '' }}</strong><p>{{ message.content }}</p></div>
                </article>
                <article v-if="chatRunning" class="chat-message assistant"><div class="message-avatar"><Bot :size="15" /></div><div><strong>{{ agent.name }}</strong><p class="typing">正在执行任务并整理回复</p></div></article>
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
                <span><FileText v-if="documentFocus" :size="15" /><Activity v-else :size="15" />{{ documentFocus ? '文档工作区' : '运行与交付' }}</span>
                <button v-if="panelTab==='docs'" class="document-focus-toggle" :title="documentFocus ? '返回对话并排视图' : '展开文档工作区'" @click="documentFocus=!documentFocus"><Minimize2 v-if="documentFocus" :size="14" /><Maximize2 v-else :size="14" />{{ documentFocus ? '返回对话' : '展开阅读' }}</button>
                <span v-else class="live-dot" :class="{ running: chatRunning }">{{ chatRunning ? 'LIVE' : 'TRACE' }}</span>
              </div>
              <div class="execution-tabs"><button :class="{active:panelTab==='steps'}" @click="selectPanel('steps')"><Activity :size="12" />步骤</button><button :class="{active:panelTab==='web'}" @click="selectPanel('web')"><Globe2 :size="12" />网页 {{ webEvents.length }}</button><button :class="{active:panelTab==='docs'}" @click="openDocuments()"><FileText :size="12" />文档 {{ artifacts.length }}</button></div>
              <div v-if="panelTab==='steps'" class="step-list">
                <div v-for="(step,index) in steps" :key="index" class="step-item" :class="{ error: step.type==='error' }"><span class="step-index">{{ index + 1 }}</span><div><strong>{{ stepTitle(step) }}</strong><p>{{ stepMeta(step) }}</p><div v-if="step.type==='approval_required' && !step.status" class="inline-approval"><button :disabled="step.deciding" @click="decideInChat(step,true)"><Check :size="11" />批准执行</button><button :disabled="step.deciding" class="reject" @click="decideInChat(step,false)"><X :size="11" />拒绝</button></div><span v-else-if="step.type==='approval_required' && step.status" class="approval-state">{{ step.status==='approved' ? '已批准' : '已拒绝' }}</span></div></div>
                <div v-if="!steps.length" class="empty compact">发送消息后，这里会实时显示模型、工具、MCP、Agent 联动和审批事件。</div>
              </div>
              <div v-else-if="panelTab==='web'" class="research-list">
                <section v-if="selectedWebSource" class="web-preview"><header><strong>{{ selectedWebSource.title || '来源网页' }}</strong><button @click="selectedWebSource=null"><X :size="13" /></button></header><div class="preview-actions"><a :href="selectedWebSource.url" target="_blank" rel="noreferrer"><ExternalLink :size="11" />浏览器打开原文</a><button class="confirm" @click="reviewSource(selectedWebSource,'confirmed')"><Check :size="11" />确认采用</button><button class="exclude" @click="reviewSource(selectedWebSource,'excluded')"><X :size="11" />排除</button></div><iframe :src="selectedWebSource.url" :title="selectedWebSource.title" sandbox="allow-scripts allow-same-origin allow-popups" /></section>
                <article v-for="(event,index) in webEvents" :key="index" class="research-card"><div><Globe2 :size="13" /><strong>{{ stepTitle(event) }}</strong></div><button v-if="event.url" class="source-link" @click="selectedWebSource=event"><ExternalLink :size="11" />{{ event.title || event.url }}</button><div v-if="event.url" class="source-review-actions"><button @click="reviewSource(event,'confirmed')"><Check :size="10" />确认</button><button @click="reviewSource(event,'excluded')"><X :size="10" />排除</button><span v-if="sourceDecision[event.url]">{{ sourceDecision[event.url]==='confirmed' ? '已采用' : '已排除' }}</span></div></article>
                <div v-if="!webEvents.length" class="empty compact">研究类任务开始后，这里会显示检索、抓取和来源审查。</div>
              </div>
              <div v-else class="document-workspace">
                <aside v-if="artifacts.length" class="document-index">
                  <header><span>交付文档</span><small>{{ artifacts.length }} 份</small></header>
                  <button v-for="artifact in artifacts" :key="artifact.id" :class="{ active: activeArtifact?.id===artifact.id }" @click="selectedArtifactId=artifact.id">
                    <span><FileText :size="14" /><strong>{{ artifact.title }}</strong></span>
                    <small>{{ artifact.relative_path }}</small>
                  </button>
                  <p v-if="!documentFocus"><Maximize2 :size="12" />点击“展开阅读”查看完整正文与 AI 板书。</p>
                </aside>
                <main v-if="activeArtifact" class="document-reader">
                  <header class="document-reader-header">
                    <div><span>正在阅读</span><strong>{{ activeArtifact.title }}</strong><small :title="activeArtifact.relative_path">{{ activeArtifact.relative_path }}</small></div>
                    <span class="document-ready"><Check :size="12" />已生成</span>
                  </header>
                  <DocumentClassroom :artifact="activeArtifact" :agent-name="agent.name" :conversation-id="activeConversation?.id || ''" @ask="askTeacher" />
                </main>
                <div v-if="!artifacts.length" class="empty compact document-empty"><FileText :size="25" /><strong>还没有交付文档</strong><span>研究任务完成后，完整正文会显示在这里。</span></div>
              </div>
              <div v-if="panelTab!=='docs'" class="trace-note">仅展示操作轨迹与依据，不展示模型内部隐式推理。</div>
            </aside>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.agent-dialog-layer{position:fixed;inset:0;z-index:900;display:flex;align-items:center;justify-content:center;padding:32px}.agent-dialog-backdrop{position:absolute;inset:0;border:0;background:rgba(8,28,48,.46);backdrop-filter:blur(5px);cursor:default}.agent-dialog{position:relative;width:min(1440px,calc(100vw - 64px));height:min(820px,calc(100vh - 64px));overflow:hidden;border:1px solid #bdd4e5;border-radius:16px;background:#fff;box-shadow:0 30px 90px rgba(8,31,52,.32)}.agent-dialog-header{height:68px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #dde8f0;background:linear-gradient(120deg,#f8fbfe,#eef7fd)}.agent-dialog-identity{display:flex;align-items:center;gap:11px}.agent-dialog-avatar{display:grid;width:39px;height:39px;place-items:center;border-radius:11px;color:#fff;background:linear-gradient(135deg,#1266bb,#22a0c4)}.agent-dialog-identity>div{display:grid;grid-template-columns:auto auto;align-items:end;gap:2px 8px}.agent-dialog-identity small{grid-column:1/-1;font-size:8px;letter-spacing:1.4px;color:#6e879b}.agent-dialog-identity strong{font-size:15px;color:#153b62}.agent-dialog-identity span{font-size:9px;color:#7890a5}.agent-dialog-actions{display:flex;align-items:center;gap:7px}.agent-dialog-actions>button{position:relative;display:grid;width:31px;height:31px;place-items:center;border:1px solid #cbdce9;border-radius:8px;color:#587289;background:#fff;cursor:pointer}.agent-dialog-actions>button:hover,.agent-dialog-actions>button.active{color:#1269bd;border-color:#8fbee0;background:#f1f8fd}.document-shortcut b{position:absolute;right:-5px;top:-6px;min-width:16px;height:16px;padding:0 4px;display:grid;place-items:center;border:2px solid #f4f9fd;border-radius:99px;color:#fff;background:#1769c2;font-size:8px}.overlay-layout{height:calc(100% - 68px);min-height:0;grid-template-columns:190px minmax(360px,1fr) 370px}.overlay-layout :deep(.message-pane){max-height:none}.chat-composer{position:relative;padding-top:29px!important}.composer-security{position:absolute;top:7px;left:12px;right:12px;display:flex;align-items:center;gap:6px;color:#617f97;font-size:8px}.composer-security button{margin-left:auto;border:0;color:#1769c2;background:transparent;font-size:8px;cursor:pointer}.security-menu{position:absolute;z-index:40;width:300px;padding:7px;border:1px solid #cbddea;border-radius:10px;background:#fff;box-shadow:0 14px 36px #143d5c2e}.composer-menu{right:12px;bottom:100%}.security-menu>button{display:grid;width:100%;grid-template-columns:auto 1fr auto;align-items:center;gap:9px;padding:9px;border:0;border-radius:7px;text-align:left;color:#6a8296;background:transparent;cursor:pointer}.security-menu>button:hover,.security-menu>button.active{color:#1769c2;background:#edf6fd}.security-menu span{display:flex;flex-direction:column;gap:2px}.security-menu strong{font-size:10px;color:#315875}.security-menu small{font-size:8px}.security-menu>p{margin:6px 5px 2px;padding-top:7px;border-top:1px solid #e3ebf2;color:#7890a3;font-size:8px}.inline-approval{display:flex;gap:5px;margin-top:7px}.inline-approval button{display:inline-flex;align-items:center;gap:3px;padding:4px 7px;border:1px solid #8ac4ac;border-radius:5px;color:#137653;background:#edfaf4;font-size:8px;cursor:pointer}.inline-approval button.reject{border-color:#e3adad;color:#a73c3c;background:#fff5f5}.approval-state{display:inline-block;margin-top:5px;color:#187855;font-size:8px;font-weight:700}.agent-chat-float{position:fixed;z-index:880;width:260px;height:72px;padding:9px 10px 9px 6px;display:flex;align-items:center;gap:9px;border:1px solid #87b8da;border-radius:14px;color:#244c6f;background:rgba(255,255,255,.96);box-shadow:0 14px 38px rgba(17,64,99,.25);backdrop-filter:blur(10px);cursor:grab;touch-action:none;user-select:none}.agent-chat-float.dragging{cursor:grabbing;box-shadow:0 18px 45px rgba(17,64,99,.34)}.float-grip{flex:none;color:#9ab0c1}.float-avatar{position:relative;display:grid;width:39px;height:39px;flex:none;place-items:center;border-radius:11px;color:#fff;background:linear-gradient(135deg,#1769c2,#25a5bc)}.float-avatar i{position:absolute;right:-2px;bottom:-2px;width:9px;height:9px;border:2px solid #fff;border-radius:50%;background:#20b774}.float-copy{display:flex;min-width:0;flex:1;flex-direction:column;text-align:left}.float-copy small{font-size:8px;color:#7991a6}.float-copy strong{overflow:hidden;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.float-open{font-size:8px;font-weight:700;color:#1769c2}.float-close{display:grid;width:22px;height:22px;place-items:center;border-radius:6px;color:#8096a8}.float-close:hover{color:#b44444;background:#fff1f1}.agent-dialog-enter-active,.agent-dialog-leave-active,.agent-float-enter-active,.agent-float-leave-active{transition:opacity .18s ease,transform .18s ease}.agent-dialog-enter-from,.agent-dialog-leave-to{opacity:0}.agent-dialog-enter-from .agent-dialog,.agent-dialog-leave-to .agent-dialog{transform:translateY(12px) scale(.985)}.agent-float-enter-from,.agent-float-leave-to{opacity:0;transform:translateY(8px) scale(.96)}
.execution-panel.document-focused{position:absolute;inset:68px 0 0;z-index:30;border-left:0;background:#f4f9fd;animation:document-in .18s ease}.document-focus-toggle{margin-left:auto;padding:6px 9px;border:1px solid #b9d4e8;border-radius:7px;display:flex;align-items:center;gap:5px;color:#1769c2;background:#fff;font-size:9px;font-weight:700;cursor:pointer}.document-workspace{min-height:0;flex:1;display:grid;grid-template-columns:1fr;overflow:hidden}.document-index{min-width:0;padding:10px;overflow:auto;background:#f4f9fd}.document-index>header{height:34px;padding:0 4px;display:flex;align-items:center;justify-content:space-between;color:#315875}.document-index>header span{font-size:10px;font-weight:800}.document-index>header small{padding:3px 6px;border-radius:99px;color:#1769c2;background:#e1f0fb;font-size:8px}.document-index>button{width:100%;margin-bottom:7px;padding:9px;border:1px solid #d6e4ee;border-radius:8px;display:block;text-align:left;color:#496a84;background:#fff;cursor:pointer}.document-index>button:hover,.document-index>button.active{border-color:#88bce2;background:#eaf5fd;box-shadow:0 5px 15px rgba(20,84,130,.08)}.document-index>button span{display:flex;align-items:center;gap:6px;min-width:0}.document-index>button strong{overflow:hidden;color:#274f70;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.document-index>button small{display:block;margin:6px 0 0;padding-left:20px;overflow:hidden;color:#8397a8;font-size:8px;text-overflow:ellipsis;white-space:nowrap}.document-index>p{margin:9px 2px;padding:9px;display:flex;align-items:flex-start;gap:5px;border-radius:6px;color:#54748e;background:#e8f2f9;font-size:8px;line-height:1.55}.document-reader{display:none;min-width:0;min-height:0;overflow:hidden;background:#fff}.document-reader-header{height:55px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid #dce7ef;background:#fff}.document-reader-header>div{display:grid;min-width:0;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:2px 8px}.document-reader-header span{color:#7890a3;font-size:8px}.document-reader-header strong{overflow:hidden;color:#1f4c71;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.document-reader-header small{grid-column:1/-1;overflow:hidden;color:#8a9cac;font-size:8px;text-overflow:ellipsis;white-space:nowrap}.document-ready{flex:none;padding:5px 8px;display:flex;align-items:center;gap:4px!important;border-radius:99px;color:#08734c!important;background:#e2f5ec;font-weight:700}.document-empty{display:grid!important;place-content:center;justify-items:center;gap:7px;color:#7790a4}.document-empty strong{color:#315875}.execution-panel.document-focused .document-workspace{grid-template-columns:220px minmax(0,1fr)}.execution-panel.document-focused .document-index{border-right:1px solid #dbe7ef}.execution-panel.document-focused .document-reader{display:grid;grid-template-rows:auto minmax(0,1fr)}.document-reader :deep(.classroom){height:100%;min-height:0;border:0;border-radius:0;display:grid;grid-template-rows:auto auto auto minmax(0,1fr) auto}.document-reader :deep(.lesson-stage){min-height:0}.document-reader :deep(.document-surface),.document-reader :deep(.auto-board){max-height:none}.document-reader :deep(.teacher-chat){min-width:0}@keyframes document-in{from{opacity:.4;transform:translateX(12px)}}@media(max-width:1100px){.overlay-layout{grid-template-columns:160px minmax(320px,1fr)}.execution-panel{display:none}.execution-panel.document-focused{display:flex}.agent-dialog{width:calc(100vw - 32px);height:calc(100vh - 32px)}.agent-dialog-layer{padding:16px}}@media(max-width:720px){.conversation-sidebar{display:none}.overlay-layout{grid-template-columns:1fr}.agent-dialog-header{padding:0 10px}.agent-dialog{width:100vw;height:100vh;border-radius:0}.agent-dialog-layer{padding:0}.agent-dialog-identity span{display:none}.execution-panel.document-focused .document-workspace{grid-template-columns:1fr}.execution-panel.document-focused .document-index{max-height:126px;border-right:0;border-bottom:1px solid #dbe7ef;display:flex;gap:7px}.document-index>header,.document-index>p{display:none}.document-index>button{min-width:180px;margin:0}.document-reader-header{height:48px}}
.agent-dialog-layer{inset:auto;width:min(1120px,calc(100vw - 40px));height:min(740px,calc(100vh - 40px));display:block;padding:0}.agent-dialog{width:100%;height:100%}.agent-dialog-header{cursor:move;user-select:none}.agent-dialog-actions,.agent-dialog-actions button{cursor:default}.agent-dialog-actions button{cursor:pointer}@media(max-width:1100px){.agent-dialog-layer{left:16px!important;top:70px!important;width:calc(100vw - 32px);height:calc(100vh - 86px);padding:0}.agent-dialog{width:100%;height:100%}}@media(max-width:720px){.agent-dialog-layer{left:0!important;top:0!important;width:100vw;height:100vh}.agent-dialog{width:100%;height:100%}}
</style>
