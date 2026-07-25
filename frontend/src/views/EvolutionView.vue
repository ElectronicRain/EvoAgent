<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  AlertTriangle, ArrowRight, BarChart3, Beaker, BrainCircuit, Check,
  CheckCircle2, ChevronRight, CircleGauge, Clock3, Eye, FlaskConical,
  GitBranch, History, Layers3, Lightbulb, Pencil, Plus, RefreshCw,
  RotateCcw, Search, ShieldCheck, Sparkles, Target, Trash2, TrendingUp,
  XCircle,
} from 'lucide-vue-next'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import FloatingPanel from '../components/FloatingPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const overview = ref<Entity>({ summary: {}, pipeline: [] })
const proposals = ref<Entity[]>([])
const agents = ref<Entity[]>([])
const cases = ref<Entity[]>([])
const lineages = ref<Entity[]>([])
const activeTab = ref<'workspace' | 'proposals' | 'benchmarks' | 'versions'>('workspace')
const proposalFilter = ref('all')
const searchText = ref('')

const showEvolution = ref(false)
const evolutionStep = ref(1)
const analyzing = ref(false)
const suggestionsAdded = ref(false)
const analysis = ref<Entity | null>(null)
const form = reactive({
  agent_id: '',
  reason: '',
  proposed_prompt: '',
  proposed_tools: null as string[] | null,
  selected_case_ids: [] as string[],
  min_candidate_score: 70,
  min_improvement: 0,
  max_failure_rate: 0.25,
})

const showCase = ref(false)
const editingCaseId = ref('')
const caseForm = reactive({
  name: '',
  discipline: '通用',
  category: 'quality',
  input: '',
  expected_keywords: '',
  requires_citation: false,
  weight: 1,
  enabled: true,
})
const deleteCaseTarget = ref<Entity | null>(null)

const detailProposal = ref<Entity | null>(null)
const showDetail = ref(false)
const showEvaluation = ref(false)
const evaluatingId = ref('')
const evaluationState = reactive<Entity>({
  message: '', completed: 0, total: 0, elapsed: 0, cases: [],
  error: '', stages: [], sources: [], skill: null,
})

const decisionTarget = ref<Entity | null>(null)
const showDecision = ref(false)
const decisionForm = reactive({ approved: true, override_gate: false, note: '' })
const rollbackLineage = ref<Entity | null>(null)
const showRollback = ref(false)
const rollbackForm = reactive({ active_agent_id: '', target_agent_id: '', reason: '恢复到经过验证的历史稳定版本' })

const summary = computed(() => overview.value.summary || {})
const activeAgents = computed(() => agents.value.filter(item => item.status === 'active'))
const selectedAgent = computed(() => agents.value.find(item => item.id === form.agent_id))
const enabledCases = computed(() => cases.value.filter(item => item.enabled))
const parsedReport = (item: Entity | null) => {
  try { return JSON.parse(item?.report_json || '{}') } catch { return {} }
}
const parsedGoal = (item: Entity | null) => {
  try { return JSON.parse(item?.goal_json || '{}') } catch { return {} }
}
const parsedConfig = (item: Entity | null) => {
  try { return JSON.parse(item?.config_json || '{}') } catch { return {} }
}
const gateFor = (item: Entity | null) => parsedReport(item).gate || null
const agentName = (id: string) => agents.value.find(item => item.id === id)?.name || '历史 Agent'
const candidateFor = (item: Entity) => agents.value.find(agent => agent.id === item.candidate_agent_id)
const renderMarkdown = (value: string) => DOMPurify.sanitize(marked.parse(value || '', { async: false }) as string)
const formatDate = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
const percent = (value: number) => `${Math.round((value || 0) * 100)}%`
const breakdownPercent = (key: string, score: number) => {
  const maxima: Record<string, number> = { coverage: 45, evidence: 30, structure: 20, reliability: 15 }
  return Math.min(100, Number(score || 0) / (maxima[key] || 100) * 100)
}

const filteredProposals = computed(() => proposals.value.filter(item => {
  const matchesStatus = proposalFilter.value === 'all' || item.status === proposalFilter.value
  const query = searchText.value.trim().toLowerCase()
  const matchesSearch = !query || `${item.reason} ${agentName(item.source_agent_id)}`.toLowerCase().includes(query)
  return matchesStatus && matchesSearch
}))

async function load(silent = false) {
  if (!silent) store.loading(true)
  try {
    const [overviewResult, proposalResult, agentResult, caseResult, lineageResult] = await Promise.all([
      api.get('/evolution/overview'),
      api.get('/evolution'),
      api.get('/agents'),
      api.get('/evaluation-cases'),
      api.get('/evolution/lineages'),
    ])
    overview.value = overviewResult
    proposals.value = proposalResult
    agents.value = agentResult
    cases.value = caseResult
    lineages.value = lineageResult
    form.agent_id ||= activeAgents.value[0]?.id || ''
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    if (!silent) store.loading(false)
  }
}

function openEvolution() {
  Object.assign(form, {
    agent_id: activeAgents.value[0]?.id || '',
    reason: '',
    proposed_prompt: '',
    proposed_tools: null,
    selected_case_ids: enabledCases.value.map(item => item.id),
    min_candidate_score: 70,
    min_improvement: 0,
    max_failure_rate: 0.25,
  })
  analysis.value = null
  suggestionsAdded.value = false
  evolutionStep.value = 1
  showEvolution.value = true
}

async function analyzeGoal() {
  if (!form.agent_id || form.reason.trim().length < 3) return
  analyzing.value = true
  try {
    analysis.value = await api.post('/evolution/analyze-goal', {
      agent_id: form.agent_id,
      goal: form.reason,
      include_run_insights: true,
    })
    form.proposed_prompt = analysis.value?.recommended_prompt || ''
    evolutionStep.value = 2
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    analyzing.value = false
  }
}

