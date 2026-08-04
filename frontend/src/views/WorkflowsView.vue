<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Activity, Bot, Box, Braces, ChevronDown, ChevronRight, CircleStop, Code2, Database, Download,
  Check, FileText, GitBranch, GitMerge,
  Globe2, GripVertical, Library, Maximize2, Minimize2, Minus, MousePointer2,
  Pause, RotateCw, Send, Sparkles, Square,
  PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
  Play, Plus, Save, Search, Settings2, ShieldCheck, Trash2, Workflow, X, ZoomIn,
  CircleAlert, CircleHelp, Languages, ListChecks,
} from 'lucide-vue-next'
import FloatingPanel from '../components/FloatingPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import ResearchBrowserCenter from '../components/ResearchBrowserCenter.vue'
import RichAgentMessage from '../components/RichAgentMessage.vue'
import StatusBadge from '../components/StatusBadge.vue'
import WorkflowExpertWindow from '../components/WorkflowExpertWindow.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

type CanvasNode = Entity & { position: { x: number; y: number } }
type CanvasEdge = {
  source: string
  target: string
  source_slot?: string
  target_slot?: string
}
type WorkflowVariable = {
  name: string
  type: string
  default: any
  description: string
  required: boolean
}
type NodeRunStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'skipped'
type NodeRunState = {
  status: NodeRunStatus
  stage: string
  progress: number
  iteration: number
  eventCount: number
  durationMs?: number
  startedAt?: number
  agentRunId?: string
  detail?: string
  outputPreview?: string
  error?: string
}
type RunTimelineItem = {
  id: number
  nodeId: string
  nodeLabel: string
  title: string
  detail: string
  eventType: string
  tone: 'info' | 'success' | 'warning' | 'error'
  elapsed: number
}
type ResearchVisit = {
  id: string
  url: string
  title: string
  provider: string
  status: string
  nodeId: string
  nodeLabel: string
  verificationId?: string
  doi?: string
  publishedYear?: number
}
type ClarificationOption = { value: string; label: string; description?: string }
type ClarificationQuestion = {
  id: string
  label: string
  question: string
  type: 'single_choice' | 'number' | 'text'
  required: boolean
  default?: string | number
  options?: ClarificationOption[]
  placeholder?: string
  min?: number
  max?: number
  suffix?: string
}
type ClarificationResult = {
  required: boolean
  confirmed?: boolean
  task_type: string
  task_type_label: string
  summary: string
  questions: ClarificationQuestion[]
  original_task: string
  resolved_task: string
}

const store = useAppStore()
const workflows = ref<Entity[]>([]), agents = ref<Entity[]>([]), modelEndpoints = ref<Entity[]>([]), knowledgeBases = ref<Entity[]>([]), tools = ref<Entity[]>([]), runs = ref<Entity[]>([]), approvalPolicies = ref<Entity[]>([])
const currentWorkflow = ref<Entity | null>(null), nodes = ref<CanvasNode[]>([]), edges = ref<CanvasEdge[]>([])
const selectedNodeId = ref(''), selectedEdgeIndex = ref<number | null>(null), connectingFrom = ref(''), connectingSlot = ref('output'), pointer = reactive({ x: 0, y: 0 })
const studio = ref<HTMLElement | null>(null), canvas = ref<HTMLElement | null>(null), search = ref(''), task = ref(''), output = ref('')
const workflowForm = reactive({ name: '', description: '' })
const variables = ref<WorkflowVariable[]>([])
const execution = reactive({ loop_enabled: false, loop_count: 1, artifact_enabled: true, stop_condition: '', intent_validation: true })
const nodeWidth = 168, nodeHeight = 82
const baseCanvasWidth = 1600, baseCanvasHeight = 900, canvasGutter = 900
const zoom = ref(1), workflowRunning = ref(false), workflowRunStatus = ref('idle')
const currentRunId = ref(''), runPaused = ref(false), runGuidance = ref(''), runArtifacts = ref<Entity[]>([])
const exportingDocumentId = ref('')
const pendingApprovals = ref<Entity[]>([]), decidingApprovalId = ref('')
const runSecurityProfile = ref('default'), runPermissionMode = ref('inherit'), runApprovalPolicyId = ref('')
const nodeRunStates = ref<Record<string, NodeRunState>>({})
const runTimeline = ref<RunTimelineItem[]>([])
const runPanelTab = ref<'timeline' | 'web' | 'result'>('timeline')
const activeRunNodeId = ref(''), runStartedAt = ref(0), runElapsedSeconds = ref(0)
const lastRunEventId = ref(0)
let timelineSequence = 0
let runPollTimer: number | undefined
let runPollBusy = false
const RUN_STATE_KEY = 'evoagent-workflow-run-state-v2'
const resourceTab = ref<'agents' | 'knowledge' | 'components'>('agents')
const paletteCollapsed = ref(false), inspectorCollapsed = ref(false), runCollapsed = ref(true), fullScreen = ref(false)
const expertOpen = ref(false)
const repairingBindings = ref(false)
const EXPERT_SESSION_MAP_KEY = 'evoagent-workflow-expert-session-map-v1'
const createDraftSessionKey = () => `draft:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`
const expertSessionKey = ref(createDraftSessionKey())
const clarificationOpen = ref(false), clarificationChecking = ref(false), clarificationSubmitting = ref(false)
const clarificationResult = ref<ClarificationResult | null>(null)
const clarificationAnswers = ref<Record<string, any>>({})
const clarificationOriginalTask = ref('')
const researchBrowserOpen = ref(false)
const researchVisits = ref<ResearchVisit[]>([])
const pendingResearchVerifications = computed(() => researchVisits.value.filter(item => item.status === 'verification_required').length)
const movingNodeId = ref('')
const canvasPan = reactive({ active: false, pointerId: -1, startX: 0, startY: 0, scrollLeft: 0, scrollTop: 0 })
const paletteDrag = reactive({ item: null as Entity | null, kind: 'agent' as 'agent' | 'knowledge' | 'component', active: false, startX: 0, startY: 0, x: 0, y: 0 })
const selectedNode = computed(() => nodes.value.find(node => node.id === selectedNodeId.value) || null)
const selectedEdge = computed(() => selectedEdgeIndex.value === null ? null : edges.value[selectedEdgeIndex.value] || null)
const runStats = computed(() => {
  const states = Object.values(nodeRunStates.value)
  return {
    total: nodes.value.length,
    completed: states.filter(item => item.status === 'completed').length,
    failed: states.filter(item => item.status === 'failed').length,
    skipped: states.filter(item => item.status === 'skipped').length,
    running: states.filter(item => item.status === 'running').length,
  }
})
const runProgress = computed(() => {
  if (!nodes.value.length) return 0
  const value = nodes.value.reduce((total, node) => {
    const state = nodeRunStates.value[node.id]
    if (!state) return total
    if (['completed', 'failed', 'skipped'].includes(state.status)) return total + 1
    if (state.status === 'running') return total + state.progress / 100
    return total
  }, 0)
  return Math.min(100, Math.round((value / nodes.value.length) * 100))
})
const clarificationComplete = computed(() => {
  const questions = clarificationResult.value?.questions || []
  return questions.every(question => {
    const value = clarificationAnswers.value[question.id]
    if (question.required && (value === undefined || value === null || String(value).trim() === '')) return false
    if (question.type !== 'number' || value === undefined || value === null || value === '') return true
    const numeric = Number(value)
    return Number.isFinite(numeric)
      && (question.min === undefined || numeric >= question.min)
      && (question.max === undefined || numeric <= question.max)
  })
})
const canvasSize = computed(() => ({
  width: Math.max(
    baseCanvasWidth,
    ...nodes.value.map(node => Math.max(0, Number(node.position?.x) || 0) + nodeWidth + 240),
  ),
  height: Math.max(
    baseCanvasHeight,
    ...nodes.value.map(node => Math.max(0, Number(node.position?.y) || 0) + nodeHeight + 240),
  ),
}))
const visibleAgents = computed(() => agents.value
  .filter(agent => `${agent.name} ${agent.description || ''} ${agent.slug || ''}`.toLowerCase().includes(search.value.toLowerCase()))
  .sort((left, right) => {
    const rank: Record<string, number> = { active: 0, candidate: 1, archived: 2, rejected: 3 }
    return (rank[left.status] ?? 4) - (rank[right.status] ?? 4) || left.name.localeCompare(right.name, 'zh-CN')
  }))
const visibleKnowledgeBases = computed(() => knowledgeBases.value.filter(base =>
  `${base.name} ${base.discipline || ''} ${base.description || ''}`.toLowerCase().includes(search.value.toLowerCase())))
const zoomLabel = computed(() => `${Math.round(zoom.value * 100)}%`)
const selectedRunSecurityProfile = computed(() => runSecurityProfiles.find(item => item.value === runSecurityProfile.value))
const runApprovalSummary = computed(() => {
  if (runPermissionMode.value === 'ask') return '中高风险操作将在运行详情中等待确认'
  if (runPermissionMode.value === 'auto') return '无需人工审批，允许的操作自动执行'
  if (runPermissionMode.value === 'deny') return '禁止中高风险写入和命令操作'
  return '继承 Agent 审批策略与安全治理规则'
})
const validationMessage = computed(() => validateWorkflow())
const componentNodes = [
  { id: 'condition', name: '条件分支', description: 'IF / ELSE 双出口路由', icon: GitBranch },
  { id: 'variable', name: '变量赋值', description: '设置、追加或递增变量', icon: Braces },
  { id: 'template', name: '模板转换', description: '组合变量与上游结果', icon: Code2 },
  { id: 'function', name: '安全函数', description: '拼接、拆分、JSON、去重等', icon: Box },
  { id: 'merge', name: '变量聚合', description: '合并并行分支结果', icon: GitMerge },
  { id: 'tool', name: '工具调用', description: '执行已注册 Tool / MCP 能力', icon: Settings2 },
  { id: 'artifact', name: '产出文档', description: '生成并持久化交付文档', icon: FileText },
] as Entity[]
const agentToolPolicies = [
  { value: 'auto', label: '按节点职责自动', description: '规划、检索、撰写和评审自动使用不同预算，推荐' },
  { value: 'planning', label: '规划 / 提纲', description: '不开放主动工具，直接使用已附加上下文，一次生成' },
  { value: 'research', label: '资料检索', description: '只执行一次系统联网检索和一次综合，不重复审校' },
  { value: 'writing', label: '长文撰写', description: '不主动检索，专注使用上游证据完成长文' },
  { value: 'review', label: '审核 / 修订', description: '不主动检索，专注核验和修订上游成果' },
  { value: 'balanced', label: '均衡工具', description: '最多 3 轮、6 次工具请求，重复结果自动复用' },
  { value: 'full', label: '完整能力', description: '继承 Agent 全部工具，仅用于确需操作环境的节点' },
]
const agentRagModes = [
  { value: 'auto', label: '按节点职责自动', description: '规划可使用 Agent 知识库；检索、撰写和评审复用工作流上游证据' },
  { value: 'agent', label: '使用 Agent RAG', description: '读取该 Agent 绑定的知识库；工作流内默认不额外调用模型改写查询' },
  { value: 'off', label: '仅使用上游证据', description: '不重复检索 Agent 知识库，适合已有知识库节点或检索节点的链路' },
]
const runSecurityProfiles = [
  { value: 'default', label: '继承安全治理', description: '使用安全设置中的目录范围与默认规则' },
  { value: 'read_only', label: '只读模式', description: '禁止写文件和执行命令' },
  { value: 'workspace', label: '仅当前工作区', description: 'Agent 只能访问 EvoAgent 当前工作区' },
  { value: 'custom', label: '仅指定项目路径', description: 'Agent 只能访问安全设置中配置的项目目录' },
  { value: 'unrestricted', label: '完全访问本地', description: '允许访问本机全部路径，请谨慎选择' },
]
function onlineEndpointForAgent(agent: Entity) {
  return modelEndpoints.value.find(endpoint =>
    endpoint.id === agent.model_endpoint_id
    && endpoint.enabled
    && (endpoint.modality || 'chat') === 'chat')
}
function executableAgent(agent: Entity) {
  return ['active', 'candidate'].includes(agent.status) && Boolean(onlineEndpointForAgent(agent))
}
function nodeAgentBindingIssue(node: CanvasNode) {
  if (node.type !== 'agent') return ''
  const agent = agents.value.find(item => item.id === node.config.agent_id)
  if (!agent) return '绑定的 Agent 不存在，可能来自旧草案或已被删除'
  if (!['active', 'candidate'].includes(agent.status)) return `绑定的“${agent.name}”当前为${statusLabel(agent.status)}状态`
  if (!onlineEndpointForAgent(agent)) return `绑定的“${agent.name}”没有启用的在线对话模型接口`
  return ''
}
const invalidAgentNodes = computed(() => nodes.value.filter(node => Boolean(nodeAgentBindingIssue(node))))
const statusLabel = (status: string) => ({ active: '启用', candidate: '候选', archived: '归档', rejected: '拒绝' } as Record<string, string>)[status] || status
const toolPolicyDescription = (value: string) => agentToolPolicies.find(item => item.value === (value || 'auto'))?.description || agentToolPolicies[0].description
const ragModeDescription = (value: string) => agentRagModes.find(item => item.value === (value || 'auto'))?.description || agentRagModes[0].description
const runStatusLabel = (status: NodeRunStatus) => ({
  idle: '未运行', queued: '等待', running: '执行中', completed: '完成', failed: '失败', skipped: '跳过',
} as Record<NodeRunStatus, string>)[status]

function nodeRuntime(nodeId: string): NodeRunState {
  return nodeRunStates.value[nodeId] || {
    status: 'idle',
    stage: '尚未运行',
    progress: 0,
    iteration: 0,
    eventCount: 0,
  }
}

function updateNodeRuntime(nodeId: string, patch: Partial<NodeRunState>) {
  nodeRunStates.value = {
    ...nodeRunStates.value,
    [nodeId]: { ...nodeRuntime(nodeId), ...patch },
  }
}

function resetNodeRuntime() {
  nodeRunStates.value = Object.fromEntries(nodes.value.map(node => [
    node.id,
    {
      status: 'queued',
      stage: '等待上游节点',
      progress: 0,
      iteration: 1,
      eventCount: 0,
    } satisfies NodeRunState,
  ]))
}

function storedRunStates(): Record<string, Entity> {
  try { return JSON.parse(window.localStorage.getItem(RUN_STATE_KEY) || '{}') }
  catch { return {} }
}

function persistRunState() {
  const workflowId = currentWorkflow.value?.id
  if (!workflowId || !currentRunId.value) return
  const states = storedRunStates()
  const previousEventId = Number(states[workflowId]?.lastRunEventId || 0)
  if (previousEventId > lastRunEventId.value) return
  states[workflowId] = {
    workflowId,
    currentRunId: currentRunId.value,
    workflowRunStatus: workflowRunStatus.value,
    workflowRunning: workflowRunning.value,
    runPaused: runPaused.value,
    activeRunNodeId: activeRunNodeId.value,
    runStartedAt: runStartedAt.value,
    runElapsedSeconds: runElapsedSeconds.value,
    lastRunEventId: lastRunEventId.value,
    task: task.value,
    output: output.value,
    runPanelTab: runPanelTab.value,
    researchVisits: researchVisits.value,
    nodeRunStates: nodeRunStates.value,
    runTimeline: runTimeline.value,
    runSecurityProfile: runSecurityProfile.value,
    runPermissionMode: runPermissionMode.value,
    runApprovalPolicyId: runApprovalPolicyId.value,
    updatedAt: Date.now(),
  }
  window.localStorage.setItem(RUN_STATE_KEY, JSON.stringify(states))
}

