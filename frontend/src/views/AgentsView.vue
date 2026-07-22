<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Activity, Bot, Check, CirclePlus, MessageSquarePlus, Save, Send, Shield,
  ExternalLink, FileText, Globe2, Settings2, Sparkles, UserRound, Wrench,
  X,
} from 'lucide-vue-next'
import DocumentClassroom from '../components/DocumentClassroom.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const agents = ref<Entity[]>([]), tools = ref<Entity[]>([]), skills = ref<Entity[]>([]), bases = ref<Entity[]>([])
const policies = ref<Entity[]>([]), endpoints = ref<Entity[]>([])
const selected = ref<Entity | null>(null), conversations = ref<Entity[]>([]), activeConversation = ref<Entity | null>(null)
const messages = ref<Entity[]>([]), steps = ref<Entity[]>([]), chatInput = ref('')
const creating = ref(false), editingAgentId = ref(''), chatRunning = ref(false), messagePane = ref<HTMLElement | null>(null)
const artifacts = ref<Entity[]>([]), panelTab = ref<'steps'|'web'|'docs'>('steps')
const sourceReviews = ref<Entity[]>([]), selectedWebSource = ref<Entity | null>(null)
const currentRunId = ref(''), currentRunStatus = ref('idle')
let pollTimer: number | undefined
const form = reactive({ name: '', slug: '', description: '', system_prompt: '', model_endpoint_id: '', model: 'demo-model', temperature: 0.3, tools: [] as string[], skills: [] as string[], knowledge_bases: [] as string[], approval_policy_id: '' })
const activeAgents = computed(() => agents.value.filter(item => item.status === 'active'))
const webEvents = computed(() => steps.value.filter(item => ['web_search_started','web_search_results','research_sources_selected','web_fetch_started','web_page_fetched','web_research_empty'].includes(item.type)))
const sourceDecision = computed(() => Object.fromEntries(sourceReviews.value.map(item => [item.url, item.decision])))