async function addSuggestedCases() {
  const suggestions = analysis.value?.suggested_cases || []
  if (!suggestions.length || suggestionsAdded.value) return
  store.loading(true)
  try {
    for (const suggestion of suggestions) {
      const created = await api.post('/evaluation-cases', suggestion)
      form.selected_case_ids.push(created.id)
    }
    suggestionsAdded.value = true
    store.notify(`已加入 ${suggestions.length} 个目标驱动评测用例`)
    await load(true)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function createProposal() {
  store.loading(true)
  try {
    await api.post('/evolution', {
      ...form,
      goal_analysis: analysis.value || {},
    })
    showEvolution.value = false
    activeTab.value = 'proposals'
    store.notify('候选版本已创建，可开始新旧版本对照评测')
    await load(true)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function openCase(item?: Entity) {
  editingCaseId.value = item?.id || ''
  Object.assign(caseForm, item ? {
    name: item.name,
    discipline: item.discipline,
    category: item.category || 'quality',
    input: item.input_text,
    expected_keywords: JSON.parse(item.expected_keywords_json || '[]').join('，'),
    requires_citation: item.requires_citation,
    weight: item.weight || 1,
    enabled: item.enabled,
  } : {
    name: '',
    discipline: '通用',
    category: 'quality',
    input: '',
    expected_keywords: '',
    requires_citation: false,
    weight: 1,
    enabled: true,
  })
  showCase.value = true
}

async function saveCase() {
  const payload = {
    ...caseForm,
    expected_keywords: caseForm.expected_keywords.split(/[，,\n]/).map(value => value.trim()).filter(Boolean),
  }
  store.loading(true)
  try {
    if (editingCaseId.value) await api.put(`/evaluation-cases/${editingCaseId.value}`, payload)
    else await api.post('/evaluation-cases', payload)
    showCase.value = false
    store.notify(editingCaseId.value ? '评测用例已更新' : '评测用例已创建')
    await load(true)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function deleteCase() {
  if (!deleteCaseTarget.value) return
  store.loading(true)
  try {
    await api.delete(`/evaluation-cases/${deleteCaseTarget.value.id}`)
    deleteCaseTarget.value = null
    store.notify('评测用例已删除')
    await load(true)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function resetEvaluation(item: Entity) {
  evaluatingId.value = item.id
  Object.assign(evaluationState, {
    message: '正在连接评测服务…',
    completed: 0,
    total: parsedConfig(item).selected_case_ids?.length || enabledCases.value.length,
    elapsed: 0,
    cases: [],
    error: '',
    stages: [],
    sources: [],
    skill: null,
  })
  showEvaluation.value = true
}

async function evaluate(item: Entity) {
  resetEvaluation(item)
  try {
    await api.stream(`/evolution/${item.id}/evaluate/stream`, {}, event => {
      if (event.type === 'error') {
        evaluationState.error = event.message || '评测失败'
        evaluationState.message = evaluationState.error
        return
      }
      if (event.type === 'evolution_result') return
      if (event.type !== 'step') return
      const step = event.step || {}
      if (step.type === 'stream_connected') evaluationState.message = '已连接，正在准备基线与候选版本'
      else if (step.type === 'evaluation_started') {
        evaluationState.total = step.total_cases
        evaluationState.message = `开始运行 ${step.total_cases} 个对照用例`
      } else if (step.type === 'evolution_stage_started') {
        evaluationState.stages.push({ stage: step.stage, label: step.label, status: 'running' })
        evaluationState.message = step.label
      } else if (step.type === 'evolution_methods_ready') {
        evaluationState.sources = step.sources || []
        evaluationState.message = `已整理 ${step.count} 条进化方法来源`
      } else if (step.type === 'evolution_prompt_optimized') evaluationState.message = '候选执行协议已生成'
      else if (step.type === 'evolution_skill_packaged') {
        evaluationState.skill = step.skill
        evaluationState.message = `已封装专属 Skill：${step.skill?.name || ''}`
      } else if (step.type === 'evaluation_case_started') evaluationState.message = `用例 ${step.index}/${step.total_cases}：${step.case}`
      else if (step.type === 'evaluation_phase_started') evaluationState.message = `${step.case} · ${step.phase === 'baseline' ? '运行基线版本' : '运行候选版本'}`
      else if (step.type === 'evaluation_case_completed') {
        evaluationState.completed = step.index
        evaluationState.cases.push(step)
        evaluationState.message = `已完成 ${step.index}/${step.total_cases}：候选 ${step.candidate} 分`
      } else if (step.type === 'evaluation_waiting') {
        evaluationState.elapsed = step.elapsed_seconds
        evaluationState.message = `Agent 正在执行，已等待 ${step.elapsed_seconds} 秒`
      } else if (step.type === 'evaluation_completed') {
        evaluationState.message = `评测完成：候选 ${step.candidate_score} / 基线 ${step.baseline_score}`
      }
    })
    if (evaluationState.error) throw new Error(evaluationState.error)
    store.notify('进化评测已完成，发布门禁已生成')
    await load(true)
    const refreshed = proposals.value.find(proposal => proposal.id === item.id)
    if (refreshed) detailProposal.value = refreshed
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    evaluatingId.value = ''
  }
}

function openProposal(item: Entity) {
  detailProposal.value = item
  showDetail.value = true
}

function openDecision(item: Entity, approved: boolean) {
  decisionTarget.value = item
  Object.assign(decisionForm, { approved, override_gate: false, note: '' })
  showDecision.value = true
}

async function submitDecision() {
  if (!decisionTarget.value) return
  store.loading(true)
  try {
    await api.post(`/evolution/${decisionTarget.value.id}/decide`, {
      ...decisionForm,
      decided_by: 'local-user',
    })
    showDecision.value = false
    showDetail.value = false
    store.notify(decisionForm.approved ? '候选版本已发布，旧版本已归档' : '候选版本已拒绝')
    await load(true)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function openRollback(item: Entity) {
  rollbackLineage.value = item
  rollbackForm.active_agent_id = item.active_agent_id
  rollbackForm.target_agent_id = item.versions.find((version: Entity) => version.status === 'archived')?.id || ''
  rollbackForm.reason = '恢复到经过验证的历史稳定版本'
  showRollback.value = true
}

async function submitRollback() {
  if (!rollbackForm.active_agent_id || !rollbackForm.target_agent_id) return
  store.loading(true)
  try {
    await api.post(`/evolution/agents/${rollbackForm.active_agent_id}/rollback`, {
      target_agent_id: rollbackForm.target_agent_id,
      reason: rollbackForm.reason,
      actor: 'local-user',
    })
    showRollback.value = false
    store.notify('版本回滚完成，原激活版本已安全归档')
    await load(true)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

onMounted(load)
</script>

<template>
  <PageHeader
    eyebrow="AGENT EVOLUTION STUDIO"
    title="进化实验室"
    description="从真实运行轨迹中识别问题，生成候选版本，用同一评测集完成对照验证，并通过门禁安全发布或回滚。"
  >
    <button class="btn" @click="load()"><RefreshCw :size="14" />刷新</button>
    <button class="btn btn-primary" @click="openEvolution"><Sparkles :size="15" />开始智能进化</button>
  </PageHeader>

  <section class="evolution-hero">
    <div class="hero-copy">
      <span class="hero-kicker"><BrainCircuit :size="14" /> SELF-IMPROVING, BUT CONTROLLED</span>
      <h2>把“感觉需要优化”变成<br><em>可验证的版本提升</em></h2>
      <p>描述你希望 Agent 改进的目标。系统会结合最近运行中的失败、耗时和工具轨迹，自动提出成功标准、候选提示词与评测建议。</p>
      <button class="hero-action" @click="openEvolution">描述进化目标 <ArrowRight :size="16" /></button>
    </div>
    <div class="hero-orbit" aria-hidden="true">
      <div class="orbit-ring ring-one" />
      <div class="orbit-ring ring-two" />
      <div class="orbit-core"><Sparkles :size="26" /></div>
      <span class="orbit-node node-a">诊断</span>
      <span class="orbit-node node-b">评测</span>
      <span class="orbit-node node-c">门禁</span>
    </div>
    <div class="hero-metrics">
      <div><strong>{{ summary.total || 0 }}</strong><span>进化实验</span></div>
      <div><strong>{{ summary.approved || 0 }}</strong><span>安全发布</span></div>
      <div><strong>{{ (summary.average_improvement || 0) > 0 ? '+' : '' }}{{ summary.average_improvement || 0 }}</strong><span>平均提升</span></div>
      <div><strong>{{ summary.gate_pass_rate || 0 }}%</strong><span>门禁通过率</span></div>
    </div>
  </section>

  <section class="evolution-pipeline">
    <article v-for="(stage, index) in overview.pipeline || []" :key="stage.id">
      <span>{{ index + 1 }}</span>
      <div><strong>{{ stage.label }}</strong><p>{{ stage.description }}</p></div>
      <ChevronRight v-if="index < overview.pipeline.length - 1" :size="16" />
    </article>
  </section>

  <nav class="studio-tabs">
    <button :class="{ active: activeTab === 'workspace' }" @click="activeTab = 'workspace'"><CircleGauge :size="15" />总览</button>
    <button :class="{ active: activeTab === 'proposals' }" @click="activeTab = 'proposals'"><FlaskConical :size="15" />实验队列 <b>{{ proposals.length }}</b></button>
    <button :class="{ active: activeTab === 'benchmarks' }" @click="activeTab = 'benchmarks'"><Beaker :size="15" />评测集 <b>{{ cases.length }}</b></button>
    <button :class="{ active: activeTab === 'versions' }" @click="activeTab = 'versions'"><GitBranch :size="15" />版本谱系 <b>{{ lineages.length }}</b></button>
  </nav>

  <template v-if="activeTab === 'workspace'">
    <div class="workspace-grid">
      <section class="studio-card priority-card">
        <header><div><span>START HERE</span><h3>下一步建议</h3></div><Lightbulb :size="19" /></header>
        <div class="priority-content">
          <div class="priority-icon"><Target :size="24" /></div>
          <div>
            <strong v-if="!proposals.length">创建第一个目标驱动的进化实验</strong>
            <strong v-else-if="summary.evaluating">等待正在运行的对照评测完成</strong>
            <strong v-else-if="proposals.some(item => item.status === 'evaluated')">检查已评测候选的发布门禁</strong>
            <strong v-else>从真实运行问题发起新的进化</strong>
            <p>系统只会创建独立候选，不会直接覆盖生产版本。</p>
          </div>
        </div>
        <button class="btn btn-primary" @click="proposals.some(item => item.status === 'evaluated') ? activeTab = 'proposals' : openEvolution()">
          {{ proposals.some(item => item.status === 'evaluated') ? '查看待决策候选' : '开始智能诊断' }} <ArrowRight :size="14" />
        </button>
      </section>

      <section class="studio-card signal-card">
        <header><div><span>PIPELINE HEALTH</span><h3>实验状态</h3></div><BarChart3 :size="19" /></header>
        <div class="signal-bars">
          <div><span>草稿</span><i><b :style="{ width: `${proposals.length ? (summary.draft || 0) / proposals.length * 100 : 0}%` }" /></i><strong>{{ summary.draft || 0 }}</strong></div>
          <div><span>评测中</span><i><b :style="{ width: `${proposals.length ? (summary.evaluating || 0) / proposals.length * 100 : 0}%` }" /></i><strong>{{ summary.evaluating || 0 }}</strong></div>
          <div><span>已发布</span><i><b class="success" :style="{ width: `${proposals.length ? (summary.approved || 0) / proposals.length * 100 : 0}%` }" /></i><strong>{{ summary.approved || 0 }}</strong></div>
        </div>
      </section>

      <section class="studio-card latest-card">
        <header><div><span>RECENT EXPERIMENTS</span><h3>最近实验</h3></div><History :size="19" /></header>
        <div class="compact-experiments">
          <button v-for="item in proposals.slice(0, 4)" :key="item.id" @click="openProposal(item)">
            <span class="experiment-dot" :class="item.status" />
            <div><strong>{{ item.reason }}</strong><small>{{ agentName(item.source_agent_id) }} · {{ formatDate(item.created_at) }}</small></div>
            <StatusBadge :status="item.status" />
          </button>
          <div v-if="!proposals.length" class="empty compact">暂无实验记录</div>
        </div>
      </section>
    </div>

    <section class="studio-card readiness-card">
      <header><div><span>EVOLUTION READINESS</span><h3>可进化 Agent</h3></div><span>{{ activeAgents.length }} 个生产版本</span></header>
      <div class="agent-readiness-grid">
        <article v-for="agent in activeAgents.slice(0, 6)" :key="agent.id">
          <div class="agent-glyph"><BrainCircuit :size="18" /></div>
          <div><strong>{{ agent.name }}</strong><p>v{{ agent.version }} · {{ agent.description || '等待定义职责' }}</p></div>
          <button @click="form.agent_id = agent.id; openEvolution(); form.agent_id = agent.id">进化</button>
        </article>
      </div>
    </section>
  </template>

  <template v-else-if="activeTab === 'proposals'">
    <section class="queue-toolbar">
      <div class="search-box"><Search :size="14" /><input v-model="searchText" placeholder="搜索目标或 Agent"></div>
      <div class="filter-pills">
        <button v-for="item in [{id:'all',label:'全部'},{id:'draft',label:'草稿'},{id:'evaluating',label:'评测中'},{id:'evaluated',label:'待决策'},{id:'approved',label:'已发布'},{id:'rejected',label:'已拒绝'}]" :key="item.id" :class="{active:proposalFilter===item.id}" @click="proposalFilter=item.id">{{ item.label }}</button>
      </div>
      <button class="btn btn-primary" @click="openEvolution"><Plus :size="14" />新实验</button>
    </section>
    <div class="proposal-grid">
      <article v-for="item in filteredProposals" :key="item.id" class="proposal-card">
        <header>
          <div class="proposal-agent"><span><BrainCircuit :size="16" /></span><div><strong>{{ agentName(item.source_agent_id) }}</strong><small>候选 v{{ candidateFor(item)?.version || '—' }}</small></div></div>
          <StatusBadge :status="evaluatingId === item.id ? 'running' : item.status" />
        </header>
        <div class="proposal-body">
          <span class="proposal-date">{{ formatDate(item.created_at) }}</span>
          <h3>{{ item.reason }}</h3>
          <div v-if="parsedGoal(item).dimensions?.length" class="dimension-tags">
            <span v-for="dimension in parsedGoal(item).dimensions" :key="dimension.id">{{ dimension.label }}</span>
          </div>
          <div class="score-comparison">
            <div><span>基线</span><strong>{{ item.baseline_score.toFixed(1) }}</strong></div>
            <i><ArrowRight :size="14" /></i>
            <div><span>候选</span><strong>{{ item.candidate_score.toFixed(1) }}</strong></div>
            <div class="delta" :class="{ positive: item.candidate_score >= item.baseline_score }"><span>变化</span><strong>{{ item.candidate_score >= item.baseline_score ? '+' : '' }}{{ (item.candidate_score - item.baseline_score).toFixed(1) }}</strong></div>
          </div>
          <div v-if="gateFor(item)" class="gate-summary" :class="{ passed: gateFor(item).passed }">
            <ShieldCheck :size="15" /><span>{{ gateFor(item).passed ? '发布门禁已通过' : '发布门禁未通过' }}</span><small>{{ gateFor(item).checks?.filter((check:Entity) => check.passed).length }}/{{ gateFor(item).checks?.length }}</small>
          </div>
        </div>
        <footer>
          <button class="btn btn-sm" @click="openProposal(item)"><Eye :size="13" />查看详情</button>
          <button v-if="['draft','evaluated'].includes(item.status)" class="btn btn-sm btn-primary" :disabled="!!evaluatingId" @click="evaluate(item)"><Beaker :size="13" />{{ item.status === 'evaluated' ? '重新评测' : '开始评测' }}</button>
          <button v-if="item.status === 'evaluated' && gateFor(item)?.passed" class="btn btn-sm btn-success" @click="openDecision(item, true)"><CheckCircle2 :size="13" />发布</button>
        </footer>
      </article>
      <div v-if="!filteredProposals.length" class="studio-card empty-state"><FlaskConical :size="34" /><strong>没有匹配的进化实验</strong><p>更换筛选条件，或创建一个新的目标驱动实验。</p></div>
    </div>
  </template>

  <template v-else-if="activeTab === 'benchmarks'">
    <section class="benchmark-header studio-card">
      <div><span>BENCHMARK LIBRARY</span><h3>可复用评测集</h3><p>同一用例同时运行基线和候选版本。权重越高，对最终分数影响越大。</p></div>
      <button class="btn btn-primary" @click="openCase()"><Plus :size="14" />添加用例</button>
    </section>
    <div class="benchmark-grid">
      <article v-for="item in cases" :key="item.id" class="benchmark-card" :class="{ disabled: !item.enabled }">
        <header><span class="category-badge">{{ ({quality:'质量',reliability:'可靠性',evidence:'证据',safety:'安全',tool_use:'工具',custom:'自定义'} as any)[item.category] || item.category }}</span><strong>× {{ item.weight }}</strong></header>
        <h3>{{ item.name }}</h3>
        <p>{{ item.input_text }}</p>
        <div class="keyword-row"><span v-for="keyword in JSON.parse(item.expected_keywords_json || '[]')" :key="keyword">{{ keyword }}</span><em v-if="item.requires_citation">要求引用</em></div>
        <footer><small>{{ item.discipline }} · {{ item.enabled ? '已启用' : '已停用' }}</small><div><button @click="openCase(item)"><Pencil :size="13" /></button><button class="danger" @click="deleteCaseTarget = item"><Trash2 :size="13" /></button></div></footer>
      </article>
    </div>
  </template>

  <template v-else>
    <section class="version-intro studio-card">
      <div><GitBranch :size="22" /><div><span>VERSION LINEAGE</span><h3>每次发布都保留安全返回路径</h3><p>激活候选时旧版本会归档，不删除任何配置。需要时可一键恢复历史版本。</p></div></div>
    </section>
    <div class="lineage-list">
      <article v-for="lineage in lineages" :key="lineage.lineage_id" class="lineage-card studio-card">
        <header><div><strong>{{ lineage.name }}</strong><span>{{ lineage.versions.length }} 个版本</span></div><button class="btn btn-sm" :disabled="!lineage.versions.some((item:Entity) => item.status === 'archived')" @click="openRollback(lineage)"><RotateCcw :size="13" />版本回滚</button></header>
        <div class="version-track">
          <div v-for="(version, index) in [...lineage.versions].reverse()" :key="version.id" class="version-node" :class="version.status">
            <span class="version-point"><Check v-if="version.status === 'active'" :size="12" /><History v-else :size="12" /></span>
            <i v-if="index < lineage.versions.length - 1" />
            <div><strong>v{{ version.version }}</strong><StatusBadge :status="version.status" /><p>{{ formatDate(version.created_at) }} · {{ version.slug }}</p></div>
          </div>
        </div>
      </article>
      <div v-if="!lineages.length" class="studio-card empty-state"><GitBranch :size="34" /><strong>尚未形成多版本谱系</strong><p>批准第一个候选版本后，这里会显示完整版本路径。</p></div>
    </div>
  </template>

  <FloatingPanel v-model="showEvolution" title="开始智能进化" eyebrow="GOAL-DRIVEN EVOLUTION" description="系统先理解目标和历史信号，再生成可编辑的候选方案。" size="wide" :close-on-backdrop="!analyzing">
    <div class="wizard-steps"><span :class="{active:evolutionStep>=1}"><b>1</b>描述目标</span><i /><span :class="{active:evolutionStep>=2}"><b>2</b>检查方案</span><i /><span :class="{active:evolutionStep>=3}"><b>3</b>创建候选</span></div>
    <div v-if="evolutionStep === 1" class="goal-step">
      <div class="field"><label>选择生产 Agent</label><select v-model="form.agent_id" class="select"><option v-for="agent in activeAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.version }}</option></select></div>
      <div class="selected-agent-preview"><div class="agent-glyph"><BrainCircuit :size="20" /></div><div><strong>{{ selectedAgent?.name || '请选择 Agent' }}</strong><p>{{ selectedAgent?.description || '选择后系统会读取该 Agent 最近的真实运行轨迹。' }}</p></div></div>
      <div class="field"><label>你希望它发生什么改变？</label><textarea v-model="form.reason" class="textarea goal-input" placeholder="例如：完整理解包含多项要求的问题，回答前建立覆盖清单，避免遗漏后半部分内容；需要事实时必须给出可核验来源。" /></div>
      <div class="goal-examples"><span>可直接描述：</span><button @click="form.reason='提高长任务的完整性，先建立要求清单，逐项完成并在交付前检查遗漏。'">减少遗漏</button><button @click="form.reason='提高学术回答的证据可信度，关键结论必须给出来源并区分事实与推断。'">证据可信</button><button @click="form.reason='增强工具失败后的诊断与恢复能力，不因单个工具失败而停止整个任务。'">错误恢复</button></div>
      <button class="btn btn-primary wizard-next" :disabled="analyzing || !form.agent_id || form.reason.trim().length < 3" @click="analyzeGoal"><Sparkles :size="15" />{{ analyzing ? '正在分析最近运行轨迹…' : '理解目标并生成方案' }}</button>
    </div>
    <div v-else class="analysis-step">
      <section class="analysis-summary"><div><span>目标理解</span><h3>{{ analysis?.summary }}</h3><p>诊断置信度：{{ analysis?.confidence === 'high' ? '高' : analysis?.confidence === 'medium' ? '中' : '初始基线' }}</p></div><button class="btn btn-sm" @click="evolutionStep=1">修改目标</button></section>
      <div class="analysis-grid">
        <section><header><CircleGauge :size="15" /><strong>历史运行信号</strong></header><div class="insight-metrics"><div><strong>{{ analysis?.run_insights?.sample_size || 0 }}</strong><span>分析运行</span></div><div><strong>{{ analysis?.run_insights?.success_rate ?? '—' }}{{ analysis?.run_insights?.success_rate != null ? '%' : '' }}</strong><span>成功率</span></div><div><strong>{{ analysis?.run_insights?.failed_runs || 0 }}</strong><span>失败运行</span></div></div><ul><li v-for="item in analysis?.observations" :key="item">{{ item }}</li></ul></section>
        <section><header><Target :size="15" /><strong>成功标准</strong></header><ol><li v-for="item in analysis?.success_criteria" :key="item">{{ item }}</li></ol><div class="dimension-tags"><span v-for="item in analysis?.dimensions" :key="item.id">{{ item.label }}</span></div></section>
      </div>
      <div class="field"><label>候选系统提示词（可编辑）</label><textarea v-model="form.proposed_prompt" class="textarea prompt-editor" /></div>
      <section class="benchmark-selector">
        <header><div><strong>本轮评测用例</strong><p>至少选择一个；建议把目标驱动用例加入长期评测集。</p></div><button v-if="analysis?.suggested_cases?.length" class="btn btn-sm" :disabled="suggestionsAdded" @click="addSuggestedCases"><Check v-if="suggestionsAdded" :size="13" /><Plus v-else :size="13" />{{ suggestionsAdded ? '建议用例已加入' : `加入 ${analysis.suggested_cases.length} 个建议用例` }}</button></header>
        <div><label v-for="item in enabledCases" :key="item.id"><input v-model="form.selected_case_ids" type="checkbox" :value="item.id"><span><strong>{{ item.name }}</strong><small>{{ item.category }} · 权重 {{ item.weight }}</small></span></label></div>
      </section>
      <section class="gate-config"><header><ShieldCheck :size="15" /><div><strong>发布门禁</strong><p>评测完成后必须同时满足以下条件。</p></div></header><div><label>候选最低分<input v-model.number="form.min_candidate_score" type="number" min="0" max="100" class="input"></label><label>相对基线提升<input v-model.number="form.min_improvement" type="number" min="-100" max="100" class="input"></label><label>最大失败率<input v-model.number="form.max_failure_rate" type="number" min="0" max="1" step="0.05" class="input"></label></div></section>
      <div class="wizard-actions"><button class="btn" @click="evolutionStep=1">返回</button><button class="btn btn-primary" :disabled="!form.selected_case_ids.length || form.proposed_prompt.trim().length < 10" @click="createProposal"><FlaskConical :size="15" />创建独立候选版本</button></div>
    </div>
  </FloatingPanel>

  <FloatingPanel v-model="showCase" :title="editingCaseId ? '编辑评测用例' : '添加评测用例'" eyebrow="BENCHMARK CASE" description="定义输入、期望信号、评分权重和引用要求。" size="large">
    <div class="form-grid">
      <div class="field"><label>用例名称</label><input v-model="caseForm.name" class="input"></div>
      <div class="field"><label>学科/场景</label><input v-model="caseForm.discipline" class="input"></div>
      <div class="field"><label>评测类别</label><select v-model="caseForm.category" class="select"><option value="quality">质量与完整性</option><option value="reliability">可靠性与恢复</option><option value="evidence">证据与引用</option><option value="safety">安全边界</option><option value="tool_use">工具使用</option><option value="custom">自定义</option></select></div>
      <div class="field"><label>评分权重</label><input v-model.number="caseForm.weight" type="number" min=".1" max="10" step=".1" class="input"></div>
      <div class="field full"><label>真实测试输入</label><textarea v-model="caseForm.input" class="textarea" /></div>
      <div class="field full"><label>期望关键词（逗号或换行分隔）</label><input v-model="caseForm.expected_keywords" class="input"></div>
      <label class="switch-row"><input v-model="caseForm.requires_citation" type="checkbox"><span>要求候选回答包含可核验引用或来源</span></label>
      <label class="switch-row"><input v-model="caseForm.enabled" type="checkbox"><span>启用并加入可选评测集</span></label>
    </div>
    <template #footer><button class="btn" @click="showCase=false">取消</button><button class="btn btn-primary" :disabled="!caseForm.name.trim() || !caseForm.input.trim()" @click="saveCase">保存用例</button></template>
  </FloatingPanel>

  <FloatingPanel v-model="showEvaluation" title="进化评测正在运行" eyebrow="LIVE EVALUATION" description="关闭窗口或切换页面不会中止后台评测。" size="large" :close-on-backdrop="true">
    <div class="evaluation-live" :class="{complete:!evaluatingId && !evaluationState.error,error:!!evaluationState.error}">
      <div class="evaluation-orb"><Beaker :size="22" /></div>
      <h3>{{ evaluationState.message }}</h3>
      <p>{{ evaluationState.completed }}/{{ evaluationState.total }} 个用例完成<span v-if="evaluationState.elapsed"> · 已等待 {{ evaluationState.elapsed }} 秒</span></p>
      <div class="evaluation-progress"><i :style="{width:`${evaluationState.total ? Math.max(4, evaluationState.completed / evaluationState.total * 100) : 4}%`}" /></div>
    </div>
    <div class="live-stage-grid"><div v-for="stage in evaluationState.stages" :key="stage.stage"><CheckCircle2 :size="14" /><span>{{ stage.label }}</span></div><div v-if="evaluationState.skill"><CheckCircle2 :size="14" /><span>Skill：{{ evaluationState.skill.name }}</span></div><div v-if="evaluationState.sources.length"><CheckCircle2 :size="14" /><span>{{ evaluationState.sources.length }} 条方法来源</span></div></div>
    <div v-if="evaluationState.cases.length" class="live-case-list"><div v-for="item in evaluationState.cases" :key="item.case"><span>{{ item.case }}</span><strong>{{ item.baseline }} → {{ item.candidate }}</strong><em :class="{positive:item.delta>=0}">{{ item.delta>=0?'+':'' }}{{ item.delta }}</em></div></div>
    <template #footer><button class="btn" @click="showEvaluation=false">{{ evaluatingId ? '收起到后台' : '关闭' }}</button><button v-if="!evaluatingId && detailProposal" class="btn btn-primary" @click="showEvaluation=false;showDetail=true">查看完整报告</button></template>
  </FloatingPanel>

  <FloatingPanel v-model="showDetail" title="进化实验报告" eyebrow="EVOLUTION REPORT" description="查看目标理解、多维评分、发布门禁与候选产物。" size="wide">
    <template v-if="detailProposal">
      <section class="report-head"><div><span>{{ agentName(detailProposal.source_agent_id) }} · 候选 v{{ candidateFor(detailProposal)?.version }}</span><h3>{{ detailProposal.reason }}</h3><div class="dimension-tags"><span v-for="item in parsedGoal(detailProposal).dimensions || []" :key="item.id">{{ item.label }}</span></div></div><StatusBadge :status="detailProposal.status" /></section>
      <div class="report-score-grid"><div><span>基线得分</span><strong>{{ detailProposal.baseline_score.toFixed(1) }}</strong></div><div><span>候选得分</span><strong>{{ detailProposal.candidate_score.toFixed(1) }}</strong></div><div :class="{positive:detailProposal.candidate_score>=detailProposal.baseline_score}"><span>净提升</span><strong>{{ detailProposal.candidate_score>=detailProposal.baseline_score?'+':'' }}{{ (detailProposal.candidate_score-detailProposal.baseline_score).toFixed(1) }}</strong></div><div><span>评测用例</span><strong>{{ parsedReport(detailProposal).count || 0 }}</strong></div></div>
      <section v-if="gateFor(detailProposal)" class="gate-report" :class="{passed:gateFor(detailProposal).passed}"><header><ShieldCheck :size="20" /><div><strong>{{ gateFor(detailProposal).passed ? '发布门禁通过' : '发布门禁未通过' }}</strong><p>{{ gateFor(detailProposal).recommendation }}</p></div></header><div><article v-for="check in gateFor(detailProposal).checks" :key="check.id" :class="{passed:check.passed}"><CheckCircle2 v-if="check.passed" :size="15" /><XCircle v-else :size="15" /><span>{{ check.label }}</span><small>实际 {{ check.id==='failure_rate'?percent(check.actual):check.actual }} / 门槛 {{ check.id==='failure_rate'?percent(check.target):check.target }}</small></article></div></section>
      <section v-if="parsedReport(detailProposal).cases?.length" class="report-section"><header><h3>逐用例对照</h3><span>点击分数可查看四维明细</span></header><div class="case-report-list"><details v-for="item in parsedReport(detailProposal).cases" :key="item.case"><summary><span><b>{{ item.case }}</b><small>{{ item.category }} · 权重 {{ item.weight }}</small></span><strong>{{ item.baseline }} <ArrowRight :size="12" /> {{ item.candidate }}</strong><em :class="{positive:item.delta>=0}">{{ item.delta>=0?'+':'' }}{{ item.delta }}</em></summary><div class="breakdown-grid"><div v-for="(score,key) in item.candidate_breakdown" :key="key"><span>{{ ({coverage:'覆盖度',evidence:'证据',structure:'结构',reliability:'可靠性'} as any)[key] }}</span><i><b :style="{width:`${breakdownPercent(String(key), Number(score))}%`}" /></i><strong>{{ score }}</strong></div></div><div class="answer-compare"><div><strong>基线输出</strong><p>{{ item.baseline_excerpt }}</p></div><div><strong>候选输出</strong><p>{{ item.candidate_excerpt }}</p></div></div></details></div></section>
      <section v-if="parsedReport(detailProposal).optimized_prompt" class="report-section"><header><h3>候选改动</h3><span>提示词与 Skill 均随候选版本独立保存</span></header><details><summary>查看系统提示词前后对比</summary><div class="prompt-diff"><div><strong>优化前</strong><pre>{{ parsedReport(detailProposal).original_prompt }}</pre></div><div><strong>优化后</strong><pre>{{ parsedReport(detailProposal).optimized_prompt }}</pre></div></div></details><details v-if="parsedReport(detailProposal).skill"><summary>查看生成的专属 Skill</summary><pre class="skill-preview">{{ parsedReport(detailProposal).skill.instructions }}</pre></details><details v-if="parsedReport(detailProposal).artifact"><summary>查看 Markdown 进化成果</summary><div class="markdown-report" v-html="renderMarkdown(parsedReport(detailProposal).artifact.content)" /></details></section>
    </template>
    <template #footer>
      <button class="btn" @click="showDetail=false">关闭</button>
      <button v-if="detailProposal?.status==='draft'||detailProposal?.status==='evaluated'" class="btn" :disabled="!!evaluatingId" @click="evaluate(detailProposal!)"><Beaker :size="13" />{{ detailProposal?.status==='evaluated'?'重新评测':'开始评测' }}</button>
      <button v-if="detailProposal?.status==='evaluated'" class="btn btn-danger" @click="openDecision(detailProposal!,false)"><XCircle :size="13" />拒绝</button>
      <button v-if="detailProposal?.status==='evaluated'" class="btn btn-primary" @click="openDecision(detailProposal!,true)"><CheckCircle2 :size="13" />批准发布</button>
    </template>
  </FloatingPanel>

  <FloatingPanel v-model="showDecision" :title="decisionForm.approved ? '确认发布候选版本' : '拒绝候选版本'" eyebrow="RELEASE DECISION" :description="decisionForm.approved ? '旧版本将归档并保留回滚能力。' : '候选版本将标记为拒绝，评测报告仍会保留。'" size="small">
    <div v-if="decisionTarget && decisionForm.approved && gateFor(decisionTarget) && !gateFor(decisionTarget).passed" class="decision-warning"><AlertTriangle :size="20" /><div><strong>该候选未通过发布门禁</strong><p>建议继续优化。若仍需发布，必须明确开启覆盖门禁并填写原因。</p></div></div>
    <div class="field"><label>决策备注</label><textarea v-model="decisionForm.note" class="textarea" placeholder="记录发布依据、风险或拒绝原因" /></div>
    <label v-if="decisionTarget && decisionForm.approved && gateFor(decisionTarget) && !gateFor(decisionTarget).passed" class="switch-row override-row"><input v-model="decisionForm.override_gate" type="checkbox"><span>我已了解风险，明确覆盖发布门禁</span></label>
    <template #footer><button class="btn" @click="showDecision=false">取消</button><button class="btn" :class="decisionForm.approved?'btn-primary':'btn-danger'" :disabled="decisionForm.approved && gateFor(decisionTarget) && !gateFor(decisionTarget)?.passed && (!decisionForm.override_gate || !decisionForm.note.trim())" @click="submitDecision">{{ decisionForm.approved ? '发布并激活' : '确认拒绝' }}</button></template>
  </FloatingPanel>

  <FloatingPanel v-model="showRollback" title="回滚 Agent 版本" eyebrow="SAFE ROLLBACK" description="选择同一谱系中的历史版本。当前版本不会被删除，只会安全归档。" size="small">
    <div v-if="rollbackLineage" class="rollback-versions"><label v-for="version in rollbackLineage.versions.filter((item:Entity)=>item.status==='archived')" :key="version.id" :class="{active:rollbackForm.target_agent_id===version.id}"><input v-model="rollbackForm.target_agent_id" type="radio" :value="version.id"><span><strong>v{{ version.version }}</strong><small>{{ version.slug }} · {{ formatDate(version.created_at) }}</small></span></label></div>
    <div class="field"><label>回滚原因</label><textarea v-model="rollbackForm.reason" class="textarea" /></div>
    <template #footer><button class="btn" @click="showRollback=false">取消</button><button class="btn btn-primary" :disabled="!rollbackForm.target_agent_id || rollbackForm.reason.trim().length<2" @click="submitRollback"><RotateCcw :size="13" />确认回滚</button></template>
  </FloatingPanel>

  <FloatingPanel :model-value="!!deleteCaseTarget" title="删除评测用例" eyebrow="DELETE BENCHMARK" description="删除后，新实验不能再选择该用例。" size="small" @update:model-value="value => { if (!value) deleteCaseTarget = null }">
    <div class="decision-warning"><Trash2 :size="20" /><div><strong>{{ deleteCaseTarget?.name }}</strong><p>历史进化报告不会受影响。此操作会删除当前评测集中的定义。</p></div></div>
    <template #footer><button class="btn" @click="deleteCaseTarget=null">取消</button><button class="btn btn-danger" @click="deleteCase">确认删除</button></template>
  </FloatingPanel>
</template>

<style scoped>
.evolution-hero{position:relative;min-height:315px;padding:42px 46px;overflow:hidden;border:1px solid #185f94;border-radius:20px;color:#fff;background:radial-gradient(circle at 72% 42%,rgba(33,171,207,.3),transparent 28%),linear-gradient(125deg,#072d55 0%,#0a4f7c 58%,#087f98 100%);box-shadow:0 18px 46px rgba(7,46,82,.2)}
.hero-copy{position:relative;z-index:2;max-width:680px}.hero-kicker{display:flex;align-items:center;gap:8px;color:#8dd9ed;font-size:9px;font-weight:800;letter-spacing:.16em}.hero-copy h2{margin:18px 0 12px;font-size:34px;line-height:1.22;letter-spacing:-.035em}.hero-copy h2 em{color:#85e1e4;font-style:normal}.hero-copy p{max-width:610px;margin:0;color:#c5e0eb;font-size:12px;line-height:1.8}.hero-action{margin-top:24px;padding:0;border:0;display:flex;align-items:center;gap:9px;color:#fff;background:transparent;font-size:12px;font-weight:750}.hero-action:hover{gap:14px}.hero-orbit{position:absolute;right:80px;top:25px;width:270px;height:270px}.orbit-ring{position:absolute;inset:25px;border:1px solid rgba(158,231,243,.24);border-radius:50%;animation:orbit-spin 16s linear infinite}.ring-two{inset:68px;border-style:dashed;animation-direction:reverse;animation-duration:11s}.orbit-core{position:absolute;left:105px;top:105px;width:60px;height:60px;display:grid;place-items:center;border:1px solid rgba(255,255,255,.46);border-radius:18px;color:#b5f0ed;background:rgba(12,65,95,.78);box-shadow:0 0 45px rgba(78,217,224,.3)}.orbit-node{position:absolute;padding:5px 9px;border:1px solid rgba(172,226,235,.27);border-radius:99px;color:#c7ebef;background:rgba(5,43,72,.7);font-size:9px}.node-a{left:8px;top:72px}.node-b{right:1px;top:108px}.node-c{left:47px;bottom:25px}@keyframes orbit-spin{to{transform:rotate(360deg)}}.hero-metrics{position:absolute;z-index:3;right:30px;bottom:28px;display:flex;gap:1px;overflow:hidden;border:1px solid rgba(180,231,239,.22);border-radius:10px;background:rgba(3,36,63,.55);backdrop-filter:blur(8px)}.hero-metrics div{min-width:105px;padding:12px 15px;border-right:1px solid rgba(180,231,239,.16)}.hero-metrics div:last-child{border:0}.hero-metrics strong,.hero-metrics span{display:block}.hero-metrics strong{font-size:18px}.hero-metrics span{margin-top:4px;color:#9ecbd8;font-size:8px}
.evolution-pipeline{margin:18px 0;display:grid;grid-template-columns:repeat(5,1fr);overflow:hidden;border:1px solid #cfdeea;border-radius:13px;background:#fff;box-shadow:0 5px 20px rgba(20,56,91,.05)}.evolution-pipeline article{position:relative;min-width:0;padding:16px 14px;display:flex;align-items:center;gap:10px;border-right:1px solid #e3ebf2}.evolution-pipeline article:last-child{border:0}.evolution-pipeline article>span{width:26px;height:26px;flex:0 0 26px;display:grid;place-items:center;border-radius:8px;color:#1769c2;background:#e8f3fd;font-size:10px;font-weight:800}.evolution-pipeline strong,.evolution-pipeline p{display:block}.evolution-pipeline strong{color:#234968;font-size:10px}.evolution-pipeline p{margin:3px 0 0;color:#8092a3;font-size:8px;line-height:1.45}.evolution-pipeline svg{position:absolute;right:-9px;z-index:2;color:#a8bdce;background:#fff}
.studio-tabs{margin:24px 0 18px;padding:5px;display:flex;gap:4px;border:1px solid #d3e0eb;border-radius:11px;background:#e7eef5}.studio-tabs button{min-height:38px;padding:0 15px;border:0;border-radius:7px;display:flex;align-items:center;gap:7px;color:#5d758b;background:transparent;font-size:11px}.studio-tabs button.active{color:#165f9f;background:#fff;font-weight:750;box-shadow:0 3px 10px rgba(24,68,105,.09)}.studio-tabs b{padding:2px 6px;border-radius:99px;color:#597791;background:#d9e7f2;font-size:8px}.studio-tabs button.active b{color:#1769c2;background:#e4f2fd}
.workspace-grid{display:grid;grid-template-columns:1.1fr .9fr 1.3fr;gap:16px}.studio-card{border:1px solid #d6e2ec;border-radius:13px;background:#fff;box-shadow:0 5px 20px rgba(18,56,88,.055)}.studio-card>header{padding:16px 18px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e7eef4;color:#5d7b94}.studio-card>header span{color:#7e95a8;font-size:8px;font-weight:800;letter-spacing:.11em}.studio-card>header h3{margin:4px 0 0;color:#1c4265;font-size:14px}.priority-card{padding-bottom:16px}.priority-card>header{border:0}.priority-content{margin:2px 18px 18px;padding:14px;display:flex;gap:12px;border-radius:10px;background:linear-gradient(135deg,#eef7ff,#f4fbfc)}.priority-icon{width:42px;height:42px;flex:0 0 42px;display:grid;place-items:center;border-radius:10px;color:#1769c2;background:#fff;box-shadow:0 3px 10px rgba(25,91,145,.09)}.priority-content strong{color:#214966;font-size:11px}.priority-content p{margin:5px 0 0;color:#748b9f;font-size:9px;line-height:1.5}.priority-card>.btn{margin-left:18px}.signal-bars{padding:18px;display:grid;gap:15px}.signal-bars>div{display:grid;grid-template-columns:50px 1fr 22px;align-items:center;gap:9px}.signal-bars span,.signal-bars strong{font-size:9px}.signal-bars span{color:#698197}.signal-bars strong{color:#2b4f6c}.signal-bars i{height:6px;overflow:hidden;border-radius:99px;background:#e9eff4}.signal-bars b{height:100%;display:block;border-radius:inherit;background:#e9a344}.signal-bars b.success{background:#2ba274}.compact-experiments{padding:10px}.compact-experiments button{width:100%;padding:9px 8px;border:0;border-radius:8px;display:grid;grid-template-columns:8px minmax(0,1fr) auto;align-items:center;gap:9px;text-align:left;background:transparent}.compact-experiments button:hover{background:#f2f7fb}.experiment-dot{width:7px;height:7px;border-radius:50%;background:#9badbd}.experiment-dot.evaluated{background:#e4a124}.experiment-dot.approved{background:#20a270}.experiment-dot.evaluating{background:#2c83cc;box-shadow:0 0 0 4px #e3f1fc}.compact-experiments strong,.compact-experiments small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.compact-experiments strong{color:#294c68;font-size:10px}.compact-experiments small{margin-top:4px;color:#8698a8;font-size:8px}.readiness-card{margin-top:16px}.readiness-card>header>span{letter-spacing:0}.agent-readiness-grid{padding:15px;display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.agent-readiness-grid article{min-width:0;padding:12px;display:grid;grid-template-columns:36px minmax(0,1fr) auto;align-items:center;gap:10px;border:1px solid #e0e9f1;border-radius:9px}.agent-glyph{width:36px;height:36px;display:grid;place-items:center;border-radius:9px;color:#1971b8;background:#e9f4fd}.agent-readiness-grid strong,.agent-readiness-grid p{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.agent-readiness-grid strong{color:#234762;font-size:10px}.agent-readiness-grid p{margin:4px 0 0;color:#8394a3;font-size:8px}.agent-readiness-grid button{padding:5px 8px;border:1px solid #bdd7ea;border-radius:6px;color:#1769c2;background:#f5faff;font-size:8px}
.queue-toolbar{margin-bottom:14px;padding:10px;border:1px solid #d5e1eb;border-radius:11px;display:flex;align-items:center;gap:10px;background:#fff}.search-box{width:230px;height:34px;padding:0 10px;display:flex;align-items:center;gap:7px;border:1px solid #d0deea;border-radius:7px;color:#7890a4}.search-box input{width:100%;border:0;outline:0;color:#2e506c;background:transparent;font-size:10px}.filter-pills{display:flex;gap:4px;flex:1}.filter-pills button{padding:6px 9px;border:0;border-radius:6px;color:#71869a;background:transparent;font-size:9px}.filter-pills button.active{color:#1769c2;background:#e9f4fd;font-weight:700}.proposal-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.proposal-card{overflow:hidden;border:1px solid #d6e2ec;border-radius:13px;background:#fff;box-shadow:0 5px 18px rgba(17,55,87,.05);transition:.18s}.proposal-card:hover{transform:translateY(-2px);border-color:#a9cbe5;box-shadow:0 10px 28px rgba(17,72,114,.1)}.proposal-card>header{padding:13px 15px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e8eef4}.proposal-agent{display:flex;align-items:center;gap:9px}.proposal-agent>span{width:31px;height:31px;display:grid;place-items:center;border-radius:8px;color:#1769c2;background:#e9f4fd}.proposal-agent strong,.proposal-agent small{display:block}.proposal-agent strong{color:#234762;font-size:10px}.proposal-agent small{margin-top:3px;color:#8294a4;font-size:8px}.proposal-body{padding:15px}.proposal-date{color:#8b9ba9;font-size:8px}.proposal-body h3{height:38px;margin:7px 0 9px;overflow:hidden;color:#1e4261;font-size:12px;line-height:1.55}.dimension-tags{display:flex;flex-wrap:wrap;gap:4px}.dimension-tags span{padding:3px 6px;border-radius:4px;color:#346c97;background:#edf5fb;font-size:8px}.score-comparison{margin-top:14px;padding:11px;display:flex;align-items:center;gap:10px;border-radius:9px;background:#f6f9fc}.score-comparison div{flex:1}.score-comparison span,.score-comparison strong{display:block}.score-comparison span{color:#8495a4;font-size:8px}.score-comparison strong{margin-top:3px;color:#244c6b;font-size:17px}.score-comparison i{color:#92a8b9}.score-comparison .delta strong{color:#b64a4a}.score-comparison .delta.positive strong{color:#17805b}.gate-summary{margin-top:10px;padding:8px 10px;display:flex;align-items:center;gap:7px;border-radius:7px;color:#a46718;background:#fff4dc;font-size:9px}.gate-summary.passed{color:#147551;background:#e6f6ee}.gate-summary small{margin-left:auto}.proposal-card>footer{padding:11px 15px;display:flex;gap:6px;border-top:1px solid #e8eef4;background:#fbfdfe}.btn-success{color:#fff;border-color:#23865f;background:#23865f}.empty-state{grid-column:1/-1;padding:60px;display:grid;place-items:center;color:#9ab0c2}.empty-state strong{margin-top:10px;color:#486984;font-size:12px}.empty-state p{margin:6px 0 0;color:#8195a7;font-size:9px}
.benchmark-header{padding:20px;display:flex;align-items:center;justify-content:space-between}.benchmark-header span,.version-intro span{color:#3481b9;font-size:8px;font-weight:800;letter-spacing:.12em}.benchmark-header h3,.version-intro h3{margin:5px 0;color:#1d4363;font-size:16px}.benchmark-header p,.version-intro p{margin:4px 0 0;color:#71889c;font-size:10px}.benchmark-grid{margin-top:15px;display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.benchmark-card{padding:16px;border:1px solid #d7e3ed;border-radius:12px;background:#fff;box-shadow:0 4px 15px rgba(19,58,91,.045)}.benchmark-card.disabled{opacity:.58}.benchmark-card>header{display:flex;align-items:center;justify-content:space-between}.category-badge{padding:4px 7px;border-radius:5px;color:#246b9e;background:#e8f4fc;font-size:8px}.benchmark-card>header strong{color:#647e93;font-size:9px}.benchmark-card h3{margin:13px 0 6px;color:#204563;font-size:12px}.benchmark-card>p{height:48px;margin:0;overflow:hidden;color:#687f93;font-size:9px;line-height:1.7}.keyword-row{min-height:28px;margin-top:10px;display:flex;flex-wrap:wrap;gap:4px}.keyword-row span,.keyword-row em{padding:3px 6px;border-radius:4px;color:#57758e;background:#f0f5f8;font-size:8px;font-style:normal}.keyword-row em{color:#8d6118;background:#fff3dc}.benchmark-card>footer{margin-top:12px;padding-top:10px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid #ebf0f4}.benchmark-card small{color:#8999a7;font-size:8px}.benchmark-card>footer div{display:flex;gap:4px}.benchmark-card>footer button{width:27px;height:27px;padding:0;border:1px solid #d3e0ea;border-radius:6px;display:grid;place-items:center;color:#58748b;background:#fff}.benchmark-card>footer button.danger{color:#b54a4a}
.version-intro{padding:20px}.version-intro>div{display:flex;align-items:center;gap:13px;color:#2677b3}.lineage-list{margin-top:15px;display:grid;gap:14px}.lineage-card>header>div strong,.lineage-card>header>div span{display:block}.lineage-card>header>div strong{color:#204561;font-size:12px}.lineage-card>header>div span{margin-top:4px;letter-spacing:0}.version-track{padding:25px 30px;display:flex;align-items:flex-start;overflow:auto}.version-node{position:relative;min-width:175px;display:flex;gap:10px}.version-point{position:relative;z-index:2;width:27px;height:27px;flex:0 0 27px;display:grid;place-items:center;border:2px solid #9eb6c9;border-radius:50%;color:#6d879c;background:#fff}.version-node.active .version-point{border-color:#1d9c6b;color:#fff;background:#1d9c6b;box-shadow:0 0 0 5px #dff4eb}.version-node>i{position:absolute;left:27px;top:13px;width:148px;height:2px;background:#d5e1e9}.version-node>div strong{display:inline-block;margin-right:6px;color:#234864;font-size:11px}.version-node>div p{margin:6px 0 0;color:#8496a5;font-size:8px}
.wizard-steps{margin-bottom:22px;display:flex;align-items:center;justify-content:center}.wizard-steps span{display:flex;align-items:center;gap:7px;color:#8a9cab;font-size:9px;font-weight:700}.wizard-steps b{width:25px;height:25px;display:grid;place-items:center;border-radius:50%;color:#768ea2;background:#e9eff4}.wizard-steps span.active{color:#1769c2}.wizard-steps span.active b{color:#fff;background:#1769c2}.wizard-steps i{width:90px;height:1px;margin:0 9px;background:#d4e0e9}.goal-step{max-width:760px;margin:auto;display:grid;gap:15px}.selected-agent-preview{padding:12px;display:flex;align-items:center;gap:11px;border:1px solid #d9e6f0;border-radius:9px;background:#f8fbfd}.selected-agent-preview strong{color:#264c69;font-size:11px}.selected-agent-preview p{margin:4px 0 0;color:#7e91a1;font-size:9px}.goal-input{min-height:145px;font-size:13px}.goal-examples{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.goal-examples span{color:#7f92a3;font-size:9px}.goal-examples button{padding:5px 8px;border:1px solid #cee0ee;border-radius:6px;color:#39729d;background:#f5faff;font-size:8px}.wizard-next{min-height:42px}.analysis-summary{margin-bottom:15px;padding:14px 16px;display:flex;align-items:flex-start;justify-content:space-between;gap:14px;border:1px solid #cce2f2;border-radius:10px;background:linear-gradient(135deg,#edf7ff,#f7fbfe)}.analysis-summary>div{min-width:0;flex:1}.analysis-summary>.btn{flex:0 0 auto;white-space:nowrap}.analysis-summary span{color:#3480b8;font-size:8px;font-weight:800;letter-spacing:.1em}.analysis-summary h3{margin:5px 0;color:#204b6c;font-size:13px}.analysis-summary p{margin:4px 0 0;color:#7890a2;font-size:9px}.analysis-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.analysis-grid>section{padding:14px;border:1px solid #dce6ee;border-radius:9px}.analysis-grid header,.gate-config>header{display:flex;align-items:center;gap:7px;color:#28658f}.analysis-grid header strong,.gate-config strong{font-size:10px}.insight-metrics{margin:12px 0;display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.insight-metrics div{padding:8px;border-radius:7px;background:#f2f7fa}.insight-metrics strong,.insight-metrics span{display:block}.insight-metrics strong{color:#234d6c;font-size:15px}.insight-metrics span{margin-top:3px;color:#8194a5;font-size:7px}.analysis-grid ul,.analysis-grid ol{margin:10px 0 0;padding-left:17px;color:#647c90;font-size:9px;line-height:1.7}.prompt-editor{min-height:235px;font:10px/1.65 Consolas,"Microsoft YaHei",sans-serif}.analysis-step>.field{margin-top:14px}.benchmark-selector,.gate-config{margin-top:14px;padding:14px;border:1px solid #d9e5ed;border-radius:9px}.benchmark-selector>header{display:flex;align-items:center;justify-content:space-between;gap:12px}.benchmark-selector>header>.btn{flex:0 0 auto;white-space:nowrap}.benchmark-selector header strong,.benchmark-selector header p{display:block}.benchmark-selector header strong{color:#264d69;font-size:10px}.benchmark-selector header p,.gate-config p{margin:4px 0 0;color:#8193a2;font-size:8px}.benchmark-selector>div{margin-top:10px;display:grid;grid-template-columns:repeat(2,1fr);gap:6px;max-height:160px;overflow:auto}.benchmark-selector label{padding:8px;display:flex;align-items:center;gap:8px;border:1px solid #e0e8ee;border-radius:7px}.benchmark-selector label span,.benchmark-selector label strong,.benchmark-selector label small{display:block}.benchmark-selector label strong{color:#31536d;font-size:9px}.benchmark-selector label small{margin-top:3px;color:#8999a6;font-size:7px}.gate-config>div{margin-top:11px;display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.gate-config label{color:#617a8e;font-size:8px}.gate-config .input{margin-top:5px}.wizard-actions{margin-top:18px;display:flex;justify-content:flex-end;gap:8px}
.switch-row{grid-column:1/-1;display:flex!important;grid-auto-flow:column;justify-content:start;align-items:center;gap:8px;color:#46627a;font-size:10px}.evaluation-live{padding:25px;display:grid;place-items:center;text-align:center;border-radius:12px;background:linear-gradient(135deg,#eff8ff,#f5fbfc)}.evaluation-orb{width:54px;height:54px;display:grid;place-items:center;border-radius:16px;color:#fff;background:linear-gradient(135deg,#1769c2,#1aa1ac);box-shadow:0 10px 25px rgba(23,105,194,.23);animation:evaluation-pulse 1.8s ease-in-out infinite}.evaluation-live.complete .evaluation-orb{background:#1d9568;animation:none}.evaluation-live.error .evaluation-orb{background:#bd4c4c;animation:none}@keyframes evaluation-pulse{50%{transform:scale(1.06);box-shadow:0 14px 35px rgba(23,105,194,.34)}}.evaluation-live h3{margin:13px 0 5px;color:#214a68;font-size:13px}.evaluation-live p{margin:0;color:#778fa2;font-size:9px}.evaluation-progress{width:min(520px,90%);height:7px;margin-top:15px;overflow:hidden;border-radius:99px;background:#dce9f2}.evaluation-progress i{height:100%;display:block;border-radius:inherit;background:linear-gradient(90deg,#1769c2,#22aab2);transition:width .25s}.live-stage-grid{margin:14px 0;display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.live-stage-grid div{padding:9px;display:flex;align-items:center;gap:7px;border:1px solid #dce7ee;border-radius:7px;color:#397055;background:#fbfefd;font-size:8px}.live-case-list{display:grid;gap:6px}.live-case-list div{padding:9px 11px;display:grid;grid-template-columns:1fr auto 50px;gap:9px;border:1px solid #e0e8ee;border-radius:7px;font-size:9px}.live-case-list span{color:#4e6b82}.live-case-list strong{color:#284e6c}.live-case-list em{color:#b94e4e;font-style:normal;text-align:right}.live-case-list em.positive{color:#187957}
.report-head{padding:15px;display:flex;align-items:flex-start;justify-content:space-between;border:1px solid #d8e5ee;border-radius:10px}.report-head>div>span{color:#6d879c;font-size:8px}.report-head h3{margin:6px 0 9px;color:#1e4665;font-size:14px}.report-score-grid{margin:12px 0;display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.report-score-grid>div{padding:12px;border:1px solid #dce6ed;border-radius:8px;background:#fafcfd}.report-score-grid span,.report-score-grid strong{display:block}.report-score-grid span{color:#8294a3;font-size:8px}.report-score-grid strong{margin-top:4px;color:#244e6d;font-size:21px}.report-score-grid .positive strong{color:#17805b}.gate-report{padding:14px;border:1px solid #f0d5a4;border-radius:10px;background:#fff9ed}.gate-report.passed{border-color:#b9e0cf;background:#f0fbf6}.gate-report>header{display:flex;align-items:center;gap:10px;color:#a36a1e}.gate-report.passed>header{color:#167652}.gate-report header strong,.gate-report header p{display:block}.gate-report header strong{font-size:11px}.gate-report header p{margin:3px 0 0;color:#7c8c98;font-size:8px}.gate-report>div{margin-top:11px;display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.gate-report article{padding:8px;display:grid;grid-template-columns:16px 1fr;gap:5px;color:#a34f48;background:#fff;border-radius:7px;font-size:8px}.gate-report article.passed{color:#177454}.gate-report article small{grid-column:2;color:#8192a0}.report-section{margin-top:13px;border:1px solid #dce6ee;border-radius:10px;overflow:hidden}.report-section>header{padding:12px 14px;display:flex;align-items:center;justify-content:space-between;background:#f6f9fb}.report-section>header h3{margin:0;color:#284c68;font-size:11px}.report-section>header span{color:#8194a3;font-size:8px}.case-report-list{padding:10px}.case-report-list details,.report-section>details{border-bottom:1px solid #e7edf2}.case-report-list details:last-child,.report-section>details:last-child{border:0}.case-report-list summary,.report-section>details>summary{padding:10px;display:flex;align-items:center;gap:10px;cursor:pointer;list-style:none}.case-report-list summary>span{flex:1}.case-report-list summary b,.case-report-list summary small{display:block}.case-report-list summary b{color:#2a4d67;font-size:9px}.case-report-list summary small{margin-top:3px;color:#8798a6;font-size:7px}.case-report-list summary>strong{display:flex;align-items:center;gap:5px;color:#2b5270;font-size:9px}.case-report-list summary>em{width:45px;color:#b64e4e;font-size:9px;font-style:normal;text-align:right}.case-report-list summary>em.positive{color:#187957}.breakdown-grid{padding:10px;display:grid;grid-template-columns:repeat(2,1fr);gap:8px;background:#f8fafc}.breakdown-grid div{display:grid;grid-template-columns:50px 1fr 32px;align-items:center;gap:7px}.breakdown-grid span,.breakdown-grid strong{font-size:8px}.breakdown-grid span{color:#698095}.breakdown-grid i{height:5px;border-radius:99px;background:#e2eaf0}.breakdown-grid b{height:100%;display:block;border-radius:inherit;background:#2584ca}.answer-compare,.prompt-diff{padding:10px;display:grid;grid-template-columns:1fr 1fr;gap:8px}.answer-compare>div,.prompt-diff>div{min-width:0;padding:10px;border:1px solid #e0e8ee;border-radius:7px}.answer-compare strong,.prompt-diff strong{color:#31536d;font-size:8px}.answer-compare p{max-height:160px;overflow:auto;color:#61788b;font-size:8px;line-height:1.6;white-space:pre-wrap}.prompt-diff pre,.skill-preview{max-height:320px;margin:8px 0 0;overflow:auto;color:#3c566d;font:8px/1.65 Consolas,monospace;white-space:pre-wrap}.report-section>details{margin:0 10px}.report-section>details>summary{color:#31536d;font-size:9px;font-weight:700}.skill-preview{padding:12px;background:#f7f9fb}.markdown-report{padding:18px;max-height:600px;overflow:auto;font-size:10px;line-height:1.7}.decision-warning{padding:13px;display:flex;gap:10px;border:1px solid #efcf9d;border-radius:9px;color:#9f661c;background:#fff8e9}.decision-warning strong{font-size:10px}.decision-warning p{margin:4px 0 0;color:#7b8791;font-size:8px;line-height:1.5}.decision-warning+.field{margin-top:14px}.override-row{margin-top:12px;padding:10px;border-radius:7px;background:#fff6e5}.rollback-versions{margin-bottom:14px;display:grid;gap:7px}.rollback-versions label{padding:10px;display:flex;align-items:center;gap:9px;border:1px solid #dce6ed;border-radius:8px}.rollback-versions label.active{border-color:#6fa8d5;background:#eef7fe}.rollback-versions strong,.rollback-versions small{display:block}.rollback-versions strong{color:#284d68;font-size:10px}.rollback-versions small{margin-top:4px;color:#8194a4;font-size:8px}
@media(max-width:1300px){.hero-orbit{right:20px;opacity:.65}.workspace-grid{grid-template-columns:1fr 1fr}.latest-card{grid-column:1/-1}.proposal-grid,.benchmark-grid{grid-template-columns:repeat(2,1fr)}.agent-readiness-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:950px){.evolution-hero{padding:32px}.hero-orbit{display:none}.hero-metrics{position:static;width:max-content;margin-top:26px}.evolution-pipeline{grid-template-columns:1fr}.evolution-pipeline article{border-right:0;border-bottom:1px solid #e3ebf2}.evolution-pipeline svg{display:none}.workspace-grid,.proposal-grid,.benchmark-grid,.analysis-grid{grid-template-columns:1fr}.latest-card{grid-column:auto}.agent-readiness-grid{grid-template-columns:1fr}.queue-toolbar{align-items:stretch;flex-direction:column}.search-box{width:100%}.studio-tabs{overflow:auto}.hero-metrics div{min-width:92px}.gate-report>div,.report-score-grid{grid-template-columns:repeat(2,1fr)}}
</style>