function applyStoredRunState(state: Entity) {
  currentRunId.value = state.currentRunId || ''
  workflowRunStatus.value = state.workflowRunStatus || 'idle'
  workflowRunning.value = Boolean(state.workflowRunning)
  runPaused.value = Boolean(state.runPaused)
  activeRunNodeId.value = state.activeRunNodeId || ''
  runStartedAt.value = Number(state.runStartedAt || 0)
  runElapsedSeconds.value = Number(state.runElapsedSeconds || 0)
  lastRunEventId.value = Number(state.lastRunEventId || 0)
  task.value = state.task || task.value
  output.value = state.output || ''
  runPanelTab.value = ['timeline', 'web', 'result'].includes(state.runPanelTab) ? state.runPanelTab : 'timeline'
  researchVisits.value = state.researchVisits || []
  nodeRunStates.value = state.nodeRunStates || {}
  runTimeline.value = state.runTimeline || []
  timelineSequence = Math.max(0, ...runTimeline.value.map(item => Number(item.id) || 0))
  runSecurityProfile.value = state.runSecurityProfile || 'default'
  runPermissionMode.value = state.runPermissionMode || 'inherit'
  runApprovalPolicyId.value = state.runApprovalPolicyId || ''
}

function parseJson(value: unknown, fallback: any) {
  try { return typeof value === 'string' ? JSON.parse(value) : (value ?? fallback) }
  catch { return fallback }
}

function workflowOutputMarkdown(value: unknown): string {
  let current = value
  for (let attempt = 0; attempt < 3 && typeof current === 'string'; attempt += 1) {
    const source = current.trim()
    if (!source || !['{', '[', '"'].includes(source[0])) return current
    try { current = JSON.parse(source) }
    catch { return source }
  }
  if (typeof current === 'string') return current
  if (current && typeof current === 'object' && !Array.isArray(current)) {
    const record = current as Record<string, unknown>
    for (const key of ['result', 'output', 'content', 'answer', 'markdown', 'text', 'document']) {
      const candidate = record[key]
      if (typeof candidate === 'string' && candidate.trim()) return workflowOutputMarkdown(candidate)
    }
    const sections = Object.entries(record)
      .filter(([, item]) => typeof item === 'string' && item.trim())
      .map(([key, item]) => `## ${key}\n\n${item}`)
    if (sections.length) return sections.join('\n\n')
  }
  return `\`\`\`json\n${JSON.stringify(current, null, 2)}\n\`\`\``
}

function documentFilename(value: string) {
  const stem = String(value || '工作流成果').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-').trim()
  return `${stem || '工作流成果'}.docx`
}

function saveDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function downloadWorkflowWord() {
  if (!currentRunId.value || exportingDocumentId.value) return
  exportingDocumentId.value = 'run'
  try {
    const blob = await api.blob(`/workflow-runs/${currentRunId.value}/export/docx`, {})
    saveDownload(blob, documentFilename(`${currentWorkflow.value?.name || '工作流'}-最终成果`))
    store.notify('Word 文档已生成并开始下载')
  } catch (error: any) {
    store.notify(error.message || 'Word 文档生成失败', 'error')
  } finally {
    exportingDocumentId.value = ''
  }
}

async function downloadArtifactWord(artifact: Entity) {
  if (!artifact?.id || exportingDocumentId.value) return
  exportingDocumentId.value = artifact.id
  try {
    const blob = await api.blob(`/workflow-artifacts/${artifact.id}/export/docx`, {})
    saveDownload(blob, documentFilename(artifact.title))
    store.notify('Word 文档已生成并开始下载')
  } catch (error: any) {
    store.notify(error.message || 'Word 文档生成失败', 'error')
  } finally {
    exportingDocumentId.value = ''
  }
}

function artifactReady(artifact: Entity) {
  const metadata = parseJson(artifact?.metadata_json, {})
  return workflowRunStatus.value === 'completed' && metadata.delivery_status !== 'needs_revision'
}

function previewArtifact(artifact: Entity) {
  output.value = String(artifact.content || '')
  runPanelTab.value = 'result'
}

function elapsedSeconds() {
  if (!runStartedAt.value) return 0
  runElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - runStartedAt.value) / 1000))
  return runElapsedSeconds.value
}

function appendRunTimeline(
  nodeId: string,
  title: string,
  detail = '',
  tone: RunTimelineItem['tone'] = 'info',
  eventType = '',
) {
  const nodeLabel = nodes.value.find(node => node.id === nodeId)?.label || (nodeId ? nodeId : '工作流')
  runTimeline.value = [
    ...runTimeline.value,
    {
      id: ++timelineSequence,
      nodeId,
      nodeLabel,
      title,
      detail: String(detail || '').slice(0, 900),
      tone,
      eventType,
      elapsed: elapsedSeconds(),
    },
  ].slice(-160)
}