async function load() {
  store.loading(true)
  try {
    [agents.value, tools.value, skills.value, bases.value, policies.value, endpoints.value] = await Promise.all([
      api.get('/agents'), api.get('/tools'), api.get('/skills'), api.get('/knowledge-bases'), api.get('/approval-policies'), api.get('/model-endpoints'),
    ])
    form.approval_policy_id ||= policies.value.find(item => item.is_default)?.id || ''
    await selectAgent(selected.value || activeAgents.value[0] || agents.value[0] || null)
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

function resetForm() {
  Object.assign(form, { name: '', slug: '', description: '', system_prompt: '', model_endpoint_id: '', model: 'demo-model', temperature: 0.3, tools: [], skills: [], knowledge_bases: [], approval_policy_id: policies.value.find(item => item.is_default)?.id || '' })
  editingAgentId.value = ''
  creating.value = true
}
function toggle(list: string[], value: string) { const index = list.indexOf(value); index >= 0 ? list.splice(index, 1) : list.push(value) }
function parse(value: string) { try { return JSON.parse(value || '[]') } catch { return [] } }
function parseObject(value: string) { try { return JSON.parse(value || '{}') } catch { return {} } }

function editAgent(agent: Entity) {
  const permissions = parseObject(agent.permissions_json)
  Object.assign(form, {
    name: agent.name,
    slug: agent.slug,
    description: agent.description,
    system_prompt: agent.system_prompt,
    model_endpoint_id: agent.model_endpoint_id || '',
    model: agent.model,
    temperature: agent.temperature,
    tools: parse(agent.tools_json),
    skills: parse(agent.skills_json),
    knowledge_bases: parse(agent.knowledge_bases_json),
    approval_policy_id: permissions.approval_policy_id || policies.value.find(item => item.is_default)?.id || '',
  })
  editingAgentId.value = agent.id
  creating.value = true
  selected.value = agent
}

function validateAgentForm() {
  if (form.name.trim().length < 2) return 'Agent 名称至少需要 2 个字符'
  if (!/^[a-z0-9][a-z0-9_-]{1,99}$/.test(form.slug)) return '唯一标识需使用小写字母、数字、下划线或连字符，且至少 2 个字符'
  if (form.system_prompt.trim().length < 10) return '系统提示词至少需要 10 个字符'
  return ''
}

async function saveAgent() {
  const validation = validateAgentForm()
  if (validation) return store.notify(validation, 'error')
  store.loading(true)
  try {
    const payload = {
      name: form.name,
      description: form.description,
      system_prompt: form.system_prompt,
      model_endpoint_id: form.model_endpoint_id || null,
      model: form.model,
      temperature: form.temperature,
      tools: form.tools,
      skills: form.skills,
      knowledge_bases: form.knowledge_bases,
      provider: form.model_endpoint_id ? 'openai-compatible' : 'demo',
      permissions: { tool_mode: 'ask', approval_policy_id: form.approval_policy_id },
    }
    const saved: Entity = editingAgentId.value
      ? await api.patch(`/agents/${editingAgentId.value}`, payload)
      : await api.post('/agents', { ...payload, slug: form.slug, is_template: true })
    store.notify(editingAgentId.value ? 'Agent 设置已更新' : 'Agent 已加入工厂')
    creating.value = false
    editingAgentId.value = ''
    await load()
    await selectAgent(agents.value.find(item => item.id === saved.id) || saved)
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

async function selectAgent(agent: Entity | null) {
  stopPolling()
  selected.value = agent
  conversations.value = []
  activeConversation.value = null
  messages.value = []
  steps.value = []
  artifacts.value = []
  sourceReviews.value = []
  selectedWebSource.value = null
  chatRunning.value = false
  currentRunStatus.value = 'idle'
  if (!agent) return
  conversations.value = await api.get(`/agents/${agent.id}/conversations`)
  if (conversations.value.length) await openConversation(conversations.value[0])
}

async function createConversation() {
  if (!selected.value) return null
  const conversation: Entity = await api.post(`/agents/${selected.value.id}/conversations`, { title: '新会话' })
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

function stopPolling() {
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
  pollTimer = undefined
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
      if (wasRunning && !silent) store.notify('Agent 本轮执行完成')
    }
  } catch (error: any) {
    if (!silent) store.notify(error.message, 'error')
  }
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
      run_id: currentRunId.value || null,
      url: source.url,
      title: source.title || source.url,
      decision,
      credibility: source.credibility || {},
    })
    sourceReviews.value = await api.get(`/conversations/${activeConversation.value.id}/source-reviews`)
    store.notify(decision === 'confirmed' ? '已确认采用该来源' : '已排除该来源')
  } catch (error: any) { store.notify(error.message, 'error') }
}

function askTeacher(question: string) {
  chatInput.value = question
  panelTab.value = 'steps'
  void sendMessage()
}

async function scrollMessages() {
  await nextTick()
  if (messagePane.value) messagePane.value.scrollTop = messagePane.value.scrollHeight
}

async function sendMessage() {
  const content = chatInput.value.trim()
  if (!selected.value || !content || chatRunning.value) return
  const conversation = activeConversation.value || await createConversation()
  if (!conversation) return
  messages.value.push({ id: `local-${Date.now()}`, role: 'user', content })
  chatInput.value = ''
  steps.value = [{ type: 'request_submitted', at: new Date().toISOString() }]
  chatRunning.value = true
  currentRunStatus.value = 'running'
  await scrollMessages()
  try {
    await api.stream(`/conversations/${conversation.id}/messages/stream`, { content }, event => {
      if (event.type === 'step') {
        if (event.step.run_id) currentRunId.value = event.step.run_id
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
        if (event.step.type === 'artifact_created') {
          panelTab.value = 'docs'
          void api.get<Entity[]>(`/conversations/${conversation.id}/artifacts`).then(value => { artifacts.value = value })
        }
      }
      if (event.type === 'assistant') messages.value.push(event.message)
      if (event.type === 'error') throw new Error(event.message)
      void scrollMessages()
    })
    store.notify('Agent 本轮执行完成')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    if (activeConversation.value?.id === conversation.id) await refreshConversation(conversation, true)
    if (selected.value) {
      conversations.value = await api.get(`/agents/${selected.value.id}/conversations`)
      activeConversation.value = conversations.value.find(item => item.id === conversation.id) || conversation
    }
  }
}