function researchVisitId(url: string) {
  let hash = 2166136261
  for (let index = 0; index < url.length; index += 1) {
    hash ^= url.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `site-${(hash >>> 0).toString(36)}`
}

function upsertResearchVisit(
  value: Entity,
  nodeId: string,
  status: string,
  overrides: Entity = {},
) {
  const url = String(value.url || value.verification_url || value.provider_url || value.search_url || '')
  if (!url.startsWith('http://') && !url.startsWith('https://')) return
  const id = researchVisitId(url)
  const existing = researchVisits.value.find(item => item.id === id)
  const next: ResearchVisit = {
    id,
    url,
    title: String(value.title || value.query || value.search_label || existing?.title || url),
    provider: String(value.source || value.provider || value.search_label || existing?.provider || '网络来源'),
    status,
    nodeId,
    nodeLabel: nodes.value.find(node => node.id === nodeId)?.label || existing?.nodeLabel || nodeId || '工作流',
    verificationId: String(value.verification_id || existing?.verificationId || '') || undefined,
    doi: String(value.doi || existing?.doi || '') || undefined,
    publishedYear: Number(value.published_year || existing?.publishedYear || 0) || undefined,
    ...overrides,
  }
  if (existing) Object.assign(existing, next)
  else researchVisits.value = [...researchVisits.value, next].slice(-160)
}

function recordResearchEvent(event: Entity, nodeId: string) {
  const type = String(event.type || '')
  if (type === 'web_search_started') {
    const targets = event.provider_urls || [{ ...event, url: event.search_url }]
    for (const target of targets) {
      upsertResearchVisit(
        { ...event, ...target, title: `${target.provider || event.search_label}：${target.query || event.query}` },
        nodeId,
        'searching',
      )
    }
  } else if (type === 'web_search_results' || type === 'research_sources_selected') {
    if (type === 'web_search_results') {
      const providerQueries = Object.values(event.provider_queries || {}).map(String)
      for (const visit of researchVisits.value) {
        if (
          visit.status === 'searching'
          && [String(event.query || ''), ...providerQueries].some(query => query && visit.title.endsWith(query))
        ) visit.status = 'searched'
      }
    }
    for (const result of event.results || []) upsertResearchVisit(result, nodeId, 'discovered')
  } else if (type === 'web_fetch_started') {
    upsertResearchVisit(event, nodeId, 'visiting')
  } else if (type === 'web_page_fetched') {
    upsertResearchVisit(event, nodeId, String(event.status || 'fetched'))
  } else if (type === 'web_search_provider_error') {
    upsertResearchVisit(
      { ...event, url: event.verification_url || event.provider_url || event.search_url },
      nodeId,
      event.verification_required ? 'verification_required' : 'failed',
    )
  } else if (type === 'human_verification_required') {
    upsertResearchVisit(event, nodeId, 'verification_required')
    researchBrowserOpen.value = true
    runPanelTab.value = 'web'
    runCollapsed.value = false
  } else if (type === 'human_verification_completed') {
    const visit = researchVisits.value.find(item => item.verificationId === event.verification_id)
    if (visit) visit.status = 'verified'
  } else if (['human_verification_skipped', 'human_verification_timed_out'].includes(type)) {
    const visit = researchVisits.value.find(item => item.verificationId === event.verification_id)
    if (visit) visit.status = 'skipped'
  } else if (type === 'human_verification_retry_failed') {
    const visit = researchVisits.value.find(item => item.verificationId === event.verification_id)
    if (visit) visit.status = 'failed'
  }
}

function markResearchVerification(payload: { verificationId: string; approved: boolean }) {
  const visit = researchVisits.value.find(item => item.verificationId === payload.verificationId)
  if (visit) visit.status = payload.approved ? 'verified' : 'skipped'
  persistRunState()
}

function agentEventInfo(event: Entity) {
  const type = String(event.type || 'agent_progress')
  const rows: Record<string, { title: string; stage: string; progress: number }> = {
    run_started: { title: 'Agent 运行实例已启动', stage: '初始化 Agent', progress: 4 },
    intent_detected: { title: '已识别任务意图', stage: '意图识别', progress: 10 },
    context_ready: { title: '上下文与能力准备完成', stage: '组装上下文', progress: 18 },
    node_context_prepared: { title: '节点输入已按预算组装', stage: '组装上下文', progress: 16 },
    tool_policy_applied: { title: '节点工具策略已生效', stage: '能力与预算', progress: 20 },
    rag_query_condensed: { title: '已生成独立检索问题', stage: 'RAG · 问题改写', progress: 22 },
    rag_scope_resolved: { title: '已确定知识库检索范围', stage: 'RAG · 检索范围', progress: 25 },
    rag_query_rewrite_started: { title: '正在扩展检索问题', stage: 'RAG · 查询扩展', progress: 28 },
    rag_query_rewritten: { title: '检索问题扩展完成', stage: 'RAG · 查询扩展', progress: 31 },
    rag_hybrid_retrieval_started: { title: '正在执行向量与全文混合召回', stage: 'RAG · 混合召回', progress: 34 },
    rag_hybrid_retrieval_completed: { title: '混合召回完成', stage: 'RAG · 候选片段', progress: 40 },
    rag_fusion_completed: { title: '多路检索结果融合完成', stage: 'RAG · 结果融合', progress: 44 },
    rag_rerank_started: { title: '正在重排候选资料', stage: 'RAG · Rerank', progress: 47 },
    rag_rerank_completed: { title: '候选资料重排完成', stage: 'RAG · Rerank', progress: 51 },
    rag_context_assembled: { title: '引用上下文组装完成', stage: 'RAG · 证据上下文', progress: 55 },
    research_planning: { title: '已制定联网检索计划', stage: '联网检索 · 规划', progress: 22 },
    web_search_started: { title: '正在检索外部资料', stage: '联网检索 · 搜索', progress: 30 },
    web_search_results: { title: '取得一批检索结果', stage: '联网检索 · 筛选', progress: 38 },
    web_search_provider_error: { title: '一个检索源暂时不可用', stage: '联网检索 · 数据源', progress: 38 },
    human_verification_required: { title: '检索站点等待机器人验证', stage: '联网检索 · 人工验证', progress: 39 },
    human_verification_retrying: { title: '已同步验证会话，正在重试', stage: '联网检索 · 重试', progress: 40 },
    human_verification_completed: { title: '人工验证后检索完成', stage: '联网检索 · 已恢复', progress: 42 },
    human_verification_retry_failed: { title: '验证后仍无法访问，已切换备用源', stage: '联网检索 · 备用源', progress: 42 },
    human_verification_skipped: { title: '已跳过人工验证', stage: '联网检索 · 备用源', progress: 42 },
    human_verification_timed_out: { title: '人工验证等待超时', stage: '联网检索 · 备用源', progress: 42 },
    research_sources_selected: { title: '已选定可信资料来源', stage: '联网检索 · 来源选择', progress: 45 },
    research_target_shortfall: { title: '来源未达偏好目标，继续生成', stage: '联网检索 · 目标提示', progress: 48 },
    research_requirements_unmet: { title: '未取得真实来源，已停止生成', stage: '联网检索 · 前置校验', progress: 48 },
    web_fetch_started: { title: '正在读取来源正文', stage: '联网检索 · 正文抓取', progress: 50 },
    web_page_fetched: { title: '已读取一个资料来源', stage: '联网检索 · 正文抓取', progress: 57 },
    research_context_ready: { title: '检索证据已压缩并注入', stage: '联网检索 · 上下文整理', progress: 60 },
    research_synthesis_started: { title: '开始综合研究资料', stage: '模型生成 · 资料综合', progress: 64 },
    model_response: { title: '模型返回阶段性结果', stage: '模型生成', progress: 74 },
    tool_result: { title: '工具调用返回结果', stage: '工具执行', progress: 62 },
    tool_result_reused: { title: '已复用相同工具结果', stage: '工具去重', progress: 64 },
    tool_budget_updated: { title: '工具预算已更新', stage: '能力与预算', progress: 68 },
    tool_context_compacted: { title: '工具上下文已自动压缩', stage: '上下文控制', progress: 70 },
    tool_iteration_limit_reached: { title: '资料调用已完成，正在整理答案', stage: '模型生成 · 收敛', progress: 76 },
    tool_iteration_recovered: { title: '已基于工具结果生成最终答案', stage: '模型生成 · 收敛', progress: 79 },
    approval_required: { title: '工具操作等待审批', stage: '等待安全审批', progress: 58 },
    approval_resolved: { title: '工具审批已经处理', stage: '工具执行', progress: 62 },
    generation_verification_started: { title: '开始核验回答质量', stage: '生成校验', progress: 80 },
    generation_repair_started: { title: '正在修复回答质量问题', stage: '生成修复', progress: 84 },
    generation_repaired: { title: '回答修复完成', stage: '生成修复', progress: 88 },
    generation_verified: { title: '回答质量核验完成', stage: '生成校验', progress: 90 },
    quality_review_started: { title: '开始第二轮质量审校', stage: '质量审校', progress: 91 },
    quality_review_skipped: { title: '质量审校已跳过', stage: '质量审校', progress: 92 },
    image_generation_started: { title: '开始生成配图', stage: '图片生成', progress: 92 },
    image_generated: { title: '配图生成完成', stage: '图片生成', progress: 96 },
    image_generation_failed: { title: '配图生成失败，正文继续交付', stage: '图片生成', progress: 96 },
    artifact_created: { title: 'Agent 产出文档已写入数据库', stage: '成果入库', progress: 97 },
    run_completed: { title: 'Agent 执行完成', stage: 'Agent 完成', progress: 100 },
    error: { title: 'Agent 执行发生错误', stage: 'Agent 失败', progress: 100 },
  }
  const info = rows[type] || {
    title: type.replaceAll('_', ' '),
    stage: 'Agent 内部处理',
    progress: 60,
  }
  let detail = String(event.message || event.error || '')
  if (type === 'intent_detected') detail = `类型：${event.category || '通用任务'}${event.needs_clarification ? ' · 需要补充信息' : ''}`
  else if (type === 'node_context_prepared') detail = `${event.tool_policy || 'auto'} 策略 · RAG ${event.rag_mode || 'auto'} · 上下文 ${event.context_chars || 0}/${event.context_char_limit || 0} 字${event.removed_chars ? ` · 压缩 ${event.removed_chars} 字` : ''}`
  else if (type === 'context_ready') detail = `知识片段 ${event.rag?.citations || 0} 条 · 历史消息 ${event.history_messages || 0} 条`
  else if (type === 'tool_policy_applied') detail = `${event.preset || 'balanced'} · 最多 ${event.max_iterations || 1} 轮 / ${event.max_calls || 0} 次 · 可用工具 ${event.available_tools?.length || 0} 个`
  else if (type === 'rag_query_rewritten') detail = `生成 ${event.query_count || event.queries?.length || 0} 个检索问题`
  else if (type === 'rag_hybrid_retrieval_completed') detail = `向量候选 ${event.dense_candidates || 0} · 全文候选 ${event.lexical_candidates || 0}`
  else if (type === 'rag_rerank_completed') detail = `重排 ${event.reranked || 0} 条 · 选中 ${event.selected || 0} 条`
  else if (type === 'rag_context_assembled') detail = `上下文 ${event.context_chars || 0} 字 · 引用 ${event.citation_count || 0} 条`
  else if (type === 'research_planning') {
    const domain = event.domain_label || (event.mode === 'academic' ? '学术研究' : '网页研究')
    const sources = Array.isArray(event.preferred_sources)
      ? event.preferred_sources.slice(0, 2).join('、')
      : ''
    detail = `${domain} · ${event.queries?.length || 0} 组检索词${sources ? ` · 优先 ${sources}` : ''}`
  }
  else if (type === 'web_search_started') detail = String(event.query || '')
  else if (type === 'web_search_results') detail = `取得 ${event.count || 0} 条 · 排除 ${event.discarded || 0} 条`
  else if (type === 'web_search_provider_error') detail = `${event.provider || '检索源'} · ${event.error_type || ''} · ${event.error || '暂时不可用'}`
  else if (type === 'human_verification_required') detail = `${event.provider || '检索站点'} · 等待 ${event.wait_seconds || 90} 秒 · 可在“访问网站”中处理`
  else if (type === 'human_verification_completed') detail = `重试取得 ${event.count || 0} 条结果`
  else if (type.startsWith('human_verification_')) detail = String(event.error || event.message || event.provider || '')
  else if (type === 'research_sources_selected') detail = `选定 ${event.count || 0} 个可追溯来源 · 近 3 年 ${event.recent_3_year_count || 0} 篇 · 近 5 年 ${event.recent_5_year_count || 0} 篇${event.research_scope ? ` · ${event.research_scope}` : ''}`
  else if (type === 'research_target_shortfall') detail = `目标约 ${event.target_sources || 0} 条 · 实际 ${event.actual_sources || 0} 条 · 将继续生成并披露数量`
  else if (type === 'research_requirements_unmet') detail = `目标约 ${event.target_sources || 0} 条 · 实际 0 条 · 未调用模型综合`
  else if (type === 'research_context_ready') detail = `${event.sources || 0} 个来源 · ${event.context_chars || 0}/${event.context_char_limit || 0} 字符`
  else if (type === 'web_page_fetched') detail = `${event.title || event.url || ''} · ${event.status || '已读取'}`
  else if (type === 'model_response') detail = `第 ${event.iteration || 1} 次响应 · ${event.stage || 'answer'}${event.tool_calls?.length ? ` · 调用 ${event.tool_calls.join('、')}` : ''}`
  else if (type === 'tool_result') detail = `${event.tool || '工具'} · ${event.status || 'completed'}${event.error ? ` · ${event.error}` : ''}`
  else if (type === 'tool_result_reused') detail = `${event.tool || '工具'} · 相同参数不再重复执行`
  else if (type === 'tool_budget_updated') detail = `已执行 ${event.calls_executed || 0}/${event.max_calls || 0} 次 · 复用 ${event.calls_reused || 0} 次 · 第 ${event.iterations_used || 1}/${event.max_iterations || 1} 轮`
  else if (type === 'tool_context_compacted') detail = `压缩 ${event.removed_chars || 0} 字 · 上限 ${event.context_char_limit || 0} 字`
  else if (type === 'generation_verified') detail = `${event.passed ? '校验通过' : `发现 ${event.issues?.length || 0} 个问题`} · 引用 ${event.citation_count || 0} 条`
  else if (type === 'run_completed') detail = `耗时 ${event.duration_ms || 0} ms · 模型请求 ${event.model_calls || 1} 次 · 工具执行 ${event.tool_calls_executed || 0} 次 · Token ${event.token_usage || 0}`
  return {
    ...info,
    type,
    detail,
    tone: (type === 'error' ? 'error' : type.includes('failed') || type.includes('skipped') || type.includes('verification_required') || type.includes('timed_out') ? 'warning' : type === 'run_completed' || type.includes('verification_completed') ? 'success' : 'info') as RunTimelineItem['tone'],
  }
}

function edgeRuntimeStatus(edge: CanvasEdge) {
  const source = nodeRuntime(edge.source).status
  const target = nodeRuntime(edge.target).status
  if (source === 'failed' || target === 'failed') return 'failed'
  if (target === 'skipped') return 'skipped'
  if (target === 'running' && ['completed', 'running'].includes(source)) return 'active'
  if (source === 'completed' && target === 'completed') return 'completed'
  return 'idle'
}

function handleWorkflowStep(step: Entity) {
  if (step.event_id) lastRunEventId.value = Math.max(lastRunEventId.value, Number(step.event_id))
  elapsedSeconds()
  if (step.type === 'stream_connected') {
    output.value = '实时连接已建立，准备执行节点…'
    appendRunTimeline('', '实时运行连接已建立', '', 'info', step.type)
  } else if (step.type === 'workflow_run_started') {
    currentRunId.value = step.run_id || ''
    output.value = '工作流已启动，正在计算节点执行顺序…'
    appendRunTimeline('', '工作流正式启动', `运行 ID：${step.run_id || '-'}`, 'info', step.type)
  } else if (step.type === 'workflow_iteration_started') {
    if ((step.iteration || 1) > 1) resetNodeRuntime()
    appendRunTimeline('', `开始第 ${step.iteration}/${step.total_iterations} 轮执行`, '', 'info', step.type)
    output.value = `开始第 ${step.iteration}/${step.total_iterations} 轮执行…`
  } else if (step.type === 'workflow_node_started') {
    activeRunNodeId.value = step.node_id
    updateNodeRuntime(step.node_id, {
      status: 'running',
      stage: step.node_type === 'agent' ? '启动 Agent' : '执行节点',
      progress: 2,
      iteration: step.iteration || 1,
      startedAt: Date.now(),
      detail: '',
      error: '',
    })
    appendRunTimeline(step.node_id, '节点开始执行', `类型：${step.node_type || 'node'}`, 'info', step.type)
    output.value = `第 ${step.iteration || 1} 轮 · 正在执行：${step.label || step.node_id}…`
  } else if (step.type === 'workflow_agent_event') {
    const event = step.agent_event || {}
    recordResearchEvent(event, step.node_id || '')
    const info = agentEventInfo(event)
    const previous = nodeRuntime(step.node_id)
    updateNodeRuntime(step.node_id, {
      status: info.type === 'error' ? 'failed' : 'running',
      stage: info.stage,
      progress: Math.max(previous.progress, info.progress),
      eventCount: previous.eventCount + 1,
      agentRunId: event.run_id || previous.agentRunId,
      detail: info.detail,
      error: info.type === 'error' ? info.detail : previous.error,
    })
    appendRunTimeline(step.node_id, info.title, info.detail, info.tone, info.type)
    output.value = `${step.label || step.node_id} · ${info.title}${info.detail ? `：${info.detail}` : ''}`
    if (info.type === 'approval_required') {
      runCollapsed.value = false
      runPanelTab.value = 'timeline'
      void loadRunApprovals()
    } else if (info.type === 'approval_resolved') {
      void loadRunApprovals()
    }
  } else if (step.type === 'workflow_node_completed') {
    const preview = String(step.result?.output_preview || '')
    updateNodeRuntime(step.node_id, {
      status: 'completed',
      stage: '执行完成',
      progress: 100,
      durationMs: step.duration_ms || 0,
      outputPreview: preview,
      detail: preview ? `已产出 ${preview.length} 字符结果摘要` : '节点执行完成',
      agentRunId: step.result?.run_id || nodeRuntime(step.node_id).agentRunId,
    })
    activeRunNodeId.value = ''
    appendRunTimeline(step.node_id, '节点执行完成', preview.slice(0, 260), 'success', step.type)
    output.value = `已完成：${step.label || step.node_id}（${step.duration_ms || 0} ms）`
  } else if (step.type === 'workflow_node_failed') {
    updateNodeRuntime(step.node_id, {
      status: 'failed',
      stage: '执行失败',
      progress: 100,
      durationMs: step.duration_ms || 0,
      error: step.error || '节点执行失败',
      detail: step.error || '节点执行失败',
    })
    activeRunNodeId.value = step.node_id || ''
    appendRunTimeline(step.node_id, '节点执行失败', step.error || '', 'error', step.type)
    output.value = step.error || `节点 ${step.label || step.node_id} 执行失败`
  } else if (step.type === 'workflow_node_retrying') {
    updateNodeRuntime(step.node_id, {
      status: 'running',
      stage: `自动重试 ${step.attempt}/${step.max_attempts}`,
      detail: step.error || '在线接口暂时不可用',
    })
    appendRunTimeline(step.node_id, `节点准备第 ${step.attempt}/${step.max_attempts} 次尝试`, step.error || '', 'warning', step.type)
    output.value = `${step.label || step.node_id} 暂时失败，${step.delay_ms || 0} ms 后自动重试…`
  } else if (step.type === 'workflow_node_skipped') {
    updateNodeRuntime(step.node_id, {
      status: 'skipped',
      stage: '分支未命中',
      progress: 100,
      detail: step.reason || '上游分支未命中',
    })
    appendRunTimeline(step.node_id, '节点已跳过', step.reason || '', 'warning', step.type)
    output.value = `已跳过：${step.label || step.node_id} · ${step.reason}`
  } else if (step.type === 'workflow_run_paused') {
    runPaused.value = true
    workflowRunStatus.value = 'paused'
    output.value = '工作流已暂停，可发送引导后继续。'
    appendRunTimeline('', '工作流已暂停', '', 'warning', step.type)
  } else if (step.type === 'workflow_run_resumed') {
    runPaused.value = false
    workflowRunStatus.value = 'running'
    output.value = '工作流已恢复执行。'
    appendRunTimeline('', '工作流恢复执行', '', 'info', step.type)
  } else if (step.type === 'workflow_guidance_received') {
    output.value = '引导已接收，将在下一个节点前生效。'
    appendRunTimeline(activeRunNodeId.value, '收到人工引导', step.message || '', 'info', step.type)
  } else if (step.type === 'workflow_guidance_applied') {
    output.value = `已应用 ${step.messages?.length || 1} 条人工引导。`
    appendRunTimeline(activeRunNodeId.value, '人工引导已应用', (step.messages || []).join('；'), 'success', step.type)
  } else if (step.type === 'workflow_artifact_created') {
    output.value = `已生成产出文档：${step.title}`
    appendRunTimeline(activeRunNodeId.value, '产出文档已写入数据库', step.title || '', 'success', step.type)
  } else if (step.type === 'workflow_intent_validation_started') {
    output.value = '全部节点已完成，正在校验最终结果是否符合用户原始意图…'
    appendRunTimeline('', '开始最终意图校验', `${step.endpoint || ''} · ${step.model || ''}`, 'info', step.type)
  } else if (step.type === 'workflow_intent_validation_completed') {
    const detail = `意图匹配 ${step.score || 0} 分${step.improved ? ' · 已自动修正最终交付' : ''}${step.issues?.length ? ` · ${step.issues.join('；')}` : ''}`
    output.value = detail
    appendRunTimeline('', step.passed ? '最终结果符合用户意图' : '最终结果已按用户意图修正', detail, step.passed ? 'success' : 'warning', step.type)
  } else if (step.type === 'workflow_intent_validation_failed' || step.type === 'workflow_intent_validation_skipped') {
    const detail = step.error || step.issues?.join('；') || '意图校验未完成'
    output.value = `节点链路已完成，但${detail}`
    appendRunTimeline('', '最终意图校验未完成', detail, 'warning', step.type)
  } else if (step.type === 'workflow_delivery_quality_warning') {
    const detail = step.warnings?.join('；') || '真实来源数量低于优先目标，已按实际结果完成交付'
    output.value = detail
    appendRunTimeline('', '交付完成，来源数量低于优先目标', detail, 'warning', step.type)
  } else if (step.type === 'workflow_run_interrupted') {
    output.value = step.error || '工作流已中断'
    appendRunTimeline('', '工作流已中断', step.error || '', 'warning', step.type)
  } else if (step.type === 'workflow_waiting') {
    output.value = `${activeRunNodeId.value ? `${nodes.value.find(node => node.id === activeRunNodeId.value)?.label || 'Agent'} 仍在执行` : '工作流仍在执行'}，总耗时 ${runElapsedSeconds.value} 秒…`
  } else if (step.type === 'workflow_run_completed') {
    appendRunTimeline('', '工作流执行完成', `总耗时 ${step.duration_ms || 0} ms · ${step.iteration_count || 1} 轮`, 'success', step.type)
  } else if (step.type === 'workflow_run_failed') {
    output.value = step.error || '工作流执行失败'
    appendRunTimeline(activeRunNodeId.value, '工作流执行失败', step.error || '', 'error', step.type)
  }
  persistRunState()
}

function hydrateRunRecord(run: Entity, includeTraceTimeline = false) {
  currentRunId.value = run.id || currentRunId.value
  workflowRunStatus.value = run.status || 'idle'
  workflowRunning.value = ['queued', 'running', 'paused'].includes(run.status)
  runPaused.value = run.status === 'paused'
  const input = parseJson(run.input_json, {})
  if (input.task) task.value = input.task
  const control = parseJson(run.control_json, {})
  runSecurityProfile.value = control.security_profile || runSecurityProfile.value
  runPermissionMode.value = control.permission_mode || runPermissionMode.value
  runApprovalPolicyId.value = control.approval_policy_id || runApprovalPolicyId.value
  if (!runStartedAt.value && run.created_at) runStartedAt.value = new Date(run.created_at).getTime()
  runElapsedSeconds.value = run.duration_ms
    ? Math.max(0, Math.floor(run.duration_ms / 1000))
    : Math.max(0, Math.floor((Date.now() - runStartedAt.value) / 1000))
  if (!Object.keys(nodeRunStates.value).length) resetNodeRuntime()
  const trace = parseJson(run.trace_json, [])
  for (const item of trace) {
    if (!item.node_id || !item.status) continue
    const status = ['completed', 'failed', 'skipped'].includes(item.status) ? item.status : 'running'
    updateNodeRuntime(item.node_id, {
      status,
      stage: status === 'completed' ? '执行完成' : status === 'failed' ? '执行失败' : status === 'skipped' ? '分支未命中' : '执行中',
      progress: ['completed', 'failed', 'skipped'].includes(status) ? 100 : 50,
      iteration: item.iteration || 1,
      durationMs: item.duration_ms || 0,
      outputPreview: item.result?.output_preview || '',
      detail: item.error || item.result?.output_preview || '',
      error: item.error || '',
    })
    if (includeTraceTimeline) {
      appendRunTimeline(
        item.node_id,
        status === 'completed' ? '节点执行完成' : status === 'failed' ? '节点执行失败' : '节点已跳过',
        item.error || item.result?.output_preview || '',
        status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'warning',
        `restored_${status}`,
      )
    }
  }
  activeRunNodeId.value = workflowRunning.value ? (run.current_node_id || activeRunNodeId.value) : ''
  if (activeRunNodeId.value && !['completed', 'failed', 'skipped'].includes(nodeRuntime(activeRunNodeId.value).status)) {
    updateNodeRuntime(activeRunNodeId.value, {
      status: 'running',
      stage: pendingApprovals.value.length ? '等待安全审批' : '后台持续执行',
      progress: Math.max(5, nodeRuntime(activeRunNodeId.value).progress),
    })
  }
  if (!workflowRunning.value) {
    if (run.status === 'completed') {
      output.value = workflowOutputMarkdown(run.output_json || '')
      runPanelTab.value = 'result'
    } else if (run.error) output.value = run.error
  }
  persistRunState()
}

async function loadRunApprovals() {
  if (!currentRunId.value) { pendingApprovals.value = []; return }
  try {
    pendingApprovals.value = await api.get(`/approvals?status=pending&run_id=${encodeURIComponent(currentRunId.value)}`)
    if (pendingApprovals.value.length) {
      runCollapsed.value = false
      if (activeRunNodeId.value) updateNodeRuntime(activeRunNodeId.value, { stage: '等待安全审批' })
    }
  } catch { pendingApprovals.value = [] }
}

async function decideRunApproval(approval: Entity, approved: boolean) {
  if (!approval.id || decidingApprovalId.value) return
  decidingApprovalId.value = approval.id
  try {
    await api.post(`/approvals/${approval.id}/decide`, {
      approved,
      decided_by: 'workflow-user',
    })
    appendRunTimeline(activeRunNodeId.value, approved ? '已批准安全操作' : '已拒绝安全操作', approval.summary || '', approved ? 'success' : 'warning', 'approval_decided')
    store.notify(approved ? '操作已批准，工作流将继续执行' : '操作已拒绝，工作流将按拒绝结果继续')
    await loadRunApprovals()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { decidingApprovalId.value = '' }
}

async function loadBufferedRunEvents() {
  if (!currentRunId.value) return 0
  try {
    const result = await api.get<Entity>(`/workflow-runs/${currentRunId.value}/events?after=${lastRunEventId.value}`)
    const events = result.events || []
    for (const event of events) handleWorkflowStep(event)
    return events.length
  } catch { return 0 }
}

function stopRunPolling() {
  if (runPollTimer !== undefined) window.clearInterval(runPollTimer)
  runPollTimer = undefined
  runPollBusy = false
}

async function pollRestoredRun() {
  if (!currentRunId.value || runPollBusy) return
  runPollBusy = true
  try {
    const [run] = await Promise.all([
      api.get<Entity>(`/workflow-runs/${currentRunId.value}`),
      loadBufferedRunEvents(),
      loadRunApprovals(),
    ])
    hydrateRunRecord(run)
    if (!['queued', 'running', 'paused'].includes(run.status)) {
      workflowRunning.value = false
      runArtifacts.value = await api.get(`/workflow-runs/${currentRunId.value}/artifacts`)
      runs.value = await api.get('/workflow-runs')
      stopRunPolling()
    }
  } catch { /* The next poll retries while the desktop service is available. */ }
  finally { runPollBusy = false }
}

function startRunPolling() {
  stopRunPolling()
  void pollRestoredRun()
  runPollTimer = window.setInterval(() => void pollRestoredRun(), 1200)
}

async function restoreRunForWorkflow(workflowId: string) {
  stopRunPolling()
  const stored = storedRunStates()[workflowId]
  const active = runs.value.find(run => run.workflow_id === workflowId && ['queued', 'running', 'paused'].includes(run.status))
  const storedRun = stored?.currentRunId ? runs.value.find(run => run.id === stored.currentRunId) : null
  const latest = runs.value.find(run => run.workflow_id === workflowId)
  const run = active || storedRun || latest
  if (!run) return
  if (stored && stored.currentRunId === run.id) applyStoredRunState(stored)
  else lastRunEventId.value = 0
  currentRunId.value = run.id
  const eventCount = await loadBufferedRunEvents()
  hydrateRunRecord(run, !eventCount && !runTimeline.value.length)
  await Promise.all([
    loadRunApprovals(),
    api.get<Entity[]>(`/workflow-runs/${run.id}/artifacts`).then(items => { runArtifacts.value = items }).catch(() => {}),
  ])
  if (['queued', 'running', 'paused'].includes(run.status)) {
    runCollapsed.value = false
    startRunPolling()
  }
}

async function selectWorkflow(workflow: Entity) {
  persistRunState()
  stopRunPolling()
  openWorkflow(workflow)
  await restoreRunForWorkflow(workflow.id)
}

function parseDefinition(value: string) {
  try { return JSON.parse(value) } catch { return { nodes: [], edges: [] } }
}

function expertSessionMap(): Record<string, string> {
  try { return JSON.parse(window.localStorage.getItem(EXPERT_SESSION_MAP_KEY) || '{}') }
  catch { return {} }
}

function expertSessionForWorkflow(workflowId: string) {
  return expertSessionMap()[workflowId] || `workflow:${workflowId}`
}

function bindExpertSession(workflowId: string) {
  const sessions = expertSessionMap()
  sessions[workflowId] = expertSessionKey.value
  window.localStorage.setItem(EXPERT_SESSION_MAP_KEY, JSON.stringify(sessions))
}

function normalizeNodes(definition: Entity): CanvasNode[] {
  const raw = definition.nodes || []
  return raw.map((node: Entity, index: number) => {
    const config = { ...(node.config || {}) }
    if (['agent', 'knowledge'].includes(node.type) && config.auto_input === undefined) config.auto_input = true
    if (node.type === 'agent' && config.retry_count === undefined) config.retry_count = 0
    if (node.type === 'agent' && !config.tool_policy) config.tool_policy = 'auto'
    if (node.type === 'agent' && !config.rag_mode) config.rag_mode = 'auto'
    if (node.type === 'function') config.arguments ||= ['', '']
    return {
      ...node,
      config,
      position: {
        x: Math.max(8, Number(node.position?.x) || 50 + index * 215),
        y: Math.max(8, Number(node.position?.y) || (index % 2 ? 245 : 120)),
      },
    }
  })
}

async function load() {
  store.loading(true)
  try {
    [workflows.value, agents.value, modelEndpoints.value, knowledgeBases.value, tools.value, runs.value, approvalPolicies.value] = await Promise.all([
      api.get('/workflows'),
      api.get('/agents'),
      api.get('/model-endpoints'),
      api.get('/knowledge-bases'),
      api.get('/tools'),
      api.get('/workflow-runs'),
      api.get('/approval-policies'),
    ])
    if (currentWorkflow.value) {
      const refreshed = workflows.value.find(item => item.id === currentWorkflow.value?.id)
      if (refreshed) await selectWorkflow(refreshed)
    } else if (workflows.value.length) {
      const activeRun = runs.value.find(run => ['queued', 'running', 'paused'].includes(run.status))
      const initial = workflows.value.find(item => item.id === activeRun?.workflow_id) || workflows.value[0]
      await selectWorkflow(initial)
    }
    else newWorkflow()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

function openWorkflow(workflow: Entity) {
  expertOpen.value = false
  expertSessionKey.value = expertSessionForWorkflow(workflow.id)
  currentWorkflow.value = workflow
  workflowForm.name = workflow.name
  workflowForm.description = workflow.description
  const definition = parseDefinition(workflow.definition_json)
  nodes.value = normalizeNodes(definition)
  edges.value = (definition.edges || []).map((edge: Entity) => ({
    source: edge.source,
    target: edge.target,
    source_slot: edge.source_slot || edge.sourceHandle || 'output',
    target_slot: edge.target_slot || edge.targetHandle || 'input',
  }))
  variables.value = (definition.variables || []).map((item: Entity) => ({
    name: item.name || '',
    type: item.type || 'string',
    default: item.default ?? '',
    description: item.description || '',
    required: Boolean(item.required),
  }))
  Object.assign(execution, {
    loop_enabled: false,
    loop_count: 1,
    artifact_enabled: true,
    stop_condition: '',
    intent_validation: true,
    ...(definition.execution || {}),
  })
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
  task.value = ''
  clarificationOpen.value = false
  clarificationResult.value = null
  clarificationAnswers.value = {}
  clarificationOriginalTask.value = ''
  output.value = ''
  runArtifacts.value = []
  pendingApprovals.value = []
  nodeRunStates.value = {}
  runTimeline.value = []
  researchVisits.value = []
  activeRunNodeId.value = ''
  currentRunId.value = ''
  workflowRunning.value = false
  workflowRunStatus.value = 'idle'
  runPaused.value = false
  lastRunEventId.value = 0
  void nextTick().then(fitCanvas)
}

function newWorkflow() {
  persistRunState()
  stopRunPolling()
  expertOpen.value = false
  expertSessionKey.value = createDraftSessionKey()
  currentWorkflow.value = null
  workflowForm.name = '未命名协作工作流'
  workflowForm.description = ''
  nodes.value = [
    { id: 'input', type: 'input', label: '任务输入', config: {}, position: { x: 50, y: 210 } },
    { id: 'output', type: 'output', label: '结果输出', config: { value: { result: '{{input.task}}' } }, position: { x: 390, y: 210 } },
  ]
  edges.value = []
  variables.value = [
    { name: 'objective', type: 'string', default: '', description: '本次工作流目标', required: false },
  ]
  Object.assign(execution, {
    loop_enabled: false,
    loop_count: 1,
    artifact_enabled: true,
    stop_condition: '',
    intent_validation: true,
  })
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
  task.value = ''
  clarificationOpen.value = false
  clarificationResult.value = null
  clarificationAnswers.value = {}
  clarificationOriginalTask.value = ''
  output.value = ''
  runArtifacts.value = []
  pendingApprovals.value = []
  nodeRunStates.value = {}
  runTimeline.value = []
  researchVisits.value = []
  activeRunNodeId.value = ''
  currentRunId.value = ''
  workflowRunning.value = false
  workflowRunStatus.value = 'idle'
  runPaused.value = false
  lastRunEventId.value = 0
  void nextTick().then(fitCanvas)
}

function addAgentAt(agent: Entity, point: { x: number; y: number }) {
  if (!executableAgent(agent)) {
    store.notify(`“${agent.name}”当前为${statusLabel(agent.status)}版本，不能加入运行工作流`, 'error')
    return
  }
  const id = `agent_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
  nodes.value.push({
    id,
    type: 'agent',
    label: agent.name,
    config: { agent_id: agent.id, input: '{{input.task}}', prompt: '', auto_input: true, retry_count: 0, max_output_tokens: 8192, tool_policy: 'auto', rag_mode: 'auto' },
    position: {
      x: Math.max(8, point.x - nodeWidth / 2),
      y: Math.max(8, point.y - nodeHeight / 2),
    },
  })
  selectedNodeId.value = id
  selectedEdgeIndex.value = null
}

function addKnowledgeAt(base: Entity, point: { x: number; y: number }) {
  const id = `knowledge_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
  nodes.value.push({
    id,
    type: 'knowledge',
    label: base.name,
    config: {
      knowledge_base_id: base.id,
      query: '{{input.task}}',
      top_k: 5,
      auto_input: true,
    },
    position: {
      x: Math.max(8, point.x - nodeWidth / 2),
      y: Math.max(8, point.y - nodeHeight / 2),
    },
  })
  selectedNodeId.value = id
  selectedEdgeIndex.value = null
}

function addComponentAt(component: Entity, point: { x: number; y: number }) {
  const type = String(component.id)
  const id = `${type}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
  const configs: Record<string, Entity> = {
    condition: { left: '{{input.task}}', operator: 'contains', right: '', },
    variable: { assignments: [{ name: 'result', operation: 'set', value: '{{input.task}}' }] },
    template: { template: '# 处理结果\n\n{{input.task}}' },
    function: { function: 'concat', arguments: ['{{input.task}}', ''] },
    merge: { mode: 'text', separator: '\n\n' },
    tool: {
      tool: tools.value[0]?.name || tools.value[0]?.id || '',
      arguments: {},
      permission_mode: 'ask',
      security_profile: 'default',
    },
    artifact: { title: '工作流产出文档', content: '# 工作流产出\n\n{{input.task}}' },
  }
  nodes.value.push({
    id,
    type,
    label: component.name,
    config: configs[type] || {},
    position: {
      x: Math.max(8, point.x - nodeWidth / 2),
      y: Math.max(8, point.y - nodeHeight / 2),
    },
  })
  selectedNodeId.value = id
  selectedEdgeIndex.value = null
}

function addResourceAt(kind: 'agent' | 'knowledge' | 'component', item: Entity, point: { x: number; y: number }) {
  if (kind === 'component') addComponentAt(item, point)
  else
  if (kind === 'knowledge') addKnowledgeAt(item, point)
  else addAgentAt(item, point)
}

function stopPaletteTracking() {
  window.removeEventListener('pointermove', movePaletteDrag)
  window.removeEventListener('pointerup', finishPaletteDrag)
  window.removeEventListener('pointercancel', cancelPaletteDrag)
}

function startPalettePointer(event: PointerEvent, item: Entity, kind: 'agent' | 'knowledge' | 'component') {
  if (event.button !== 0) return
  if (kind === 'agent' && !executableAgent(item)) return
  paletteDrag.item = item
  paletteDrag.kind = kind
  paletteDrag.active = false
  paletteDrag.startX = paletteDrag.x = event.clientX
  paletteDrag.startY = paletteDrag.y = event.clientY
  window.addEventListener('pointermove', movePaletteDrag)
  window.addEventListener('pointerup', finishPaletteDrag)
  window.addEventListener('pointercancel', cancelPaletteDrag)
}

function movePaletteDrag(event: PointerEvent) {
  if (!paletteDrag.item) return
  paletteDrag.x = event.clientX
  paletteDrag.y = event.clientY
  if (Math.hypot(event.clientX - paletteDrag.startX, event.clientY - paletteDrag.startY) > 4) paletteDrag.active = true
}

function finishPaletteDrag(event: PointerEvent) {
  const item = paletteDrag.item
  const kind = paletteDrag.kind
  const rect = canvas.value?.getBoundingClientRect()
  if (item && paletteDrag.active && rect && event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) {
    addResourceAt(kind, item, canvasPoint(event))
    store.notify(`已将“${item.name}”加入画板`)
  }
  cancelPaletteDrag()
}

function cancelPaletteDrag() {
  stopPaletteTracking()
  paletteDrag.item = null
  paletteDrag.active = false
}

function addResourceToCenter(kind: 'agent' | 'knowledge' | 'component', item: Entity) {
  const target = canvas.value
  if (!target) return
  addResourceAt(kind, item, {
    x: (target.scrollLeft + target.clientWidth / 2 - canvasGutter) / zoom.value,
    y: (target.scrollTop + target.clientHeight / 2 - canvasGutter) / zoom.value,
  })
  if (kind !== 'agent' || executableAgent(item)) store.notify(`已将“${item.name}”加入画板`)
}

function canvasPoint(event: { clientX: number; clientY: number }) {
  const rect = canvas.value?.getBoundingClientRect()
  return {
    x: Math.max(10, (event.clientX - (rect?.left || 0) + (canvas.value?.scrollLeft || 0) - canvasGutter) / zoom.value),
    y: Math.max(10, (event.clientY - (rect?.top || 0) + (canvas.value?.scrollTop || 0) - canvasGutter) / zoom.value),
  }
}

function dropAgent(event: DragEvent) {
  const agentId = event.dataTransfer?.getData('application/evoagent-agent')
  const agent = agents.value.find(item => item.id === agentId)
  if (!agent) return
  addAgentAt(agent, canvasPoint(event))
}

function stopCanvasPan() {
  const pointerId = canvasPan.pointerId
  canvasPan.active = false
  canvasPan.pointerId = -1
  if (canvas.value && pointerId >= 0 && canvas.value.hasPointerCapture(pointerId)) {
    canvas.value.releasePointerCapture(pointerId)
  }
  window.removeEventListener('pointermove', moveCanvasPan)
  window.removeEventListener('pointerup', stopCanvasPan)
  window.removeEventListener('pointercancel', stopCanvasPan)
}

function startCanvasPan(event: PointerEvent) {
  if (event.button !== 0 || connectingFrom.value || !canvas.value) return
  const target = event.target as HTMLElement
  if (target.closest('.workflow-node') || target.closest('.workflow-wire')) return
  event.preventDefault()
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
  canvasPan.active = true
  canvasPan.pointerId = event.pointerId
  canvas.value.setPointerCapture(event.pointerId)
  Object.assign(canvasPan, {
    startX: event.clientX,
    startY: event.clientY,
    scrollLeft: canvas.value.scrollLeft,
    scrollTop: canvas.value.scrollTop,
  })
  window.addEventListener('pointermove', moveCanvasPan)
  window.addEventListener('pointerup', stopCanvasPan)
  window.addEventListener('pointercancel', stopCanvasPan)
}

function moveCanvasPan(event: PointerEvent) {
  if (!canvasPan.active || !canvas.value) return
  event.preventDefault()
  canvas.value.scrollLeft = canvasPan.scrollLeft - (event.clientX - canvasPan.startX)
  canvas.value.scrollTop = canvasPan.scrollTop - (event.clientY - canvasPan.startY)
}

function startMove(node: CanvasNode, event: PointerEvent) {
  if ((event.target as HTMLElement).classList.contains('node-port')) return
  selectedNodeId.value = node.id
  selectedEdgeIndex.value = null
  movingNodeId.value = node.id
  const start = { x: event.clientX, y: event.clientY, nodeX: node.position.x, nodeY: node.position.y }
  const move = (next: PointerEvent) => {
    node.position.x = Math.max(8, start.nodeX + (next.clientX - start.x) / zoom.value)
    node.position.y = Math.max(8, start.nodeY + (next.clientY - start.y) / zoom.value)
  }
  const stop = () => {
    node.position.x = Math.max(8, Math.round(node.position.x / 20) * 20)
    node.position.y = Math.max(8, Math.round(node.position.y / 20) * 20)
    movingNodeId.value = ''
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
    window.removeEventListener('pointercancel', stop)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
  window.addEventListener('pointercancel', stop)
}

function outputPoint(nodeId: string, slot = 'output') {
  const node = nodes.value.find(item => item.id === nodeId)
  if (!node) return { x: 0, y: 0 }
  const y = node.type === 'condition' && slot === 'true'
    ? node.position.y + nodeHeight * .32
    : node.type === 'condition' && slot === 'false'
      ? node.position.y + nodeHeight * .7
      : node.position.y + nodeHeight / 2
  return { x: node.position.x + nodeWidth, y }
}
function inputPoint(nodeId: string, _slot = 'input') {
  const node = nodes.value.find(item => item.id === nodeId)
  return node ? { x: node.position.x, y: node.position.y + nodeHeight / 2 } : { x: 0, y: 0 }
}
function curve(source: { x: number; y: number }, target: { x: number; y: number }) {
  const offset = Math.max(60, Math.abs(target.x - source.x) * .45)
  return `M ${source.x} ${source.y} C ${source.x + offset} ${source.y}, ${target.x - offset} ${target.y}, ${target.x} ${target.y}`
}
function edgePath(edge: CanvasEdge) { return curve(outputPoint(edge.source, edge.source_slot), inputPoint(edge.target, edge.target_slot)) }
function previewPath() { return curve(outputPoint(connectingFrom.value, connectingSlot.value), pointer) }

function startConnection(nodeId: string, event: PointerEvent, slot = 'output') {
  event.preventDefault()
  connectingFrom.value = nodeId
  connectingSlot.value = slot
  Object.assign(pointer, canvasPoint(event))
}
function moveConnection(event: PointerEvent) {
  if (connectingFrom.value) Object.assign(pointer, canvasPoint(event))
}
function createsCycle(source: string, target: string) {
  const outgoing = new Map<string, string[]>()
  for (const edge of edges.value) outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge.target])
  const stack = [target], visited = new Set<string>()
  while (stack.length) {
    const current = stack.pop()!
    if (current === source) return true
    if (visited.has(current)) continue
    visited.add(current)
    stack.push(...(outgoing.get(current) || []))
  }
  return false
}
function finishConnection(target: string, targetSlot = 'input') {
  const source = connectingFrom.value
  const sourceSlot = connectingSlot.value
  connectingFrom.value = ''
  if (!source || source === target || target === 'input' || source === 'output') return
  if (edges.value.some(edge => edge.source === source && edge.target === target && (edge.source_slot || 'output') === sourceSlot)) return
  if (createsCycle(source, target)) return store.notify('连接会形成循环，已阻止', 'error')
  edges.value.push({ source, target, source_slot: sourceSlot, target_slot: targetSlot })
}
function cancelConnection() { connectingFrom.value = ''; connectingSlot.value = 'output' }
function selectEdge(index: number) {
  selectedEdgeIndex.value = index
  selectedNodeId.value = ''
}
function removeEdge(index: number) {
  edges.value.splice(index, 1)
  selectedEdgeIndex.value = null
}
function removeNode(nodeId: string) {
  const node = nodes.value.find(item => item.id === nodeId)
  if (!node || ['input', 'output'].includes(node.type)) return
  nodes.value = nodes.value.filter(item => item.id !== node.id)
  edges.value = edges.value.filter(edge => edge.source !== node.id && edge.target !== node.id)
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
}
function removeSelectedNode() { if (selectedNode.value) removeNode(selectedNode.value.id) }
function deleteSelection() {
  if (selectedEdgeIndex.value !== null) removeEdge(selectedEdgeIndex.value)
  else removeSelectedNode()
}
function selectNode(nodeId: string) {
  selectedNodeId.value = nodeId
  selectedEdgeIndex.value = null
}

async function setZoom(value: number) {
  const target = canvas.value
  const oldZoom = zoom.value
  const center = target ? {
    x: (target.scrollLeft + target.clientWidth / 2 - canvasGutter) / oldZoom,
    y: (target.scrollTop + target.clientHeight / 2 - canvasGutter) / oldZoom,
  } : null
  zoom.value = Math.max(.2, Math.min(1.8, Math.round(value * 100) / 100))
  await nextTick()
  if (target && center) {
    target.scrollLeft = canvasGutter + center.x * zoom.value - target.clientWidth / 2
    target.scrollTop = canvasGutter + center.y * zoom.value - target.clientHeight / 2
  }
}
function zoomBy(delta: number) { void setZoom(zoom.value + delta) }
async function fitCanvas() {
  await nextTick()
  const target = canvas.value
  if (!target) return
  const minX = nodes.value.length ? Math.min(...nodes.value.map(node => node.position.x)) : 0
  const minY = nodes.value.length ? Math.min(...nodes.value.map(node => node.position.y)) : 0
  const maxX = nodes.value.length ? Math.max(...nodes.value.map(node => node.position.x + nodeWidth)) : baseCanvasWidth
  const maxY = nodes.value.length ? Math.max(...nodes.value.map(node => node.position.y + nodeHeight)) : baseCanvasHeight
  const fitted = Math.min(
    target.clientWidth / Math.max(320, maxX - minX + 180),
    target.clientHeight / Math.max(220, maxY - minY + 180),
    1.3,
  )
  await setZoom(fitted)
  await nextTick()
  target.scrollLeft = canvasGutter + ((minX + maxX) / 2) * zoom.value - target.clientWidth / 2
  target.scrollTop = canvasGutter + ((minY + maxY) / 2) * zoom.value - target.clientHeight / 2
}
async function autoLayout() {
  const nodeIds = new Set(nodes.value.map(node => node.id))
  const incomingCount = new Map(nodes.value.map(node => [node.id, 0]))
  const outgoing = new Map<string, CanvasEdge[]>()
  for (const edge of edges.value) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue
    incomingCount.set(edge.target, (incomingCount.get(edge.target) || 0) + 1)
    outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge])
  }
  const queue = nodes.value.filter(node => !incomingCount.get(node.id)).map(node => node.id)
  const levels = new Map<string, number>(queue.map(id => [id, 0]))
  while (queue.length) {
    const source = queue.shift()!
    for (const edge of outgoing.get(source) || []) {
      levels.set(edge.target, Math.max(levels.get(edge.target) || 0, (levels.get(source) || 0) + 1))
      incomingCount.set(edge.target, (incomingCount.get(edge.target) || 1) - 1)
      if (!incomingCount.get(edge.target)) queue.push(edge.target)
    }
  }
  for (const node of nodes.value) if (!levels.has(node.id)) levels.set(node.id, 0)
  const columns = new Map<number, CanvasNode[]>()
  for (const node of nodes.value) {
    const level = levels.get(node.id) || 0
    columns.set(level, [...(columns.get(level) || []), node])
  }
  const maxRows = Math.max(1, ...[...columns.values()].map(items => items.length))
  const centerY = Math.max(280, maxRows * 70)
  for (const [level, items] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    items.sort((left, right) => {
      const leftSlot = edges.value.find(edge => edge.target === left.id)?.source_slot
      const rightSlot = edges.value.find(edge => edge.target === right.id)?.source_slot
      const rank = (slot?: string) => slot === 'true' ? -1 : slot === 'false' ? 1 : 0
      return rank(leftSlot) - rank(rightSlot) || left.label.localeCompare(right.label, 'zh-CN')
    })
    const totalHeight = (items.length - 1) * 132
    items.forEach((node, index) => {
      node.position.x = 70 + level * 245
      node.position.y = Math.max(40, centerY - totalHeight / 2 + index * 132)
    })
  }
  await fitCanvas()
  store.notify('已按执行依赖自动整理节点')
}
async function focusSelectedNode() {
  const target = canvas.value
  const node = selectedNode.value
  if (!target || !node) return
  target.scrollLeft = canvasGutter + (node.position.x + nodeWidth / 2) * zoom.value - target.clientWidth / 2
  target.scrollTop = canvasGutter + (node.position.y + nodeHeight / 2) * zoom.value - target.clientHeight / 2
}
function wheelZoom(event: WheelEvent) {
  if (!event.ctrlKey) return
  event.preventDefault()
  zoomBy(event.deltaY < 0 ? .1 : -.1)
}

function buildDefinition() {
  const preparedNodes = nodes.value.map(node => {
    const copy = JSON.parse(JSON.stringify(node))
    if (copy.type === 'agent' && copy.config.auto_input !== false) {
      const parents = edges.value.filter(edge => edge.target === copy.id).map(edge => edge.source)
      const upstream = parents.map(source => source === 'input' ? '原始任务：{{input.task}}' : `上游 ${nodes.value.find(item => item.id === source)?.label || source}：{{nodes.${source}.output}}`)
      copy.config.input = upstream.join('\n\n') || '{{input.task}}'
    }
    if (copy.type === 'knowledge' && copy.config.auto_input !== false) {
      const parents = edges.value.filter(edge => edge.target === copy.id).map(edge => edge.source)
      const upstream = parents.map(source => source === 'input' ? '{{input.task}}' : `{{nodes.${source}.output}}`)
      copy.config.query = upstream.join('\n\n') || copy.config.query || '{{input.task}}'
    }
    if (copy.type === 'output') {
      const parent = edges.value.find(edge => edge.target === copy.id)?.source
      copy.config.value = { result: parent && parent !== 'input' ? `{{nodes.${parent}.output}}` : '{{input.task}}' }
    }
    return copy
  })
  return {
    nodes: preparedNodes,
    edges: edges.value,
    variables: variables.value,
    execution: { ...execution },
  }
}

function validateWorkflow(ignoreAgentBindings = false) {
  if (!workflowForm.name.trim()) return '请填写工作流名称'
  if (!edges.value.some(edge => edge.source === 'input')) return '任务输入节点尚未连接'
  if (!edges.value.some(edge => edge.target === 'output')) return '结果输出节点尚未连接'
  const isolated = nodes.value.filter(node => !['input', 'output'].includes(node.type) && (!edges.value.some(edge => edge.target === node.id) || !edges.value.some(edge => edge.source === node.id)))
  if (isolated.length) return `节点“${isolated[0].label}”尚未完整连接`
  const badCondition = nodes.value.find(node => {
    if (node.type !== 'condition') return false
    const slots = new Set(edges.value.filter(edge => edge.source === node.id).map(edge => edge.source_slot))
    return !slots.has('true') || !slots.has('false')
  })
  if (badCondition) return `条件节点“${badCondition.label}”必须同时连接 TRUE 和 FALSE 分支`
  const invalidAgent = ignoreAgentBindings ? null : invalidAgentNodes.value[0]
  if (invalidAgent) return `Agent 节点“${invalidAgent.label}”：${nodeAgentBindingIssue(invalidAgent)}`
  const invalidKnowledge = nodes.value.find(node => node.type === 'knowledge' && !knowledgeBases.value.some(item => item.id === node.config.knowledge_base_id))
  if (invalidKnowledge) return `知识库节点“${invalidKnowledge.label}”绑定的知识库不存在`
  const outgoing = new Map<string, string[]>()
  const incoming = new Map<string, string[]>()
  for (const edge of edges.value) {
    outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge.target])
    incoming.set(edge.target, [...(incoming.get(edge.target) || []), edge.source])
  }
  const traverse = (start: string, graph: Map<string, string[]>) => {
    const visited = new Set([start]), queue = [start]
    while (queue.length) for (const next of graph.get(queue.shift()!) || []) if (!visited.has(next)) { visited.add(next); queue.push(next) }
    return visited
  }
  const reachable = traverse('input', outgoing)
  const reachesOutput = traverse('output', incoming)
  const unreachable = nodes.value.find(node => !reachable.has(node.id) || !reachesOutput.has(node.id))
  if (unreachable) return `节点“${unreachable.label}”不在任务输入到结果输出的完整链路中`
  const variableNames = variables.value.map(item => item.name.trim()).filter(Boolean)
  if (new Set(variableNames).size !== variableNames.length) return '工作流变量名称不能重复'
  return ''
}

function addVariable() {
  variables.value.push({
    name: `variable_${variables.value.length + 1}`,
    type: 'string',
    default: '',
    description: '',
    required: false,
  })
}

function removeVariable(index: number) {
  variables.value.splice(index, 1)
}

function addAssignment(node: CanvasNode) {
  node.config.assignments ||= []
  node.config.assignments.push({ name: '', operation: 'set', value: '' })
}

function applyDefinitionToCanvas(definition: Entity) {
  nodes.value = normalizeNodes(definition)
  edges.value = (definition.edges || []).map((edge: Entity) => ({
    source: edge.source,
    target: edge.target,
    source_slot: edge.source_slot || 'output',
    target_slot: edge.target_slot || 'input',
  }))
  variables.value = (definition.variables || []).map((item: Entity) => ({
    name: item.name || '',
    type: item.type || 'string',
    default: item.default ?? '',
    description: item.description || '',
    required: Boolean(item.required),
  }))
  Object.assign(execution, {
    loop_enabled: false,
    loop_count: 1,
    artifact_enabled: true,
    stop_condition: '',
    intent_validation: true,
    ...(definition.execution || {}),
  })
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
}

async function repairAgentBindings(showNotice = true) {
  if (!invalidAgentNodes.value.length) return true
  if (repairingBindings.value) return false
  repairingBindings.value = true
  try {
    const result = await api.post<Entity>('/workflow-expert/materialize', {
      proposal: {
        name: workflowForm.name,
        description: workflowForm.description,
        definition: buildDefinition(),
        agent_drafts: [],
      },
    })
    ;[agents.value, modelEndpoints.value] = await Promise.all([
      api.get('/agents'),
      api.get('/model-endpoints'),
    ])
    applyDefinitionToCanvas(result.definition || {})
    await nextTick()
    await fitCanvas()
    const repaired = result.binding_repairs?.length || result.created_agents?.length || 0
    if (showNotice) {
      store.notify(repaired
        ? `已自动修复 ${repaired} 个 Agent 绑定，全部使用现有在线接口`
        : 'Agent 在线绑定已重新校验')
    }
    return invalidAgentNodes.value.length === 0
  } catch (error: any) {
    store.notify(error.message || 'Agent 在线绑定自动修复失败', 'error')
    return false
  } finally {
    repairingBindings.value = false
  }
}

async function repairBindingsAndSave() {
  store.loading(true)
  try {
    if (await repairAgentBindings(true)) await persistWorkflow(false)
  } finally {
    store.loading(false)
  }
}

async function applyExpertProposal(proposal: Entity) {
  try {
    const definition = proposal.definition || {}
    ;[agents.value, modelEndpoints.value] = await Promise.all([
      api.get('/agents'),
      api.get('/model-endpoints'),
    ])
    workflowForm.name = proposal.name || workflowForm.name
    workflowForm.description = proposal.description || workflowForm.description
    if (proposal.objective) task.value = proposal.objective
    applyDefinitionToCanvas(definition)
    await nextTick()
    await fitCanvas()
    const saved = await persistWorkflow(false)
    if (!saved) throw new Error('编排草案未通过画板校验')
    store.notify(
      proposal.created_agents?.length
        ? `已创建 ${proposal.created_agents.length} 个在线 Agent，画板已校验并保存，可直接运行`
        : '工作流已应用、校验并保存，可继续手动调整或直接运行',
    )
  } catch (error: any) {
    store.notify(error.message || '工作流应用失败，请根据提示调整', 'error')
  }
}

async function persistWorkflow(showNotice = true) {
  const structuralError = validateWorkflow(true)
  if (structuralError) { store.notify(structuralError, 'error'); return null }
  if (invalidAgentNodes.value.length && !(await repairAgentBindings(false))) return null
  const error = validateWorkflow()
  if (error) { store.notify(error, 'error'); return null }
  const payload = { name: workflowForm.name, description: workflowForm.description, definition: buildDefinition() }
  const wasNew = !currentWorkflow.value
  const saved: Entity = currentWorkflow.value
    ? await api.put(`/workflows/${currentWorkflow.value.id}`, payload)
    : await api.post('/workflows', payload)
  currentWorkflow.value = saved
  if (wasNew) bindExpertSession(saved.id)
  workflows.value = await api.get('/workflows')
  if (showNotice) store.notify('可视化工作流已保存')
  return saved
}

async function saveWorkflow() {
  store.loading(true)
  try { await persistWorkflow(true) }
  catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

function clarificationPayload(confirmed = false) {
  return {
    task: clarificationOriginalTask.value || task.value.trim(),
    workflow_name: workflowForm.name,
    workflow_description: workflowForm.description,
    definition: buildDefinition(),
    answers: confirmed ? clarificationAnswers.value : {},
    confirmed,
  }
}

async function inspectWorkflowRequirements() {
  clarificationChecking.value = true
  clarificationOriginalTask.value = task.value.trim()
  try {
    const result = await api.post<ClarificationResult>('/workflow-clarification', clarificationPayload(false))
    if (!result.required || !result.questions.length) return true
    clarificationResult.value = result
    clarificationAnswers.value = Object.fromEntries(
      result.questions.map(question => [question.id, question.default ?? '']),
    )
    clarificationOpen.value = true
    return false
  } catch (error: any) {
    store.notify(error.message || '暂时无法分析任务完整度，请重试', 'error')
    return false
  } finally {
    clarificationChecking.value = false
  }
}

function closeClarification() {
  if (clarificationSubmitting.value) return
  clarificationOpen.value = false
}

async function confirmClarification() {
  if (!clarificationComplete.value) {
    store.notify('请补全所有必填要求，并检查数字范围', 'error')
    return
  }
  clarificationSubmitting.value = true
  try {
    const result = await api.post<ClarificationResult>('/workflow-clarification', clarificationPayload(true))
    task.value = result.resolved_task
    clarificationOpen.value = false
    store.notify('需求已确认，正在按明确要求启动工作流')
    await nextTick()
    await runWorkflow(true)
  } catch (error: any) {
    store.notify(error.message || '需求确认失败，请检查后重试', 'error')
  } finally {
    clarificationSubmitting.value = false
  }
}

async function runWorkflow(skipClarification = false) {
  if (workflowRunning.value) return
  if (clarificationChecking.value || (clarificationSubmitting.value && !skipClarification)) return
  if (!task.value.trim()) return store.notify('请先填写本次工作流的用户目标', 'error')
  const missingVariable = variables.value.find(item => item.required && (item.default === '' || item.default === null || item.default === undefined))
  if (missingVariable) return store.notify(`运行必填变量“${missingVariable.name}”尚未填写`, 'error')
  if (!skipClarification && !(await inspectWorkflowRequirements())) return
  workflowRunning.value = true
  workflowRunStatus.value = 'running'
  currentRunId.value = ''
  runPaused.value = false
  runArtifacts.value = []
  pendingApprovals.value = []
  runTimeline.value = []
  researchVisits.value = []
  runPanelTab.value = 'timeline'
  activeRunNodeId.value = ''
  runStartedAt.value = Date.now()
  runElapsedSeconds.value = 0
  lastRunEventId.value = 0
  resetNodeRuntime()
  output.value = '正在保存画板并启动工作流，请稍候…'
  appendRunTimeline('', '正在保存当前画板', '保存成功后将立即启动工作流', 'info', 'workflow_preparing')
  try {
    const saved = await persistWorkflow(false)
    if (!saved) {
      workflowRunStatus.value = 'idle'
      output.value = ''
      nodeRunStates.value = {}
      runTimeline.value = []
      return
    }
    output.value = '工作流已提交，正在建立实时运行连接…'
    await nextTick()
    let run: Entity = {}
    let receivedResult = false
    let streamError = ''
    await api.stream(`/workflows/${saved.id}/run/stream`, {
      input: {
        task: task.value,
        variables: Object.fromEntries(variables.value.map(item => [item.name, item.default])),
      },
      loop_enabled: execution.loop_enabled,
      loop_count: execution.loop_count,
      artifact_enabled: execution.artifact_enabled,
      security_profile: runSecurityProfile.value,
      permission_mode: runPermissionMode.value,
      approval_policy_id: runPermissionMode.value === 'inherit' ? (runApprovalPolicyId.value || null) : null,
    }, event => {
      if (event.type === 'workflow_result') { run = event.run; receivedResult = true }
      else if (event.type === 'error') streamError = event.message || '工作流运行流异常'
      else if (event.type === 'step') {
        handleWorkflowStep(event.step || {})
      }
    })
    if (streamError) throw new Error(streamError)
    if (!receivedResult) throw new Error('工作流运行结束但未返回结果')
    workflowRunStatus.value = run.status
    currentRunId.value = run.id || currentRunId.value
    if (run.status === 'completed') {
      output.value = workflowOutputMarkdown(run.output_json || '工作流已完成')
      runPanelTab.value = 'result'
    } else output.value = run.error || (run.status === 'interrupted' ? '工作流已由用户中断。' : '工作流执行失败，请查看最近运行记录。')
    if (currentRunId.value) runArtifacts.value = await api.get(`/workflow-runs/${currentRunId.value}/artifacts`)
    store.notify(run.status === 'completed' ? '工作流执行完成，产出已保存到数据库' : run.status === 'interrupted' ? '工作流已中断，已完成的产出仍被保留' : '工作流失败', run.status === 'completed' ? 'success' : 'error')
    runs.value = await api.get('/workflow-runs')
    persistRunState()
  } catch (error: any) {
    workflowRunStatus.value = 'failed'
    output.value = error.message || '工作流请求失败'
    appendRunTimeline(activeRunNodeId.value, '工作流请求失败', output.value, 'error', 'request_failed')
    store.notify(output.value, 'error')
  } finally {
    workflowRunning.value = false
    runPaused.value = false
    activeRunNodeId.value = ''
    elapsedSeconds()
    persistRunState()
  }
}

async function controlRun(action: 'pause' | 'resume' | 'interrupt') {
  if (!currentRunId.value) return
  try {
    await api.post(`/workflow-runs/${currentRunId.value}/control`, { action })
    if (action === 'pause') output.value = '暂停请求已发送，将在当前节点完成后暂停。'
    if (action === 'resume') runPaused.value = false
    if (action === 'interrupt') output.value = '中断请求已发送，将保留已经完成的产出。'
  } catch (error: any) {
    store.notify(error.message, 'error')
  }
}

async function sendRunGuidance() {
  const message = runGuidance.value.trim()
  if (!currentRunId.value || !message) return
  try {
    await api.post(`/workflow-runs/${currentRunId.value}/control`, {
      action: 'guide',
      message,
    })
    runGuidance.value = ''
    store.notify('引导已加入运行上下文')
  } catch (error: any) {
    store.notify(error.message, 'error')
  }
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement === studio.value) await document.exitFullscreen()
    else if (studio.value) await studio.value.requestFullscreen()
  } catch {
    fullScreen.value = !fullScreen.value
  }
  await nextTick()
  fitCanvas()
}

function syncFullscreen() {
  fullScreen.value = document.fullscreenElement === studio.value
  void nextTick().then(fitCanvas)
}

function keyboardDelete(event: KeyboardEvent) {
  const target = event.target as HTMLElement
  const editing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault()
    void saveWorkflow()
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    void runWorkflow()
    return
  }
  if (editing) return
  if (event.key === 'Delete' || event.key === 'Backspace') deleteSelection()
  else if (event.key.toLowerCase() === 'f') void fitCanvas()
  else if (event.key.toLowerCase() === 'l') void autoLayout()
  else if (event.key === 'Escape') cancelConnection()
}

onMounted(() => {
  void load()
  window.addEventListener('keydown', keyboardDelete)
  document.addEventListener('fullscreenchange', syncFullscreen)
})
onBeforeUnmount(() => {
  persistRunState()
  stopRunPolling()
  stopPaletteTracking()
  stopCanvasPan()
  window.removeEventListener('keydown', keyboardDelete)
  document.removeEventListener('fullscreenchange', syncFullscreen)
})
</script>

<template>
  <PageHeader eyebrow="VISUAL ORCHESTRATION" title="协作工作流画板" description="编排全部 Agent 与知识库资源，在同一工作台完成配置、运行和结果检查。">
    <div class="page-actions">
      <button class="btn" @click="newWorkflow"><Plus :size="15" />新建画板</button>
      <button class="btn expert-launch" @click="expertOpen=true"><Sparkles :size="15" />智能编排专家</button>
      <button class="btn" @click="toggleFullscreen"><Maximize2 :size="15" />全屏编排</button>
      <button class="btn btn-primary" @click="saveWorkflow"><Save :size="15" />保存工作流</button>
    </div>
  </PageHeader>

  <section ref="studio" class="workflow-studio card" :class="{
    'palette-collapsed': paletteCollapsed,
    'inspector-collapsed': inspectorCollapsed,
    'run-collapsed': runCollapsed,
    'is-fullscreen': fullScreen,
  }">
    <aside class="workflow-palette">
      <div class="studio-pane-title">
        <Library :size="16" />
        <span v-if="!paletteCollapsed">节点资源</span>
        <small v-if="!paletteCollapsed">{{ agents.length + knowledgeBases.length + componentNodes.length }}</small>
        <button class="pane-toggle" :title="paletteCollapsed ? '展开资源栏' : '收起资源栏'" @click="paletteCollapsed=!paletteCollapsed">
          <PanelLeftOpen v-if="paletteCollapsed" :size="15" /><PanelLeftClose v-else :size="15" />
        </button>
      </div>
      <template v-if="!paletteCollapsed">
        <nav class="resource-tabs">
          <button :class="{active:resourceTab==='agents'}" @click="resourceTab='agents';search=''"><Bot :size="13" />全部 Agent <b>{{ agents.length }}</b></button>
          <button :class="{active:resourceTab==='knowledge'}" @click="resourceTab='knowledge';search=''"><Database :size="13" />知识库 <b>{{ knowledgeBases.length }}</b></button>
          <button :class="{active:resourceTab==='components'}" @click="resourceTab='components';search=''"><Braces :size="13" />专业节点 <b>{{ componentNodes.length }}</b></button>
        </nav>
        <div class="palette-search"><Search :size="13" /><input v-model="search" :placeholder="resourceTab==='agents'?'搜索名称、说明或标识':resourceTab==='knowledge'?'搜索知识库':'搜索专业节点'"></div>
        <div v-if="resourceTab==='agents'" class="palette-agents resource-list">
          <article v-for="agent in visibleAgents" :key="agent.id" class="palette-agent" :class="{disabled:!executableAgent(agent)}" :title="executableAgent(agent)?'拖入画板；双击可添加到画布中央':'历史版本仅展示，不能加入运行工作流'" @pointerdown.prevent="startPalettePointer($event,agent,'agent')" @dblclick="addResourceToCenter('agent',agent)">
            <GripVertical :size="15" />
            <div><strong>{{ agent.name }}</strong><span>v{{ agent.version }} · {{ agent.description || agent.slug || '可编排智能体' }}</span></div>
            <em :class="agent.status">{{ statusLabel(agent.status) }}</em>
          </article>
          <div v-if="!visibleAgents.length" class="empty compact">没有匹配的 Agent</div>
        </div>
        <div v-else-if="resourceTab==='knowledge'" class="palette-agents resource-list knowledge-resources">
          <article v-for="base in visibleKnowledgeBases" :key="base.id" class="palette-agent palette-knowledge" title="拖入画板；双击可添加到画布中央" @pointerdown.prevent="startPalettePointer($event,base,'knowledge')" @dblclick="addResourceToCenter('knowledge',base)">
            <Database :size="15" />
            <div><strong>{{ base.name }}</strong><span>{{ base.discipline || '通用' }} · {{ base.document_count || 0 }} 份文档</span></div>
            <em>知识</em>
          </article>
          <div v-if="!visibleKnowledgeBases.length" class="empty compact">暂无知识库，可先在“学科知识库”中创建</div>
        </div>
        <div v-else class="palette-agents resource-list component-resources">
          <article v-for="item in componentNodes.filter(node=>`${node.name} ${node.description}`.includes(search))" :key="item.id" class="palette-agent palette-component" title="拖入画板；双击可添加到画布中央" @pointerdown.prevent="startPalettePointer($event,item,'component')" @dblclick="addResourceToCenter('component',item)">
            <component :is="item.icon" :size="15" />
            <div><strong>{{ item.name }}</strong><span>{{ item.description }}</span></div>
            <em>NODE</em>
          </article>
        </div>
        <div class="palette-workflows">
          <label><Workflow :size="12" />已保存工作流 <span>{{ workflows.length }}</span></label>
          <button v-for="item in workflows" :key="item.id" :class="{ active: currentWorkflow?.id===item.id }" @click="selectWorkflow(item)"><Workflow :size="13" /><span>{{ item.name }}</span></button>
          <div v-if="!workflows.length" class="empty compact">尚未保存工作流</div>
        </div>
      </template>
    </aside>

    <div class="workflow-canvas-shell">
      <div class="canvas-toolbar">
        <div class="canvas-title"><strong>{{ workflowForm.name }}</strong><span>{{ nodes.length }} 节点 · {{ edges.length }} 连线 · {{ nodes.filter(item=>item.type==='agent').length }} Agent · {{ nodes.filter(item=>item.type==='knowledge').length }} 知识库</span></div>
        <div class="workflow-validity-group">
          <div class="workflow-validity" :class="{valid:!validationMessage}" :title="validationMessage || '工作流链路完整，可直接运行'"><i />{{ validationMessage || '链路完整' }}</div>
          <button v-if="invalidAgentNodes.length" class="workflow-binding-repair" :disabled="repairingBindings" @click="repairBindingsAndSave">
            <RotateCw :size="12" :class="{spin:repairingBindings}" />{{ repairingBindings ? '修复中' : `修复 ${invalidAgentNodes.length} 个绑定` }}
          </button>
        </div>
        <div class="canvas-hint"><MousePointer2 :size="13" /><span>按住左键拖动画布</span></div>
        <div class="canvas-tools">
          <button title="自动整理节点（L）" @click="autoLayout"><Workflow :size="13" /></button>
          <button :disabled="!selectedNode" title="定位选中节点" @click="focusSelectedNode"><MousePointer2 :size="13" /></button>
          <i />
          <button title="缩小" @click="zoomBy(-.1)"><Minus :size="13" /></button><span>{{ zoomLabel }}</span><button title="放大" @click="zoomBy(.1)"><ZoomIn :size="13" /></button><button title="适应画布" @click="fitCanvas"><Maximize2 :size="13" /></button>
          <i />
          <button :title="inspectorCollapsed?'展开属性栏':'收起属性栏'" @click="inspectorCollapsed=!inspectorCollapsed"><PanelRightOpen v-if="inspectorCollapsed" :size="13" /><PanelRightClose v-else :size="13" /></button>
          <button :title="fullScreen?'退出全屏':'进入全屏'" @click="toggleFullscreen"><Minimize2 v-if="fullScreen" :size="13" /><Maximize2 v-else :size="13" /></button>
          <button class="danger" :disabled="!selectedNode && !selectedEdge" title="删除选中的节点或连线" @click="deleteSelection"><Trash2 :size="13" /></button>
        </div>
      </div>
      <div ref="canvas" class="workflow-canvas" :class="{panning:canvasPan.active}" @dragover.prevent @drop.prevent="dropAgent" @pointerdown="startCanvasPan" @pointermove="moveConnection" @pointerup="cancelConnection" @wheel="wheelZoom" @click.self="selectedNodeId='';selectedEdgeIndex=null">
        <div class="workflow-canvas-stage" :style="{width:`${canvasSize.width*zoom+canvasGutter*2}px`,height:`${canvasSize.height*zoom+canvasGutter*2}px`}">
          <div class="workflow-canvas-content" :style="{left:`${canvasGutter}px`,top:`${canvasGutter}px`,width:`${canvasSize.width}px`,height:`${canvasSize.height}px`,transform:`scale(${zoom})`}" @click.self="selectedNodeId='';selectedEdgeIndex=null">
            <svg class="workflow-wires" :width="canvasSize.width" :height="canvasSize.height">
              <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#4f91cb" /></marker></defs>
              <path v-for="(edge,index) in edges" :key="`${edge.source}-${edge.target}`" :d="edgePath(edge)" class="workflow-wire" :class="[{selected:selectedEdgeIndex===index},`run-${edgeRuntimeStatus(edge)}`]" marker-end="url(#workflow-arrow)" @click.stop="selectEdge(index)" @dblclick.stop="removeEdge(index)" />
              <path v-if="connectingFrom" :d="previewPath()" class="workflow-wire preview" />
            </svg>
            <article v-for="node in nodes" :key="node.id" class="workflow-node" :class="[node.type,{selected:selectedNodeId===node.id,moving:movingNodeId===node.id,'binding-invalid':Boolean(nodeAgentBindingIssue(node))},`run-${nodeRuntime(node.id).status}`]" :style="{left:`${node.position.x}px`,top:`${node.position.y}px`}" :title="nodeAgentBindingIssue(node) || `${node.label} · ${runStatusLabel(nodeRuntime(node.id).status)} · ${nodeRuntime(node.id).stage}`" @pointerdown.stop="startMove(node,$event)" @click.stop="selectNode(node.id)">
              <button v-if="node.type!=='input'" class="node-port input-port" title="输入端口" @pointerup.stop="finishConnection(node.id)" />
              <div class="node-icon"><Bot v-if="node.type==='agent'" :size="17" /><Database v-else-if="node.type==='knowledge'" :size="17" /><Braces v-else-if="node.type==='variable'" :size="17" /><Code2 v-else-if="node.type==='template'" :size="17" /><GitMerge v-else-if="node.type==='merge'" :size="17" /><FileText v-else-if="node.type==='artifact'" :size="17" /><GitBranch v-else-if="['input','condition'].includes(node.type)" :size="17" /><CircleStop v-else :size="17" /></div>
              <div class="node-copy"><small>{{ node.type.toUpperCase() }}</small><strong>{{ node.label }}</strong></div>
              <button v-if="!['input','output'].includes(node.type)" class="node-delete" title="删除此节点" @pointerdown.stop @click.stop="removeNode(node.id)"><Trash2 :size="11" /></button>
              <span v-if="nodeRuntime(node.id).status!=='idle'" class="node-run-indicator" :class="nodeRuntime(node.id).status"><i />{{ runStatusLabel(nodeRuntime(node.id).status) }}</span>
              <span v-if="nodeRuntime(node.id).status==='running'" class="node-run-stage">{{ nodeRuntime(node.id).stage }}</span>
              <span v-if="nodeRuntime(node.id).status==='running'" class="node-run-progress"><i :style="{width:`${nodeRuntime(node.id).progress}%`}" /></span>
              <span v-if="nodeAgentBindingIssue(node)" class="node-binding-warning"><CircleAlert :size="10" />待修复</span>
              <template v-if="node.type==='condition'">
                <button class="node-port output-port branch-true" title="TRUE 分支" @pointerdown.stop="startConnection(node.id,$event,'true')" /><span class="slot-label true">TRUE</span>
                <button class="node-port output-port branch-false" title="FALSE 分支" @pointerdown.stop="startConnection(node.id,$event,'false')" /><span class="slot-label false">FALSE</span>
              </template>
              <button v-else-if="node.type!=='output'" class="node-port output-port" title="输出槽位" @pointerdown.stop="startConnection(node.id,$event,'output')" />
            </article>
          </div>
        </div>
        <button class="workflow-minimap" title="工作流缩略图 · 点击适应画布" @click.stop="fitCanvas">
          <span v-for="node in nodes" :key="node.id" :class="[node.type,nodeRuntime(node.id).status]" :style="{left:`${Math.min(94,Math.max(2,node.position.x/canvasSize.width*100))}%`,top:`${Math.min(88,Math.max(5,node.position.y/canvasSize.height*100))}%`}" />
          <small>{{ nodes.length }} NODES</small>
        </button>
      </div>
    </div>

    <aside class="workflow-inspector">
      <div class="studio-pane-title">
        <Settings2 :size="16" /><span v-if="!inspectorCollapsed">属性设置</span>
        <button class="pane-toggle" :title="inspectorCollapsed?'展开属性栏':'收起属性栏'" @click="inspectorCollapsed=!inspectorCollapsed"><PanelRightOpen v-if="inspectorCollapsed" :size="15" /><PanelRightClose v-else :size="15" /></button>
      </div>
      <div v-if="!inspectorCollapsed" class="inspector-body">
        <div class="field"><label>工作流名称</label><input v-model="workflowForm.name" class="input"></div>
        <div class="field"><label>工作流说明</label><textarea v-model="workflowForm.description" class="textarea inspector-textarea" /></div>
        <section class="inspector-section">
          <header><span><Braces :size="12" />工作流变量</span><button @click="addVariable"><Plus :size="12" />添加</button></header>
          <div v-for="(item,index) in variables" :key="index" class="workflow-variable-card">
            <div><input v-model="item.name" class="input" placeholder="变量名"><select v-model="item.type" class="select"><option value="string">文本</option><option value="number">数字</option><option value="boolean">布尔</option><option value="object">对象</option><option value="array">数组</option></select><button title="删除变量" @click="removeVariable(index)"><Trash2 :size="12" /></button></div>
            <input v-model="item.default" class="input" placeholder="默认值">
            <input v-model="item.description" class="input" placeholder="变量说明">
            <label><input v-model="item.required" type="checkbox">运行时必填</label>
          </div>
          <div v-if="!variables.length" class="empty compact">可添加全局变量，并用 &#123;&#123;variables.name&#125;&#125; 引用。</div>
        </section>
        <section class="inspector-section execution-settings">
          <header><span><RotateCw :size="12" />循环与交付</span></header>
          <label class="switch-line"><input v-model="execution.loop_enabled" type="checkbox"><span>循环执行工作流</span></label>
          <div v-if="execution.loop_enabled" class="field"><label>最多执行次数</label><input v-model.number="execution.loop_count" class="input" type="number" min="1" max="50"></div>
          <div v-if="execution.loop_enabled" class="field"><label>提前停止条件</label><input v-model="execution.stop_condition" class="input" placeholder="{{nodes.quality_gate.passed}}"></div>
          <label class="switch-line"><input v-model="execution.artifact_enabled" type="checkbox"><span>每轮自动生成产出文档</span></label>
          <label class="switch-line"><input v-model="execution.intent_validation" type="checkbox"><span>完成后校验并修正用户意图</span></label>
        </section>
        <template v-if="selectedNode">
          <div class="inspector-divider" />
          <section v-if="nodeRuntime(selectedNode.id).status!=='idle'" class="inspector-section node-runtime-card" :class="nodeRuntime(selectedNode.id).status">
            <header><span><Activity :size="12" />节点运行状态</span><b>{{ runStatusLabel(nodeRuntime(selectedNode.id).status) }}</b></header>
            <div class="node-runtime-meter"><i :style="{width:`${nodeRuntime(selectedNode.id).progress}%`}" /></div>
            <p><strong>{{ nodeRuntime(selectedNode.id).stage }}</strong><span>第 {{ nodeRuntime(selectedNode.id).iteration || 1 }} 轮 · {{ nodeRuntime(selectedNode.id).eventCount }} 条内部事件<span v-if="nodeRuntime(selectedNode.id).durationMs"> · {{ nodeRuntime(selectedNode.id).durationMs }} ms</span></span></p>
            <small v-if="nodeRuntime(selectedNode.id).detail">{{ nodeRuntime(selectedNode.id).detail }}</small>
            <pre v-if="nodeRuntime(selectedNode.id).outputPreview">{{ nodeRuntime(selectedNode.id).outputPreview }}</pre>
            <small v-if="nodeRuntime(selectedNode.id).error" class="runtime-error">{{ nodeRuntime(selectedNode.id).error }}</small>
          </section>
          <div class="field"><label>节点名称</label><input v-model="selectedNode.label" class="input"></div>
          <div class="field"><label>节点类型</label><input :value="selectedNode.type" class="input" disabled></div>
          <template v-if="selectedNode.type==='agent'">
            <div class="field"><label>绑定 Agent</label><select v-model="selectedNode.config.agent_id" class="select"><option value="" disabled>请选择在线 Agent</option><option v-for="agent in agents" :key="agent.id" :value="agent.id" :disabled="!executableAgent(agent)">{{ agent.name }} · v{{ agent.version }} · {{ executableAgent(agent) ? '在线可用' : `${statusLabel(agent.status)} / 接口不可用` }}</option></select></div>
            <section v-if="nodeAgentBindingIssue(selectedNode)" class="agent-binding-repair-card">
              <CircleAlert :size="16" />
              <div><strong>此节点暂时无法运行</strong><span>{{ nodeAgentBindingIssue(selectedNode) }}</span></div>
              <button :disabled="repairingBindings" @click="repairBindingsAndSave"><RotateCw :size="12" :class="{spin:repairingBindings}" />{{ repairingBindings ? '正在修复' : '自动创建并绑定在线 Agent' }}</button>
            </section>
            <label class="switch-line"><input v-model="selectedNode.config.auto_input" type="checkbox"><span>根据入线自动聚合输入</span></label>
            <div class="field"><label>节点工具策略</label><select v-model="selectedNode.config.tool_policy" class="select"><option v-for="policy in agentToolPolicies" :key="policy.value" :value="policy.value">{{ policy.label }}</option></select><span class="field-help">{{ toolPolicyDescription(selectedNode.config.tool_policy) }}</span></div>
            <div class="field"><label>节点 RAG 策略</label><select v-model="selectedNode.config.rag_mode" class="select"><option v-for="mode in agentRagModes" :key="mode.value" :value="mode.value">{{ mode.label }}</option></select><span class="field-help">{{ ragModeDescription(selectedNode.config.rag_mode) }}</span></div>
            <div v-if="['balanced','full'].includes(selectedNode.config.tool_policy)" class="field-grid two-col compact-grid">
              <div class="field"><label>最多工具轮数</label><input v-model.number="selectedNode.config.max_tool_iterations" type="number" min="1" max="8" class="input" placeholder="自动"></div>
              <div class="field"><label>最多工具请求</label><input v-model.number="selectedNode.config.max_tool_calls" type="number" min="0" max="64" class="input" placeholder="自动"></div>
            </div>
            <div class="field"><label>节点输入预算（字符）</label><input v-model.number="selectedNode.config.input_context_char_limit" type="number" min="8000" max="120000" step="4000" class="input" placeholder="按职责自动"><span class="field-help">只压缩超出预算的长上下文，并保留开头和结尾；不会触发额外模型请求。</span></div>
            <div class="field"><label>节点整体重试</label><input v-model.number="selectedNode.config.retry_count" type="number" min="0" max="3" class="input"><span class="field-help">默认 0；模型接口内部已处理安全重试，节点整体重跑可能产生重复费用。</span></div>
            <div class="field"><label>最长输出 Token</label><input v-model.number="selectedNode.config.max_output_tokens" type="number" min="512" max="32768" step="512" class="input"><span class="field-help">长文撰写建议 12000–20000；该值仅覆盖当前节点，不修改 Agent 全局设置。</span></div>
            <div class="field"><label>节点专用任务说明</label><textarea v-model="selectedNode.config.prompt" class="textarea inspector-textarea" placeholder="定义本节点角色、交付结构、证据边界和验收标准" /><span class="field-help">此说明会在运行时真实传给 Agent，并支持引用上游变量。</span></div>
            <div v-if="selectedNode.config.auto_input===false" class="field"><label>输入模板</label><textarea v-model="selectedNode.config.input" class="textarea inspector-textarea" /></div>
            <div class="notice">可引用 &#123;&#123;input.task&#125;&#125;、&#123;&#123;variables.name&#125;&#125; 和 &#123;&#123;nodes.node_id.output&#125;&#125;。</div>
          </template>
          <template v-if="selectedNode.type==='knowledge'">
            <div class="field"><label>绑定知识库</label><select v-model="selectedNode.config.knowledge_base_id" class="select"><option v-for="base in knowledgeBases" :key="base.id" :value="base.id">{{ base.name }} · {{ base.document_count || 0 }} 份文档</option></select></div>
            <div class="field"><label>召回片段数</label><input v-model.number="selectedNode.config.top_k" type="number" min="1" max="20" class="input"></div>
            <label class="switch-line"><input v-model="selectedNode.config.auto_input" type="checkbox"><span>根据入线自动生成检索问题</span></label>
            <div v-if="selectedNode.config.auto_input===false" class="field"><label>检索模板</label><textarea v-model="selectedNode.config.query" class="textarea inspector-textarea" /></div>
            <div class="notice knowledge-notice">将知识库连到 Agent，可把可追溯资料作为 Agent 的上游输入。</div>
          </template>
          <template v-if="selectedNode.type==='condition'">
            <div class="field"><label>左值 / 变量</label><input v-model="selectedNode.config.left" class="input" placeholder="{{nodes.agent.output}}"></div>
            <div class="field"><label>比较运算</label><select v-model="selectedNode.config.operator" class="select"><option value="equals">等于</option><option value="not_equals">不等于</option><option value="contains">包含</option><option value="not_contains">不包含</option><option value="greater">大于</option><option value="less">小于</option><option value="exists">存在</option><option value="empty">为空</option><option value="regex">正则匹配</option></select></div>
            <div class="field"><label>右值</label><input v-model="selectedNode.config.right" class="input"></div>
            <div class="branch-legend"><span>TRUE 槽位</span><span>FALSE 槽位</span></div>
          </template>
          <template v-if="selectedNode.type==='variable'">
            <div v-for="(assignment,index) in selectedNode.config.assignments || []" :key="index" class="assignment-row">
              <input v-model="assignment.name" class="input" placeholder="变量名">
              <select v-model="assignment.operation" class="select"><option value="set">设置</option><option value="append">追加</option><option value="increment">递增</option></select>
              <input v-model="assignment.value" class="input" placeholder="值或变量引用">
              <button @click="selectedNode.config.assignments.splice(index,1)"><Trash2 :size="11" /></button>
            </div>
            <button class="btn btn-sm" @click="addAssignment(selectedNode)"><Plus :size="12" />增加赋值</button>
          </template>
          <div v-if="selectedNode.type==='template'" class="field"><label>模板内容</label><textarea v-model="selectedNode.config.template" class="textarea template-editor" /></div>
          <template v-if="selectedNode.type==='function'">
            <div class="field"><label>安全函数</label><select v-model="selectedNode.config.function" class="select"><option value="concat">concat · 拼接</option><option value="join">join · 数组合并</option><option value="split">split · 文本拆分</option><option value="length">length · 长度</option><option value="unique">unique · 去重</option><option value="json_parse">json_parse · 解析</option><option value="json_stringify">json_stringify · 序列化</option><option value="pick">pick · 取字段</option><option value="coalesce">coalesce · 首个非空</option></select></div>
            <div class="field"><label>参数 1</label><input v-model="selectedNode.config.arguments[0]" class="input"></div>
            <div class="field"><label>参数 2</label><input v-model="selectedNode.config.arguments[1]" class="input"></div>
          </template>
          <template v-if="selectedNode.type==='merge'">
            <div class="field"><label>聚合模式</label><select v-model="selectedNode.config.mode" class="select"><option value="text">拼接文本</option><option value="list">列表</option><option value="object">按节点 ID 组成对象</option></select></div>
            <div v-if="selectedNode.config.mode==='text'" class="field"><label>分隔符</label><input v-model="selectedNode.config.separator" class="input"></div>
          </template>
          <template v-if="selectedNode.type==='tool'">
            <div class="field"><label>工具</label><select v-model="selectedNode.config.tool" class="select"><option v-for="item in tools" :key="item.name || item.id" :value="item.name || item.id">{{ item.name || item.id }}</option></select></div>
            <div class="field"><label>审批模式</label><select v-model="selectedNode.config.permission_mode" class="select"><option value="ask">按安全策略审批</option><option value="auto">自动执行允许操作</option><option value="deny">禁止执行</option></select></div>
          </template>
          <template v-if="selectedNode.type==='artifact'">
            <div class="field"><label>文档标题</label><input v-model="selectedNode.config.title" class="input"></div>
            <div class="field"><label>Markdown 内容模板</label><textarea v-model="selectedNode.config.content" class="textarea template-editor" /></div>
          </template>
          <div v-if="selectedNode.type==='output'" class="notice">输出节点自动读取最后一条入线；也可以在保存后的定义中使用变量表达式。</div>
          <button v-if="!['input','output'].includes(selectedNode.type)" class="btn btn-danger" @click="removeSelectedNode"><Trash2 :size="14" />删除节点</button>
        </template>
        <template v-else-if="selectedEdge">
          <div class="inspector-divider" />
          <div class="notice">已选择连线：{{ nodes.find(item=>item.id===selectedEdge?.source)?.label }} [{{ selectedEdge.source_slot || 'output' }}] → {{ nodes.find(item=>item.id===selectedEdge?.target)?.label }} [{{ selectedEdge.target_slot || 'input' }}]</div>
          <button class="btn btn-danger" @click="deleteSelection"><Trash2 :size="14" />删除连线关系</button>
        </template>
        <div v-else class="empty compact">选中画布节点后配置属性；单击连线可删除。</div>
      </div>
    </aside>

    <section class="workflow-run-drawer">
      <header @click="runCollapsed=!runCollapsed">
        <button class="drawer-toggle" :title="runCollapsed?'展开运行配置':'收起运行配置'"><ChevronRight v-if="runCollapsed" :size="15" /><ChevronDown v-else :size="15" /></button>
        <div><Play :size="15" /><strong>运行与调试</strong><span v-if="runCollapsed">{{ task || '填写任务输入' }}</span></div>
        <StatusBadge v-if="workflowRunStatus!=='idle'" :status="workflowRunStatus" />
        <div v-if="workflowRunning" class="run-header-controls" @click.stop>
          <button class="btn btn-sm" @click="controlRun(runPaused?'resume':'pause')"><Play v-if="runPaused" :size="13" /><Pause v-else :size="13" />{{ runPaused ? '继续' : '暂停' }}</button>
          <button class="btn btn-sm btn-danger" @click="controlRun('interrupt')"><Square :size="12" />中断</button>
        </div>
        <button class="btn btn-primary" :disabled="workflowRunning || clarificationChecking" @click.stop="runCollapsed=false;runWorkflow()"><Play :size="14" />{{ workflowRunning ? '运行中…' : clarificationChecking ? '分析需求…' : '开始运行' }}</button>
      </header>
      <div v-if="!runCollapsed" class="workflow-run-content">
        <div class="run-input-panel">
          <div class="field"><label>任务输入</label><textarea v-model="task" class="textarea" placeholder="描述本次工作流需要完成的真实任务" /></div>
          <div class="run-security-config">
            <div class="run-security-title"><ShieldCheck :size="13" /><span><strong>本次运行安全策略</strong><small>{{ runApprovalSummary }}</small></span></div>
            <div class="run-security-fields">
              <div class="field"><label>访问范围</label><select v-model="runSecurityProfile" class="select" :disabled="workflowRunning"><option v-for="profile in runSecurityProfiles" :key="profile.value" :value="profile.value">{{ profile.label }}</option></select></div>
              <div class="field"><label>审批方式</label><select v-model="runPermissionMode" class="select" :disabled="workflowRunning"><option value="inherit">继承 Agent / 全局策略</option><option value="ask">需要人工审批</option><option value="auto">无需审批，自动执行</option><option value="deny">禁止中高风险操作</option></select></div>
            </div>
            <div v-if="runPermissionMode==='inherit' && approvalPolicies.length" class="field"><label>审批策略</label><select v-model="runApprovalPolicyId" class="select" :disabled="workflowRunning"><option value="">继承各 Agent 已配置策略</option><option v-for="policy in approvalPolicies.filter(item=>item.enabled)" :key="policy.id" :value="policy.id">{{ policy.name }}</option></select></div>
            <p>{{ selectedRunSecurityProfile?.description }}</p>
          </div>
          <div class="run-loop-config">
            <label><input v-model="execution.loop_enabled" type="checkbox">循环执行</label>
            <input v-if="execution.loop_enabled" v-model.number="execution.loop_count" type="number" min="1" max="50" class="input">
            <span v-if="execution.loop_enabled">次</span>
            <label><input v-model="execution.artifact_enabled" type="checkbox">每轮产出文档</label>
          </div>
        </div>
        <div class="run-output-panel">
          <div class="run-output-heading">
            <label>{{ workflowRunning ? '实时执行链路' : '运行详情' }}</label>
            <div class="run-result-actions">
              <nav>
                <button :class="{active:runPanelTab==='timeline'}" @click="runPanelTab='timeline'">执行过程 <b>{{ runTimeline.length }}</b></button>
                <button :class="{active:runPanelTab==='web'}" @click="runPanelTab='web'">访问网站 <b>{{ researchVisits.length }}</b><i v-if="pendingResearchVerifications">{{ pendingResearchVerifications }}</i></button>
                <button :class="{active:runPanelTab==='result'}" @click="runPanelTab='result'">最终成果</button>
              </nav>
              <button v-if="currentRunId && !workflowRunning" class="word-download-link" :disabled="!!exportingDocumentId || workflowRunStatus!=='completed'" :title="workflowRunStatus==='completed' ? '下载通过质量校验的最终成果' : '运行未通过质量校验，暂不能导出最终成果'" @click="downloadWorkflowWord"><Download :size="11" />{{ exportingDocumentId==='run' ? '生成中…' : workflowRunStatus==='completed' ? '下载 Word' : '终稿未就绪' }}</button>
            </div>
          </div>
          <div v-if="runTimeline.length || workflowRunning" class="run-progress-overview">
            <div><strong>{{ workflowRunning ? (activeRunNodeId ? nodeRuntime(activeRunNodeId).stage : '工作流调度中') : workflowRunStatus==='completed' ? '全部节点执行完成' : workflowRunStatus==='failed' ? '执行失败，请查看红色节点' : '运行已结束' }}</strong><span>{{ runProgress }}% · {{ runStats.completed }}/{{ runStats.total }} 完成<span v-if="runStats.skipped"> · {{ runStats.skipped }} 跳过</span><span v-if="runStats.failed"> · {{ runStats.failed }} 失败</span> · {{ runElapsedSeconds }} 秒</span></div>
            <div class="run-overall-meter"><i :style="{width:`${runProgress}%`}" /></div>
          </div>
          <section v-if="pendingApprovals.length" class="workflow-pending-approvals">
            <header><ShieldCheck :size="13" /><strong>等待你的安全确认</strong><span>{{ pendingApprovals.length }} 项</span></header>
            <article v-for="approval in pendingApprovals" :key="approval.id">
              <div><strong>{{ approval.summary }}</strong><span>{{ approval.risk_level || 'medium' }} 风险 · {{ approval.action_type }} · 工作流已在此操作处等待</span></div>
              <nav><button :disabled="!!decidingApprovalId" @click="decideRunApproval(approval,true)"><Check :size="11" />批准并继续</button><button class="reject" :disabled="!!decidingApprovalId" @click="decideRunApproval(approval,false)"><X :size="11" />拒绝</button></nav>
            </article>
          </section>
          <div v-if="runPanelTab==='timeline'" class="run-activity-list">
            <article v-for="item in [...runTimeline].reverse()" :key="item.id" :class="item.tone" @click="item.nodeId && selectNode(item.nodeId)">
              <i /><div><header><strong>{{ item.title }}</strong><time>+{{ item.elapsed }}s</time></header><span>{{ item.nodeLabel }}</span><p v-if="item.detail">{{ item.detail }}</p></div>
            </article>
            <div v-if="!runTimeline.length" class="run-activity-empty">运行后将在这里实时展示 Agent 内部步骤。</div>
          </div>
          <div v-else-if="runPanelTab==='web'" class="run-research-overview">
            <div v-if="researchVisits.length" class="run-research-sites">
              <article v-for="visit in researchVisits.slice(-8).reverse()" :key="visit.id" :class="visit.status">
                <Globe2 :size="12" /><span><strong>{{ visit.title }}</strong><small>{{ visit.provider }} · {{ visit.status==='verification_required' ? '等待机器人验证' : visit.status }}</small></span>
              </article>
            </div>
            <div v-else class="run-activity-empty">当前尚无联网访问记录。</div>
            <button class="research-center-launch" @click="researchBrowserOpen=true"><Globe2 :size="13" />打开联网访问中心<span v-if="pendingResearchVerifications">待验证 {{ pendingResearchVerifications }}</span></button>
          </div>
          <div v-else class="result-box workflow-markdown-result" :class="{running:workflowRunning,empty:!output}">
            <RichAgentMessage v-if="output" :content="workflowOutputMarkdown(output)" />
            <span v-else>工作流完成后将在这里展示最终成果。</span>
          </div>
          <div v-if="workflowRunning" class="run-guidance">
            <input v-model="runGuidance" class="input" placeholder="运行中补充要求或纠偏指令" @keydown.enter.prevent="sendRunGuidance">
            <button :disabled="!currentRunId || !runGuidance.trim()" @click="sendRunGuidance"><Send :size="12" />引导</button>
          </div>
        </div>
        <aside class="run-history-panel">
          <label>{{ runArtifacts.length ? '本次产出文档' : '最近运行' }}</label>
          <div v-if="runArtifacts.length" class="workflow-artifact-list">
            <article v-for="artifact in runArtifacts" :key="artifact.id" tabindex="0" title="点击预览 Markdown 成果" @click="previewArtifact(artifact)" @keydown.enter="previewArtifact(artifact)">
              <FileText :size="13" /><span><strong>{{ artifact.title }}</strong><small>第 {{ artifact.iteration }} 轮 · 已保存到数据库</small></span>
              <button class="artifact-download-link" :disabled="!!exportingDocumentId || !artifactReady(artifact)" :title="artifactReady(artifact) ? '下载排版后的 Word 文档' : '该产出未通过最终质量校验'" @click.stop="downloadArtifactWord(artifact)"><Download :size="11" />{{ exportingDocumentId===artifact.id ? '生成中' : artifactReady(artifact) ? 'Word' : '待修订' }}</button>
            </article>
          </div>
          <div class="run-history-list"><div v-for="run in runs.slice(0,5)" :key="run.id"><span><strong>{{ run.duration_ms }} ms · {{ run.iteration_count || 1 }} 轮</strong><small>{{ new Date(run.created_at).toLocaleString('zh-CN') }}</small></span><StatusBadge :status="run.status" /></div><p v-if="!runs.length">暂无运行记录</p></div>
        </aside>
      </div>
    </section>
  </section>
  <div v-if="paletteDrag.active && paletteDrag.item" class="palette-drag-ghost" :class="{knowledge:paletteDrag.kind==='knowledge'}" :style="{left:`${paletteDrag.x+14}px`,top:`${paletteDrag.y+14}px`}"><Database v-if="paletteDrag.kind==='knowledge'" :size="14" /><Braces v-else-if="paletteDrag.kind==='component'" :size="14" /><Bot v-else :size="14" />{{ paletteDrag.item.name }}</div>
  <FloatingPanel
    v-model="clarificationOpen"
    title="运行前确认需求"
    eyebrow="REQUIREMENT CHECK"
    description="先补齐会影响执行结果的关键要求，确认后才会正式启动工作流。"
    size="large"
    :close-on-backdrop="false"
  >
    <div v-if="clarificationResult" class="workflow-clarification">
      <section class="clarification-overview">
        <div class="clarification-icon"><CircleHelp :size="22" /></div>
        <div>
          <span>{{ clarificationResult.task_type_label }} · {{ clarificationResult.questions.length }} 项待确认</span>
          <strong>{{ clarificationResult.summary }}</strong>
          <p>{{ clarificationOriginalTask }}</p>
        </div>
      </section>
      <div class="clarification-list">
        <article v-for="(question,index) in clarificationResult.questions" :key="question.id" class="clarification-question">
          <header>
            <span>{{ index + 1 }}</span>
            <div><strong>{{ question.question }}</strong><small>{{ question.label }}{{ question.required ? ' · 必填' : ' · 选填' }}</small></div>
            <Languages v-if="question.id.includes('language')" :size="18" />
            <ListChecks v-else :size="18" />
          </header>
          <div v-if="question.type==='single_choice'" class="clarification-options">
            <button
              v-for="option in question.options"
              :key="option.value"
              type="button"
              :class="{active:clarificationAnswers[question.id]===option.value}"
              @click="clarificationAnswers[question.id]=option.value"
            >
              <i><Check v-if="clarificationAnswers[question.id]===option.value" :size="12" /></i>
              <span><strong>{{ option.label }}</strong><small v-if="option.description">{{ option.description }}</small></span>
            </button>
          </div>
          <label v-else-if="question.type==='number'" class="clarification-number">
            <input
              v-model.number="clarificationAnswers[question.id]"
              class="input"
              type="number"
              :min="question.min"
              :max="question.max"
            >
            <span>{{ question.suffix }}</span>
            <small v-if="question.id === 'literature_count'">优先目标；实际不足时仍会继续，并如实披露</small>
            <small v-else>可填写 {{ question.min }}–{{ question.max }}</small>
          </label>
          <textarea
            v-else
            v-model="clarificationAnswers[question.id]"
            class="textarea clarification-text"
            :placeholder="question.placeholder"
          />
        </article>
      </div>
      <p class="clarification-note"><ShieldCheck :size="14" />你的回答会合并到本次任务指令中，并随运行记录保存；不会修改原工作流结构。</p>
    </div>
    <template #footer>
      <button class="btn" :disabled="clarificationSubmitting" @click="closeClarification">取消运行</button>
      <button class="btn btn-primary" :disabled="clarificationSubmitting || !clarificationComplete" @click="confirmClarification">
        <Play :size="14" />{{ clarificationSubmitting ? '正在启动…' : '确认需求并运行' }}
      </button>
    </template>
  </FloatingPanel>
  <ResearchBrowserCenter
    v-model="researchBrowserOpen"
    :visits="researchVisits"
    @verification-completed="markResearchVerification"
  />
  <WorkflowExpertWindow
    :open="expertOpen"
    :session-key="expertSessionKey"
    :definition="buildDefinition()"
    :workflow-name="workflowForm.name"
    :workflow-description="workflowForm.description"
    @close="expertOpen=false"
    @apply="applyExpertProposal"
  />
</template>

<style scoped>
.workflow-clarification{display:grid;gap:18px}.clarification-overview{display:flex;gap:14px;padding:16px;border:1px solid #c9e1f1;border-radius:14px;background:linear-gradient(135deg,#f2f9ff,#f7fbff 55%,#eefaf7)}.clarification-icon{display:grid;place-items:center;width:44px;height:44px;flex:0 0 44px;border-radius:13px;color:#0877bb;background:#fff;box-shadow:0 7px 20px rgba(20,101,153,.13)}.clarification-overview>div:last-child{display:grid;gap:4px;min-width:0}.clarification-overview span{font-size:11px;font-weight:800;letter-spacing:.06em;color:#1682a7}.clarification-overview strong{font-size:14px;color:#173851}.clarification-overview p{margin:3px 0 0;color:#5c7181;font-size:12px;line-height:1.55;white-space:pre-wrap}.clarification-list{display:grid;gap:12px}.clarification-question{padding:15px;border:1px solid #d9e6ef;border-radius:14px;background:#fff;box-shadow:0 5px 18px rgba(18,59,86,.05)}.clarification-question>header{display:grid;grid-template-columns:26px minmax(0,1fr) auto;align-items:start;gap:10px;margin-bottom:12px}.clarification-question>header>span{display:grid;place-items:center;width:24px;height:24px;border-radius:8px;background:#e7f4fd;color:#0877bb;font-size:11px;font-weight:900}.clarification-question>header>div{display:grid;gap:3px}.clarification-question>header strong{color:#18384f;font-size:13px}.clarification-question>header small{color:#7b8f9e;font-size:10px}.clarification-question>header>svg{color:#78a7c4}.clarification-options{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.clarification-options button{display:flex;align-items:flex-start;gap:8px;min-height:64px;padding:10px;border:1px solid #dbe6ed;border-radius:11px;background:#f9fbfc;color:#334f62;text-align:left;cursor:pointer;transition:.16s ease}.clarification-options button:hover{border-color:#8fc9e7;background:#f5fbff}.clarification-options button.active{border-color:#1689c2;background:#eef8ff;box-shadow:0 0 0 2px rgba(22,137,194,.09)}.clarification-options button>i{display:grid;place-items:center;width:16px;height:16px;flex:0 0 16px;margin-top:1px;border:1px solid #b7cad7;border-radius:50%;color:#fff}.clarification-options button.active>i{border-color:#1689c2;background:#1689c2}.clarification-options button>span{display:grid;gap:3px}.clarification-options button strong{font-size:12px}.clarification-options button small{font-size:10px;line-height:1.45;color:#718594}.clarification-number{display:flex;align-items:center;gap:9px}.clarification-number .input{width:180px}.clarification-number>span{color:#426177;font-weight:700}.clarification-number>small{color:#8798a4}.clarification-text{min-height:76px;resize:vertical}.clarification-note{display:flex;align-items:center;gap:7px;margin:0;padding:10px 12px;border-radius:10px;background:#f4faf7;color:#4c7464;font-size:11px}.clarification-note svg{flex:0 0 auto;color:#248461}
@media (max-width:760px){.clarification-options{grid-template-columns:1fr}.clarification-overview{padding:13px}.clarification-question{padding:12px}}
</style>