function stepTitle(step: Entity) {
  if (step.type === 'request_submitted') return '任务已提交'
  if (step.type === 'stream_connected') return '已连接 Agent 执行引擎'
  if (step.type === 'run_started') return `启动 ${step.agent}`
  if (step.type === 'context_ready') return step.knowledge_attached ? '知识库与会话上下文已装载' : '会话上下文已装载'
  if (step.type === 'research_planning') return step.mode === 'academic' ? '生成学术检索计划' : '生成网页检索计划'
  if (step.type === 'web_search_started') return `搜索：${step.query}`
  if (step.type === 'web_search_results') return `找到 ${step.count || 0} 条候选来源`
  if (step.type === 'research_sources_selected') return `选定 ${step.count || 0} 条高相关来源`
  if (step.type === 'web_fetch_started') return `抓取网页 ${step.index}`
  if (step.type === 'web_page_fetched') return `网页 ${step.index} 已解析`
  if (step.type === 'web_research_empty') return '联网研究未取得来源'
  if (step.type === 'research_synthesis_started') return '开始多来源综合'
  if (step.type === 'quality_review_started') return '第二轮质量审校'
  if (step.type === 'quality_review_skipped') return '质量审校已降级'
  if (step.type === 'artifact_created') return `已生成 ${step.title}`
  if (step.type === 'model_response') return step.tool_calls?.length ? `模型请求调用 ${step.tool_calls.join('、')}` : '模型生成回复'
  if (step.type === 'model_waiting') return '正在等待模型响应'
  if (step.type === 'tool_result') return `工具 ${step.tool} · ${step.status}`
  if (step.type === 'run_completed') return '运行完成'
  if (step.type === 'error') return `运行异常：${step.message}`
  return step.type
}

function stepMeta(step: Entity) {
  if (step.type === 'request_submitted') return '正在建立本地执行连接'
  if (step.type === 'stream_connected') return '运行轨迹将实时写入 SQLite'
  if (step.type === 'context_ready') return `历史消息 ${step.history_messages || 0} 条`
  if (step.type === 'model_response') return `第 ${step.iteration} 轮模型响应`
  if (step.type === 'research_planning') return `${step.queries?.length || 0} 组检索词`
  if (step.type === 'web_search_started') return step.mode === 'academic' ? '正在查询 Google Scholar 与学术元数据' : '正在查询普通网站与权威官网'
  if (step.type === 'web_search_results') return `${step.results?.slice(0,2).map((item:Entity)=>item.title).join('；') || '没有通过主题过滤的结果'}${step.discarded ? ` · 已排除 ${step.discarded} 条弱相关结果` : ''}`
  if (step.type === 'research_sources_selected') return '仅抓取通过核心概念约束与相关性重排的来源'
  if (step.type === 'web_fetch_started') return step.title || step.url
  if (step.type === 'web_page_fetched') return `${step.status} · ${step.title || step.url}`
  if (step.type === 'research_synthesis_started') return `${step.sources || 0} 个来源进入总结上下文`
  if (step.type === 'quality_review_started') return '检查引用、边界、结构与局限性'
  if (step.type === 'artifact_created') return step.path
  if (step.type === 'model_waiting') return `已等待 ${step.elapsed_seconds || 0} 秒 · 第 ${step.iteration || 1} 轮`
  if (step.type === 'run_completed') return `${step.duration_ms} ms · ${step.token_usage} tokens`
  return '可审计执行事件'
}

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <PageHeader eyebrow="AGENT FACTORY" title="Agent 工厂" description="创建可独立调用、互相协作并拥有专属模型、工具、知识与审批策略的 Agent。">
    <button class="btn btn-primary" @click="resetForm"><CirclePlus :size="15" />创建 Agent</button>
  </PageHeader>

  <div class="grid grid-3">
    <article v-for="agent in agents" :key="agent.id" class="card" :style="selected?.id === agent.id ? 'border-color:#65a6df' : ''">
      <div class="card-body">
        <div style="display:flex;justify-content:space-between;gap:12px"><div class="metric-icon"><Bot :size="20" /></div><StatusBadge :status="agent.status" /></div>
        <h3 style="font-size:15px;color:#153b62;margin:14px 0 5px">{{ agent.name }} <small style="color:#7890a7">v{{ agent.version }}</small></h3>
        <p style="font-size:11px;color:#657b91;line-height:1.55;min-height:34px">{{ agent.description }}</p>
        <div style="margin:8px -2px 14px"><span v-for="tool in parse(agent.tools_json).slice(0,4)" :key="tool" class="tag">{{ tool }}</span></div>
        <div style="display:grid;grid-template-columns:1fr auto;gap:7px"><button class="btn" @click="selectAgent(agent)"><MessageSquarePlus :size="14" />打开对话</button><button class="btn" title="编辑 Agent 设置" @click="editAgent(agent)"><Settings2 :size="14" /></button></div>
      </div>
    </article>
  </div>

  <section v-if="creating" class="card" style="margin-top:20px">
    <div class="card-header"><h2>{{ editingAgentId ? '修改 Agent 设置' : '创建新的 Agent 模板' }}</h2><button class="btn btn-sm" @click="creating=false;editingAgentId=''">取消</button></div>
    <div class="card-body form-grid">
      <div class="field"><label>名称</label><input v-model="form.name" class="input" placeholder="例如：文献综述 Agent"></div>
      <div class="field"><label>唯一标识</label><input v-model="form.slug" class="input" :disabled="!!editingAgentId" placeholder="literature-reviewer"><span v-if="editingAgentId" class="field-help">唯一标识用于 Agent 联动，保存后不可修改。</span></div>
      <div class="field full"><label>职责说明</label><input v-model="form.description" class="input"></div>
      <div class="field full"><label>系统提示词</label><textarea v-model="form.system_prompt" class="textarea" placeholder="明确角色、工作边界、输出规范和引用要求。" /></div>
      <div class="field"><label>大模型 API 接口</label><select v-model="form.model_endpoint_id" class="select"><option value="">离线演示模型</option><option v-for="item in endpoints.filter(endpoint => endpoint.enabled)" :key="item.id" :value="item.id">{{ item.name }} / {{ item.default_model }}</option></select></div>
      <div class="field"><label>模型名覆盖</label><input v-model="form.model" class="input"><span class="field-help">绑定 Endpoint 时默认使用接口中的模型。</span></div>
      <div class="field"><label>审批策略</label><select v-model="form.approval_policy_id" class="select"><option v-for="item in policies" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
      <div class="field"><label>Temperature</label><input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" class="input"></div>
      <div class="field full"><label>工具权限</label><div><button v-for="item in tools" :key="item.name" class="btn btn-sm" style="margin:3px" :class="{ 'btn-primary': form.tools.includes(item.name) }" @click="toggle(form.tools,item.name)"><Wrench :size="13" />{{ item.name }}</button></div></div>
      <div class="field full"><label>Skills</label><div><button v-for="item in skills" :key="item.id" class="btn btn-sm" style="margin:3px" :class="{ 'btn-primary': form.skills.includes(item.id) }" @click="toggle(form.skills,item.id)">{{ item.name }}</button></div></div>
      <div class="field full"><label>知识库</label><div><button v-for="item in bases" :key="item.id" class="btn btn-sm" style="margin:3px" :class="{ 'btn-primary': form.knowledge_bases.includes(item.id) }" @click="toggle(form.knowledge_bases,item.id)">{{ item.name }}</button></div></div>
      <div class="field full"><button class="btn btn-primary" @click="saveAgent"><Save :size="15" />{{ editingAgentId ? '保存修改' : '保存 Agent' }}</button></div>
    </div>
  </section>

  <section v-if="selected" class="card agent-console">
    <div class="card-header">
      <div><h2>{{ selected.name }} · 多轮对话控制台</h2><p class="console-subtitle">刷新后会自动恢复消息、运行状态、网页研究轨迹与交付文档。</p></div>
      <div style="display:flex;align-items:center;gap:10px"><StatusBadge v-if="currentRunStatus!=='idle'" :status="currentRunStatus" /><div class="console-security"><Shield :size="16" />受审批策略保护</div></div>
    </div>
    <div class="agent-chat-layout">
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
          <div v-if="!messages.length" class="chat-empty"><Sparkles :size="25" /><strong>开始与 {{ selected.name }} 对话</strong><span>可以连续追问，Agent 会携带最近的会话上下文。</span></div>
          <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role" @click="showMessageTrace(message)">
            <div class="message-avatar"><UserRound v-if="message.role==='user'" :size="15" /><Bot v-else :size="15" /></div>
            <div><strong>{{ message.role==='user' ? '你' : selected.name }}{{ message.role==='assistant' && message.trace_json ? ' · 点击回放步骤' : '' }}</strong><p>{{ message.content }}</p></div>
          </article>
          <article v-if="chatRunning" class="chat-message assistant"><div class="message-avatar"><Bot :size="15" /></div><div><strong>{{ selected.name }}</strong><p class="typing">正在执行任务并整理回复</p></div></article>
        </div>
        <div class="chat-composer">
          <textarea v-model="chatInput" class="textarea" placeholder="输入消息；Ctrl + Enter 发送" @keydown.ctrl.enter.prevent="sendMessage" />
          <button class="btn btn-primary" :disabled="chatRunning || !chatInput.trim()" @click="sendMessage"><Send :size="15" />{{ chatRunning ? '执行中' : '发送' }}</button>
        </div>
      </div>

      <aside class="execution-panel">
        <div class="execution-title"><span><Activity :size="15" />运行与交付</span><span class="live-dot" :class="{ running: chatRunning }">{{ chatRunning ? 'LIVE' : 'TRACE' }}</span></div>
        <div class="execution-tabs">
          <button :class="{active:panelTab==='steps'}" @click="panelTab='steps'"><Activity :size="12" />步骤</button>
          <button :class="{active:panelTab==='web'}" @click="panelTab='web'"><Globe2 :size="12" />网页 {{ webEvents.length }}</button>
          <button :class="{active:panelTab==='docs'}" @click="panelTab='docs'"><FileText :size="12" />文档 {{ artifacts.length }}</button>
        </div>
        <div v-if="panelTab==='steps'" class="step-list">
          <div v-for="(step,index) in steps" :key="index" class="step-item" :class="{ error: step.type==='error' }">
            <span class="step-index">{{ index + 1 }}</span><div><strong>{{ stepTitle(step) }}</strong><p>{{ stepMeta(step) }}</p></div>
          </div>
          <div v-if="!steps.length" class="empty compact">发送消息后，这里会实时显示上下文、模型、工具、Agent 联动和审批事件。</div>
        </div>
        <div v-else-if="panelTab==='web'" class="research-list">
          <section v-if="selectedWebSource" class="web-preview">
            <header><strong>{{ selectedWebSource.title || '来源网页' }}</strong><button @click="selectedWebSource=null"><X :size="13" /></button></header>
            <div class="preview-actions">
              <a :href="selectedWebSource.url" target="_blank" rel="noreferrer"><ExternalLink :size="11" />浏览器打开原文</a>
              <a v-if="selectedWebSource.scholar_url" :href="selectedWebSource.scholar_url" target="_blank" rel="noreferrer"><ExternalLink :size="11" />Google Scholar</a>
              <button class="confirm" @click="reviewSource(selectedWebSource,'confirmed')"><Check :size="11" />确认采用</button>
              <button class="exclude" @click="reviewSource(selectedWebSource,'excluded')"><X :size="11" />排除</button>
            </div>
            <div v-if="selectedWebSource.credibility" class="credibility-line"><strong>来源可信度 {{ selectedWebSource.credibility.level }} · {{ selectedWebSource.credibility.score }}/100</strong><span>{{ selectedWebSource.credibility.reasons?.join('；') }}</span><small>{{ selectedWebSource.credibility.note }}</small></div>
            <iframe :src="selectedWebSource.url" :title="selectedWebSource.title" sandbox="allow-scripts allow-same-origin allow-popups" />
            <p v-if="selectedWebSource.content_excerpt" class="preview-fallback">已抓取正文：{{ selectedWebSource.content_excerpt }}</p>
          </section>
          <article v-for="(event,index) in webEvents" :key="index" class="research-card">
            <div><Globe2 :size="13" /><strong>{{ stepTitle(event) }}</strong></div>
            <button v-if="event.url" class="source-link" @click="selectedWebSource=event"><ExternalLink :size="11" />{{ event.title || event.url }}</button>
            <a v-if="event.search_url" :href="event.search_url" target="_blank" rel="noreferrer"><ExternalLink :size="11" />{{ event.search_label || (event.mode === 'academic' ? 'Google Scholar 学术检索' : '普通网页检索') }}</a>
            <div v-if="event.credibility" class="credibility-badge" :class="`level-${event.credibility.level}`">可信度 {{ event.credibility.level }} {{ event.credibility.score }}/100</div>
            <div v-if="event.url" class="source-review-actions"><button @click="reviewSource(event,'confirmed')"><Check :size="10" />确认</button><button @click="reviewSource(event,'excluded')"><X :size="10" />排除</button><span v-if="sourceDecision[event.url]">{{ sourceDecision[event.url]==='confirmed' ? '已采用' : '已排除' }}</span></div>
            <p v-if="event.content_excerpt">{{ event.content_excerpt }}</p>
            <template v-if="event.results"><div v-for="item in event.results.slice(0,8)" :key="item.url" class="candidate-source"><button class="source-link" @click="selectedWebSource=item"><ExternalLink :size="11" />{{ item.title }}</button><span v-if="item.credibility">可信度 {{ item.credibility.level }} {{ item.credibility.score }}/100</span><a v-if="item.scholar_url" :href="item.scholar_url" target="_blank" rel="noreferrer">Google Scholar</a><button @click="reviewSource(item,'confirmed')"><Check :size="10" />确认</button></div></template>
          </article>
          <div v-if="!webEvents.length" class="empty compact">研究类任务开始后，这里会显示检索词、候选网址、抓取状态和网页正文摘要。</div>
        </div>
        <div v-else class="artifact-list">
          <article v-for="artifact in artifacts" :key="artifact.id" class="artifact-card">
            <div><FileText :size="14" /><strong>{{ artifact.title }}</strong></div>
            <small>{{ artifact.relative_path }}</small>
            <DocumentClassroom :artifact="artifact" :agent-name="selected.name" :conversation-id="activeConversation?.id || ''" @ask="askTeacher" />
          </article>
          <div v-if="!artifacts.length" class="empty compact">研究任务完成后，Markdown 成果会保存在工作区并在这里显示。</div>
        </div>
        <div class="trace-note">仅展示操作轨迹与依据，不展示模型内部隐式推理。</div>
      </aside>
    </div>
  </section>
</template>
