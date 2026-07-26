<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Activity, Archive, Bot, Boxes, CheckCircle2, ChevronDown, ChevronRight,
  CirclePlus, Database, FlaskConical, Folder, FolderPlus, Gauge, MessagesSquare,
  Pencil, Play, Save, Search, Settings2, SlidersHorizontal, Sparkles,
  Trash2, Users, Wrench, X,
} from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAgentChatStore } from '../stores/agentChat'
import { useAppStore } from '../stores/app'

const app = useAppStore()
const chat = useAgentChatStore()
const agents = ref<Entity[]>([])
const groups = ref<Entity[]>([])
const tools = ref<Entity[]>([])
const skills = ref<Entity[]>([])
const bases = ref<Entity[]>([])
const knowledgeGroups = ref<Entity[]>([])
const policies = ref<Entity[]>([])
const endpoints = ref<Entity[]>([])
const extensions = ref<Entity[]>([])
const creating = ref(false)
const settingsTab = ref<'basic'|'model'|'rag'|'generation'|'capabilities'|'preview'>('basic')
const previewQuery = ref('')
const previewLoading = ref(false)
const ragPreview = ref<Entity | null>(null)
const evaluationLoading = ref(false)
const ragEvaluation = ref<Entity | null>(null)
const suggestedQuestionsText = ref('')
const customVariablesText = ref('')
const editingAgentId = ref('')
const activeGroupId = ref('all')
const agentSearch = ref('')
const groupSearch = ref('')
const collapsedStatuses = reactive<Record<string, boolean>>({
  active: false,
  candidate: false,
  archived: false,
})
const groupModalOpen = ref(false)
const editingGroupId = ref('')
const form = reactive({
  name: '', slug: '', description: '', system_prompt: '', model_endpoint_id: '',
  image_model_endpoint_id: '',
  group_id: '',
  model: 'demo-model', temperature: 0.3, tools: [] as string[], skills: [] as string[],
  mcp_extensions: [] as string[], knowledge_bases: [] as string[],
  approval_policy_id: '', security_profile: 'default',
  rag_config: {
    enabled: true, knowledge_group_ids: [] as string[], similarity_threshold: 0,
    dense_weight: 0.65, lexical_weight: 0.35, candidate_k: 30, rerank_k: 12,
    top_k: 6, context_char_budget: 12000, query_rewrite: true, multi_turn: true,
    max_history_messages: 8, cross_language: false, knowledge_graph: false,
    parent_expansion: true, complete_list_expansion: true, rerank_model: '',
  },
  generation_config: {
    opening_message: '', suggested_questions: [] as string[],
    prompt_template: `你是一个以证据为中心的智能助手。
只依据“检索证据”回答知识性问题；证据不足时明确说明，不得编造。
关键结论必须使用 [资料 N] 引用。若用户要求全部要点或编号列表，必须保持原顺序完整列出。

【对话历史】
{history}

【知识库检索结果】
{knowledge}

【可用引用】
{citations}

【用户问题】
{question}`,
    top_p: 0.9, max_output_tokens: 2048, grounded_refusal: true,
    citation_required: true, verify_answer: true, repair_retry: true,
    custom_variables: {} as Record<string, string>,
  },
})
const groupForm = reactive({
  name: '',
  description: '',
  color: '#1769c2',
  sort_order: 50,
})

const securityProfiles = [
  { value: 'default', label: '继承安全治理' },
  { value: 'read_only', label: '继承范围 · 只读' },
  { value: 'workspace_ask', label: '工作区 · 逐项确认' },
  { value: 'workspace_auto', label: '工作区 · 自动执行' },
  { value: 'custom_ask', label: '指定项目 · 逐项确认' },
  { value: 'custom_auto', label: '指定项目 · 自动执行' },
  { value: 'unrestricted_ask', label: '全盘 · 逐项确认' },
  { value: 'unrestricted_auto', label: '全盘 · 自动执行' },
]
function parse(value: string) { try { return JSON.parse(value || '[]') } catch { return [] } }
function parseObject(value: string) { try { return JSON.parse(value || '{}') } catch { return {} } }
const filteredGroups = computed(() => {
  const keyword = groupSearch.value.trim().toLowerCase()
  return keyword
    ? groups.value.filter(group => `${group.name} ${group.description}`.toLowerCase().includes(keyword))
    : groups.value
})
const activeGroup = computed(() => {
  if (activeGroupId.value === 'all') {
    return { id: 'all', name: '全部 Agent', description: '集中浏览工厂中的全部 Agent', color: '#1769c2' }
  }
  if (activeGroupId.value === 'ungrouped') {
    return { id: 'ungrouped', name: '未分组', description: '尚未归入自定义分组的 Agent', color: '#7c8fa1' }
  }
  return groups.value.find(group => group.id === activeGroupId.value)
    || { id: 'all', name: '全部 Agent', description: '', color: '#1769c2' }
})
const editingGroup = computed(() => groups.value.find(group => group.id === editingGroupId.value))
const visibleAgents = computed(() => {
  const keyword = agentSearch.value.trim().toLowerCase()
  return agents.value.filter(agent => {
    const inGroup = activeGroupId.value === 'all'
      || (activeGroupId.value === 'ungrouped' ? !agent.group_id : agent.group_id === activeGroupId.value)
    const matches = !keyword || `${agent.name} ${agent.slug} ${agent.description}`.toLowerCase().includes(keyword)
    return inGroup && matches
  })
})
const activeCount = computed(() => agents.value.filter(agent => agent.status === 'active').length)
const candidateCount = computed(() => agents.value.filter(agent => agent.status === 'candidate').length)
const archivedCount = computed(() => agents.value.filter(agent => ['archived', 'rejected'].includes(agent.status)).length)
const chatEndpoints = computed(() => endpoints.value.filter(
  endpoint => endpoint.enabled && (endpoint.modality || 'chat') === 'chat',
))
const statusSections = computed(() => [
  {
    key: 'active',
    title: '启用',
    description: '已经验证，可直接对话或加入工作流',
    icon: CheckCircle2,
    agents: visibleAgents.value.filter(agent => agent.status === 'active'),
  },
  {
    key: 'candidate',
    title: '候选',
    description: '可试用的新能力，确认效果后可切换为启用',
    icon: FlaskConical,
    agents: visibleAgents.value.filter(agent => agent.status === 'candidate'),
  },
  {
    key: 'archived',
    title: '已归档',
    description: '保留历史配置，需要时可恢复为候选或启用',
    icon: Archive,
    agents: visibleAgents.value.filter(agent => ['archived', 'rejected'].includes(agent.status)),
  },
])
const ungroupedCount = computed(() => agents.value.filter(agent => !agent.group_id).length)
function groupAgentCount(groupId: string) {
  return agents.value.filter(agent => agent.group_id === groupId).length
}
function agentGroupColor(agent: Entity) {
  return groups.value.find(group => group.id === agent.group_id)?.color || '#71879a'
}

async function load() {
  app.loading(true)
  try {
    [agents.value, groups.value, tools.value, skills.value, bases.value, knowledgeGroups.value, policies.value, endpoints.value, extensions.value] = await Promise.all([
      api.get('/agents'), api.get('/agent-groups'), api.get('/tools'), api.get('/skills'), api.get('/knowledge-bases'),
      api.get('/knowledge-groups'), api.get('/approval-policies'), api.get('/model-endpoints'), api.get('/extensions'),
    ])
    if (
      !['all', 'ungrouped'].includes(activeGroupId.value)
      && !groups.value.some(group => group.id === activeGroupId.value)
    ) activeGroupId.value = 'all'
    form.approval_policy_id ||= policies.value.find(item => item.is_default)?.id || ''
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    app.loading(false)
  }
}

function resetForm() {
  const defaultEndpoint = chatEndpoints.value[0]
  Object.assign(form, {
    name: '', slug: '', description: '', system_prompt: '', model_endpoint_id: defaultEndpoint?.id || '',
    image_model_endpoint_id: '',
    group_id: groups.value.some(group => group.id === activeGroupId.value) ? activeGroupId.value : '',
    model: defaultEndpoint?.default_model || '', temperature: 0.3,
    tools: tools.value.map(item => item.name),
    skills: skills.value.filter(item => item.enabled).map(item => item.id),
    mcp_extensions: extensions.value.filter(item => item.kind === 'mcp' && item.enabled).map(item => item.id),
    knowledge_bases: [],
    approval_policy_id: policies.value.find(item => item.is_default)?.id || '',
    security_profile: 'default',
    rag_config: {
      enabled: true, knowledge_group_ids: [], similarity_threshold: 0,
      dense_weight: 0.65, lexical_weight: 0.35, candidate_k: 30, rerank_k: 12,
      top_k: 6, context_char_budget: 12000, query_rewrite: true, multi_turn: true,
      max_history_messages: 8, cross_language: false, knowledge_graph: false,
      parent_expansion: true, complete_list_expansion: true, rerank_model: '',
    },
    generation_config: {
      opening_message: '', suggested_questions: [],
      prompt_template: `你是一个以证据为中心的智能助手。
只依据“检索证据”回答知识性问题；证据不足时明确说明，不得编造。
关键结论必须使用 [资料 N] 引用。若用户要求全部要点或编号列表，必须保持原顺序完整列出。

【对话历史】
{history}

【知识库检索结果】
{knowledge}

【可用引用】
{citations}

【用户问题】
{question}`,
      top_p: 0.9, max_output_tokens: 2048, grounded_refusal: true,
      citation_required: true, verify_answer: true, repair_retry: true,
      custom_variables: {},
    },
  })
  suggestedQuestionsText.value = ''
  customVariablesText.value = ''
  settingsTab.value = 'basic'
  ragPreview.value = null
  ragEvaluation.value = null
  previewQuery.value = ''
  editingAgentId.value = ''
  creating.value = true
}
function closeForm() {
  creating.value = false
  editingAgentId.value = ''
}
function toggle(list: string[], value: string) {
  const index = list.indexOf(value)
  index >= 0 ? list.splice(index, 1) : list.push(value)
}
function editAgent(agent: Entity) {
  resetForm()
  const permissions = parseObject(agent.permissions_json)
  const ragConfig = parseObject(agent.rag_config_json)
  const generationConfig = parseObject(agent.generation_config_json)
  Object.assign(form, {
    name: agent.name,
    slug: agent.slug,
    description: agent.description,
    system_prompt: agent.system_prompt,
    model_endpoint_id: agent.model_endpoint_id || chatEndpoints.value[0]?.id || '',
    image_model_endpoint_id: agent.image_model_endpoint_id || '',
    group_id: agent.group_id || '',
    model: agent.model,
    temperature: agent.temperature,
    tools: Array.from(new Set([...parse(agent.tools_json), 'exec'])),
    skills: parse(agent.skills_json).length ? parse(agent.skills_json) : skills.value.filter(item => item.enabled).map(item => item.id),
    mcp_extensions: Array.isArray(permissions.mcp_extensions)
      ? permissions.mcp_extensions
      : extensions.value.filter(item => item.kind === 'mcp' && item.enabled).map(item => item.id),
    knowledge_bases: parse(agent.knowledge_bases_json),
    approval_policy_id: permissions.approval_policy_id || policies.value.find(item => item.is_default)?.id || '',
    security_profile: permissions.security_profile || 'default',
    rag_config: { ...form.rag_config, ...ragConfig },
    generation_config: { ...form.generation_config, ...generationConfig },
  })
  suggestedQuestionsText.value = (generationConfig.suggested_questions || []).join('\n')
  customVariablesText.value = Object.entries(generationConfig.custom_variables || {})
    .map(([key, value]) => `${key}=${value}`).join('\n')
  settingsTab.value = 'basic'
  ragPreview.value = null
  ragEvaluation.value = null
  previewQuery.value = ''
  editingAgentId.value = agent.id
  creating.value = true
}
function validateAgentForm() {
  if (form.name.trim().length < 2) return 'Agent 名称至少需要 2 个字符'
  if (!/^[a-z0-9][a-z0-9_-]{1,99}$/.test(form.slug)) return '唯一标识需使用小写字母、数字、下划线或连字符，且至少 2 个字符'
  if (form.system_prompt.trim().length < 10) return '系统提示词至少需要 10 个字符'
  if (!form.model_endpoint_id) return 'Agent 必须绑定一个已启用的在线对话模型接口'
  if (!form.generation_config.prompt_template.includes('{question}') || !form.generation_config.prompt_template.includes('{knowledge}')) return '生成提示词必须包含 {question} 和 {knowledge}'
  if (form.rag_config.dense_weight + form.rag_config.lexical_weight <= 0) return '向量与全文检索权重不能同时为 0'
  return ''
}
async function saveAgent() {
  const validation = validateAgentForm()
  if (validation) return app.notify(validation, 'error')
  app.loading(true)
  try {
    form.generation_config.suggested_questions = suggestedQuestionsText.value.split('\n').map(item => item.trim()).filter(Boolean).slice(0, 8)
    form.generation_config.custom_variables = Object.fromEntries(
      customVariablesText.value.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
        const index = line.indexOf('=')
        return index > 0 ? [line.slice(0, index).trim(), line.slice(index + 1).trim()] : [line, '']
      }),
    )
    const payload = {
      name: form.name,
      description: form.description,
      system_prompt: form.system_prompt,
      model_endpoint_id: form.model_endpoint_id || null,
      image_model_endpoint_id: form.image_model_endpoint_id || null,
      group_id: form.group_id || null,
      model: chatEndpoints.value.find(item => item.id === form.model_endpoint_id)?.default_model || form.model,
      temperature: form.temperature,
      tools: Array.from(new Set([...form.tools, 'exec'])),
      skills: form.skills,
      knowledge_bases: form.knowledge_bases,
      provider: chatEndpoints.value.find(item => item.id === form.model_endpoint_id)?.provider_type || 'openai-compatible',
      rag_config: form.rag_config,
      generation_config: form.generation_config,
      permissions: {
        tool_mode: 'ask',
        approval_policy_id: form.approval_policy_id,
        security_profile: form.security_profile,
        mcp_extensions: form.mcp_extensions,
      },
    }
    await (editingAgentId.value
      ? api.patch(`/agents/${editingAgentId.value}`, payload)
      : api.post('/agents', { ...payload, slug: form.slug, is_template: true }))
    app.notify(editingAgentId.value ? 'Agent 设置已更新' : 'Agent 已加入工厂')
    closeForm()
    await load()
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    app.loading(false)
  }
}
async function runRagPreview() {
  if (!editingAgentId.value) return app.notify('请先保存 Agent，再测试实际 RAG 链路', 'error')
  if (!previewQuery.value.trim()) return app.notify('请输入要测试的问题', 'error')
  previewLoading.value = true
  ragPreview.value = null
  try {
    ragPreview.value = await api.post(`/agents/${editingAgentId.value}/rag/preview`, {
      query: previewQuery.value.trim(), history: [],
    })
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    previewLoading.value = false
  }
}
async function runRagEvaluation() {
  if (!editingAgentId.value) return app.notify('请先保存 Agent，再运行评测集', 'error')
  evaluationLoading.value = true
  ragEvaluation.value = null
  try {
    ragEvaluation.value = await api.post(`/agents/${editingAgentId.value}/rag/evaluate`, { limit: 20 })
    if (!ragEvaluation.value?.summary?.cases) app.notify('当前没有启用的评测用例', 'error')
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    evaluationLoading.value = false
  }
}
function openChat(agent: Entity) {
  if (!['active', 'candidate'].includes(agent.status)) {
    app.notify('请先把已归档 Agent 恢复为候选或启用状态', 'error')
    return
  }
  chat.openAgent(agent)
}
async function changeAgentStatus(agent: Entity, event: Event) {
  const status = (event.target as HTMLSelectElement).value
  app.loading(true)
  try {
    const updated: Entity = await api.patch(`/agents/${agent.id}`, { status })
    agent.status = updated.status
    app.notify(
      status === 'active' ? 'Agent 已启用'
        : status === 'candidate' ? 'Agent 已转为候选'
          : 'Agent 已归档',
    )
  } catch (error: any) {
    app.notify(error.message, 'error')
    await load()
  } finally {
    app.loading(false)
  }
}
function openCreateGroup() {
  Object.assign(groupForm, { name: '', description: '', color: '#1769c2', sort_order: (groups.value.length + 1) * 10 })
  editingGroupId.value = ''
  groupModalOpen.value = true
}
function openEditGroup(group: Entity) {
  Object.assign(groupForm, {
    name: group.name,
    description: group.description || '',
    color: group.color || '#1769c2',
    sort_order: Number(group.sort_order || 0),
  })
  editingGroupId.value = group.id
  groupModalOpen.value = true
}
function closeGroupModal() {
  groupModalOpen.value = false
  editingGroupId.value = ''
}
async function saveGroup() {
  if (!groupForm.name.trim()) return app.notify('请输入分组名称', 'error')
  app.loading(true)
  try {
    const payload = {
      name: groupForm.name.trim(),
      description: groupForm.description.trim(),
      color: groupForm.color,
      sort_order: groupForm.sort_order,
    }
    const result: Entity = editingGroupId.value
      ? await api.patch(`/agent-groups/${editingGroupId.value}`, payload)
      : await api.post('/agent-groups', payload)
    app.notify(editingGroupId.value ? '分组已更新' : '自定义分组已创建')
    closeGroupModal()
    await load()
    activeGroupId.value = result.id
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    app.loading(false)
  }
}
async function deleteGroup(group: Entity) {
  if (!window.confirm(`删除分组“${group.name}”？其中的 Agent 会移入“未分组”。`)) return
  app.loading(true)
  try {
    await api.delete(`/agent-groups/${group.id}`)
    closeGroupModal()
    activeGroupId.value = 'ungrouped'
    app.notify('分组已删除，原有 Agent 已移入未分组')
    await load()
  } catch (error: any) {
    app.notify(error.message, 'error')
  } finally {
    app.loading(false)
  }
}
async function assignAgentGroup(agent: Entity, event: Event) {
  const groupId = (event.target as HTMLSelectElement).value
  app.loading(true)
  try {
    await api.patch(`/agents/${agent.id}`, { group_id: groupId || null })
    agent.group_id = groupId || null
    groups.value = await api.get('/agent-groups')
    app.notify(groupId ? 'Agent 已移动到新分组' : 'Agent 已移入未分组')
  } catch (error: any) {
    app.notify(error.message, 'error')
    await load()
  } finally {
    app.loading(false)
  }
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && creating.value) closeForm()
  else if (event.key === 'Escape' && groupModalOpen.value) closeGroupModal()
}

watch([creating, groupModalOpen], ([agentOpen, groupOpen]) => {
  document.body.style.overflow = agentOpen || groupOpen ? 'hidden' : ''
})
watch(() => form.model_endpoint_id, endpointId => {
  const endpoint = chatEndpoints.value.find(item => item.id === endpointId)
  if (endpoint?.default_model) form.model = endpoint.default_model
})
onMounted(() => {
  void load()
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <PageHeader eyebrow="AGENT FACTORY" title="Agent 工厂" description="先按业务分组定位 Agent，再按启用、候选和已归档状态分类浏览与管理。">
    <button class="btn" @click="openCreateGroup"><FolderPlus :size="15" />新建分组</button>
    <button class="btn btn-primary" @click="resetForm"><CirclePlus :size="15" />创建 Agent</button>
  </PageHeader>

  <div class="factory-summary">
    <div><span class="summary-icon"><Boxes :size="18" /></span><p><strong>{{ agents.length }}</strong><small>全部 Agent</small></p></div>
    <div class="active-summary"><span class="summary-icon"><CheckCircle2 :size="18" /></span><p><strong>{{ activeCount }}</strong><small>启用</small></p></div>
    <div class="candidate-summary"><span class="summary-icon"><FlaskConical :size="18" /></span><p><strong>{{ candidateCount }}</strong><small>候选</small></p></div>
    <div class="archived-summary"><span class="summary-icon"><Archive :size="18" /></span><p><strong>{{ archivedCount }}</strong><small>已归档</small></p></div>
  </div>

  <div class="factory-layout">
    <aside class="group-sidebar">
      <header><div><strong>Agent 分组</strong><span>{{ groups.length }} 个分组</span></div><button title="新建分组" @click="openCreateGroup"><FolderPlus :size="15" /></button></header>
      <label v-if="groups.length > 6" class="group-search"><Search :size="13" /><input v-model="groupSearch" placeholder="查找分组"></label>
      <div class="group-navigation">
        <button class="special-group" :class="{ active: activeGroupId === 'all' }" @click="activeGroupId = 'all'">
          <span class="nav-folder all"><Users :size="15" /></span><span><strong>全部 Agent</strong><small>所有分组</small></span><b>{{ agents.length }}</b>
        </button>
        <div v-for="group in filteredGroups" :key="group.id" class="group-nav-item" :class="{ active: activeGroupId === group.id }">
          <button class="group-select" @click="activeGroupId = group.id">
            <span class="nav-folder" :style="{ color: group.color, background: `${group.color}16` }"><Folder :size="15" /></span>
            <span><strong>{{ group.name }}</strong><small>{{ group.description || '自定义 Agent 分组' }}</small></span>
            <b>{{ groupAgentCount(group.id) }}</b>
          </button>
          <button class="group-edit" :title="`编辑 ${group.name}`" @click="openEditGroup(group)"><Pencil :size="12" /></button>
        </div>
        <button class="special-group" :class="{ active: activeGroupId === 'ungrouped' }" @click="activeGroupId = 'ungrouped'">
          <span class="nav-folder ungrouped"><Folder :size="15" /></span><span><strong>未分组</strong><small>待整理</small></span><b>{{ ungroupedCount }}</b>
        </button>
        <div v-if="groupSearch && !filteredGroups.length" class="group-search-empty">没有匹配的分组</div>
      </div>
      <footer><Sparkles :size="13" /><span>分组列表独立滚动，不会撑长页面</span></footer>
    </aside>

    <section class="agent-browser">
      <header class="browser-header">
        <div class="current-group">
          <span :style="{ color: activeGroup.color, background: `${activeGroup.color}14` }"><Folder :size="19" /></span>
          <div><h2>{{ activeGroup.name }} <small>{{ visibleAgents.length }}</small></h2><p>{{ activeGroup.description }}</p></div>
        </div>
        <div class="browser-actions">
          <label class="agent-search"><Search :size="14" /><input v-model="agentSearch" placeholder="搜索当前分组的 Agent"></label>
          <button v-if="groups.some(group => group.id === activeGroupId)" class="compact-icon-btn" title="编辑当前分组" @click="openEditGroup(activeGroup)"><Pencil :size="14" /></button>
        </div>
      </header>
      <div v-if="visibleAgents.length" class="status-agent-sections">
        <section v-for="section in statusSections" :key="section.key" class="status-agent-section" :class="`status-section-${section.key}`">
          <header>
            <div class="status-section-heading">
              <span><component :is="section.icon" :size="16" /></span>
              <div><h3>{{ section.title }} <small>{{ section.agents.length }}</small></h3><p>{{ section.description }}</p></div>
            </div>
            <button
              :aria-label="`${collapsedStatuses[section.key] ? '展开' : '收起'}${section.title}分类`"
              @click="collapsedStatuses[section.key] = !collapsedStatuses[section.key]"
            >
              <ChevronRight v-if="collapsedStatuses[section.key]" :size="15" />
              <ChevronDown v-else :size="15" />
            </button>
          </header>
          <div v-if="!collapsedStatuses[section.key] && section.agents.length" class="agent-grid">
            <article v-for="agent in section.agents" :key="agent.id" class="agent-card">
              <div class="agent-card-top"><span class="agent-avatar" :style="{ background: `linear-gradient(135deg, ${agentGroupColor(agent)}, #30a6bd)` }"><Bot :size="20" /></span><StatusBadge :status="agent.status" /></div>
              <div class="agent-card-copy"><h3>{{ agent.name }} <small>v{{ agent.version }}</small></h3><p>{{ agent.description || '尚未填写职责说明' }}</p></div>
              <div class="capability-row"><span v-for="tool in parse(agent.tools_json).slice(0,4)" :key="tool">{{ tool }}</span><span v-if="parse(agent.tools_json).length>4">+{{ parse(agent.tools_json).length-4 }}</span></div>
              <div class="card-classifiers">
                <label class="card-group-select"><Folder :size="12" /><select :value="agent.group_id || ''" aria-label="所属业务分组" @change="assignAgentGroup(agent, $event)"><option value="">未分组</option><option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }}</option></select></label>
                <label class="card-status-select">
                  <component :is="section.icon" :size="12" />
                  <select :value="['rejected', 'archived'].includes(agent.status) ? 'archived' : agent.status" aria-label="Agent 状态" @change="changeAgentStatus(agent, $event)">
                    <option value="active">启用</option>
                    <option value="candidate">候选</option>
                    <option value="archived">已归档</option>
                  </select>
                </label>
              </div>
              <div class="agent-card-actions">
                <button class="btn chat-button" :disabled="!['active', 'candidate'].includes(agent.status)" @click="openChat(agent)">
                  <MessagesSquare :size="14" />{{ ['active', 'candidate'].includes(agent.status) ? '与 Agent 对话' : '恢复后可对话' }}
                </button>
                <button class="btn settings-button" title="编辑 Agent 设置" @click="editAgent(agent)"><Settings2 :size="14" /></button>
              </div>
            </article>
          </div>
          <div v-else-if="!collapsedStatuses[section.key]" class="status-section-empty">当前业务分组中暂无{{ section.title }} Agent</div>
        </section>
      </div>
      <div v-if="!visibleAgents.length" class="empty factory-empty"><Bot :size="28" /><strong>{{ agentSearch ? '没有匹配的 Agent' : '当前分组还没有 Agent' }}</strong><span>{{ agentSearch ? '尝试更换关键词或选择其他分组。' : '创建 Agent 或使用卡片中的分组选择器移动 Agent。' }}</span></div>
    </section>
  </div>

  <Teleport to="body">
    <Transition name="factory-modal">
      <div v-if="creating" class="factory-modal-layer">
        <button class="factory-modal-backdrop" aria-label="关闭创建窗口" @click="closeForm" />
        <section class="factory-modal" role="dialog" aria-modal="true" :aria-label="editingAgentId ? '修改 Agent 设置' : '创建 Agent'">
          <header class="factory-modal-header">
            <div><span>{{ editingAgentId ? 'AGENT SETTINGS' : 'NEW AGENT' }}</span><h2>{{ editingAgentId ? '修改 Agent 设置' : '创建新的 Agent' }}</h2><p>配置职责、模型、执行能力与安全边界。</p></div>
            <button title="关闭" @click="closeForm"><X :size="18" /></button>
          </header>
          <div class="factory-settings-layout">
            <nav class="settings-tabs">
              <button :class="{active:settingsTab==='basic'}" @click="settingsTab='basic'"><Bot :size="15" /><span><strong>基础信息</strong><small>身份与职责</small></span></button>
              <button :class="{active:settingsTab==='model'}" @click="settingsTab='model'"><SlidersHorizontal :size="15" /><span><strong>模型参数</strong><small>采样与输出</small></span></button>
              <button :class="{active:settingsTab==='rag'}" @click="settingsTab='rag'"><Database :size="15" /><span><strong>RAG 检索</strong><small>R · A 全链路</small></span></button>
              <button :class="{active:settingsTab==='generation'}" @click="settingsTab='generation'"><Sparkles :size="15" /><span><strong>生成策略</strong><small>G · 提示与校验</small></span></button>
              <button :class="{active:settingsTab==='capabilities'}" @click="settingsTab='capabilities'"><Wrench :size="15" /><span><strong>能力与安全</strong><small>工具 / MCP / Skill</small></span></button>
              <button :class="{active:settingsTab==='preview'}" @click="settingsTab='preview'"><Activity :size="15" /><span><strong>链路测试</strong><small>证据与 Prompt</small></span></button>
            </nav>
            <div class="factory-modal-body">
              <section v-if="settingsTab==='basic'" class="settings-section form-grid">
                <div class="settings-section-title full"><span><Bot :size="16" /></span><div><h3>Agent 身份</h3><p>定义它是谁、负责什么，以及首次对话时如何引导用户。</p></div></div>
                <div class="field"><label>名称</label><input v-model="form.name" class="input" placeholder="例如：文献综述 Agent"></div>
                <div class="field"><label>唯一标识</label><input v-model="form.slug" class="input" :disabled="!!editingAgentId" placeholder="literature-reviewer"><span v-if="editingAgentId" class="field-help">保存后不可修改，用于 Agent 联动。</span></div>
                <div class="field full"><label>职责说明</label><input v-model="form.description" class="input" placeholder="概括这个 Agent 负责解决的问题"></div>
                <div class="field"><label>所属分组</label><select v-model="form.group_id" class="select"><option value="">未分组</option><option v-for="item in groups" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
                <div class="field full"><label>基础系统提示词</label><textarea v-model="form.system_prompt" class="textarea large" placeholder="明确角色、工作边界和输出规范。" /></div>
                <div class="field full"><label>开场白</label><textarea v-model="form.generation_config.opening_message" class="textarea compact" placeholder="你好，我会基于已绑定知识库提供有引用的回答。" /></div>
                <div class="field full"><label>推荐问题</label><textarea v-model="suggestedQuestionsText" class="textarea compact" placeholder="每行一个，最多 8 个" /><span class="field-help">新会话中会显示为可直接点击的问题。</span></div>
              </section>

              <section v-else-if="settingsTab==='model'" class="settings-section form-grid">
                <div class="settings-section-title full"><span><Gauge :size="16" /></span><div><h3>生成模型</h3><p>接口决定实际模型，采样参数会直接传入每次生成请求。</p></div></div>
                <div class="field full"><label>回答模型 API 接口 · 必选</label><select v-model="form.model_endpoint_id" class="select"><option value="" disabled>{{ chatEndpoints.length ? '请选择在线接口' : '尚未配置在线对话接口' }}</option><option v-for="item in chatEndpoints" :key="item.id" :value="item.id">{{ item.name }} / {{ item.default_model }}</option></select><span class="field-help online-required">所有 Agent 均通过现有在线接口执行，不再提供离线演示模式。</span></div>
                <div class="field full"><label>图片生成 API 接口</label><select v-model="form.image_model_endpoint_id" class="select"><option value="">自动使用已启用的图片模型</option><option v-for="item in endpoints.filter(endpoint => endpoint.enabled && endpoint.modality==='image')" :key="item.id" :value="item.id">{{ item.name }} / {{ item.default_model }}</option></select><span class="field-help">仅在用户明确要求图片或回答确有视觉表达需要时调用，不影响普通文字回答。</span></div>
                <div class="field"><label>模型名覆盖</label><input v-model="form.model" class="input"><span class="field-help">绑定接口时默认采用 Endpoint 模型。</span></div>
                <div class="field"><label>Temperature</label><input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" class="input"></div>
                <div class="field"><label>Top P</label><input v-model.number="form.generation_config.top_p" type="number" min="0.01" max="1" step="0.05" class="input"></div>
                <div class="field"><label>最大输出 Token</label><input v-model.number="form.generation_config.max_output_tokens" type="number" min="128" max="32768" step="128" class="input"></div>
              </section>

              <section v-else-if="settingsTab==='rag'" class="settings-section form-grid">
                <div class="settings-section-title full"><span><Database :size="16" /></span><div><h3>RAG 检索与证据增强</h3><p>多轮问题改写 → 向量/全文召回 → 加权融合 → Rerank → 父块与完整列表扩展。</p></div><label class="master-switch"><input v-model="form.rag_config.enabled" type="checkbox"><i /><b>{{ form.rag_config.enabled ? '启用' : '关闭' }}</b></label></div>
                <div class="field full option-block"><label>知识库</label><div><button v-for="item in bases" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.knowledge_bases.includes(item.id) }" @click="toggle(form.knowledge_bases,item.id)">{{ item.name }}</button><span v-if="!bases.length" class="field-help">还没有可绑定的知识库。</span></div></div>
                <div v-if="knowledgeGroups.length" class="field full option-block"><label>知识库分组</label><div><button v-for="item in knowledgeGroups" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.rag_config.knowledge_group_ids.includes(item.id) }" @click="toggle(form.rag_config.knowledge_group_ids,item.id)">{{ item.name }}</button></div></div>
                <div class="field"><label>相似度阈值 · {{ Number(form.rag_config.similarity_threshold).toFixed(2) }}</label><input v-model.number="form.rag_config.similarity_threshold" type="range" min="0" max="1" step="0.05"><span class="field-help">低于阈值的候选不会进入上下文。</span></div>
                <div class="field"><label>候选召回 / Rerank / 最终证据</label><div class="triple-input"><input v-model.number="form.rag_config.candidate_k" type="number" min="5" max="100"><input v-model.number="form.rag_config.rerank_k" type="number" min="1" max="50"><input v-model.number="form.rag_config.top_k" type="number" min="1" max="20"></div></div>
                <div class="field"><label>向量权重</label><input v-model.number="form.rag_config.dense_weight" type="number" min="0" max="1" step="0.05" class="input"></div>
                <div class="field"><label>全文权重</label><input v-model.number="form.rag_config.lexical_weight" type="number" min="0" max="1" step="0.05" class="input"></div>
                <div class="field"><label>上下文字符预算</label><input v-model.number="form.rag_config.context_char_budget" type="number" min="1000" max="100000" step="1000" class="input"></div>
                <div class="field"><label>Rerank 模型覆盖</label><input v-model="form.rag_config.rerank_model" class="input" placeholder="留空继承知识库全局配置"></div>
                <div class="field full switch-grid">
                  <label><input v-model="form.rag_config.query_rewrite" type="checkbox"><span><b>多查询改写</b><small>生成互补检索语句</small></span></label>
                  <label><input v-model="form.rag_config.multi_turn" type="checkbox"><span><b>多轮对话优化</b><small>追问改写为独立问题</small></span></label>
                  <label><input v-model="form.rag_config.cross_language" type="checkbox"><span><b>跨语言检索</b><small>增加中英互译查询</small></span></label>
                  <label><input v-model="form.rag_config.knowledge_graph" type="checkbox"><span><b>知识图谱增强</b><small>实体邻接证据加权</small></span></label>
                  <label><input v-model="form.rag_config.parent_expansion" type="checkbox"><span><b>父块扩展</b><small>补足章节上下文</small></span></label>
                  <label><input v-model="form.rag_config.complete_list_expansion" type="checkbox"><span><b>完整列表扩展</b><small>避免五点只返回前三点</small></span></label>
                </div>
              </section>

              <section v-else-if="settingsTab==='generation'" class="settings-section form-grid">
                <div class="settings-section-title full"><span><Sparkles :size="16" /></span><div><h3>证据生成与校验</h3><p>模板变量会在每轮运行时替换，生成后执行引用与完整列表校验，失败可自动修复一次。</p></div></div>
                <div class="field full"><label>RAG 系统提示词模板</label><textarea v-model="form.generation_config.prompt_template" class="textarea prompt-editor" /><span class="field-help">保留变量：{question}、{knowledge}、{history}、{citations}</span></div>
                <div class="field full"><label>自定义变量</label><textarea v-model="customVariablesText" class="textarea compact" placeholder="每行 key=value；不能覆盖保留变量" /></div>
                <div class="field full switch-grid">
                  <label><input v-model="form.generation_config.grounded_refusal" type="checkbox"><span><b>证据不足拒答</b><small>明确知识库未找到答案</small></span></label>
                  <label><input v-model="form.generation_config.citation_required" type="checkbox"><span><b>强制引用</b><small>关键结论使用 [资料 N]</small></span></label>
                  <label><input v-model="form.generation_config.verify_answer" type="checkbox"><span><b>生成后校验</b><small>检查引用与列表完整性</small></span></label>
                  <label><input v-model="form.generation_config.repair_retry" type="checkbox"><span><b>自动修复一次</b><small>校验失败后重新生成</small></span></label>
                </div>
              </section>

              <section v-else-if="settingsTab==='capabilities'" class="settings-section form-grid">
                <div class="settings-section-title full"><span><Wrench :size="16" /></span><div><h3>执行能力与安全边界</h3><p>Exec 是固有能力，实际可访问范围由安全策略和审批策略共同控制。</p></div></div>
                <div class="field"><label>审批策略</label><select v-model="form.approval_policy_id" class="select"><option v-for="item in policies" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
                <div class="field"><label>默认安全策略</label><select v-model="form.security_profile" class="select"><option v-for="item in securityProfiles" :key="item.value" :value="item.value">{{ item.label }}</option></select><span class="field-help">对话时仍可临时切换。</span></div>
                <div class="field full option-block"><label>工具权限</label><div><button v-for="item in tools" :key="item.name" class="btn btn-sm" :disabled="item.name==='exec'" :title="item.name==='exec'?'每个 Agent 固有的命令执行能力，由安全策略约束':''" :class="{ 'btn-primary': item.name==='exec' || form.tools.includes(item.name) }" @click="item.name!=='exec' && toggle(form.tools,item.name)"><Wrench :size="13" />{{ item.name }}<template v-if="item.name==='exec'"> · 固有</template></button></div></div>
                <div class="field full option-block"><label>MCP 服务</label><div><button v-for="item in extensions.filter(extension=>extension.kind==='mcp' && extension.enabled)" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.mcp_extensions.includes(item.id) }" @click="toggle(form.mcp_extensions,item.id)">{{ item.name }}</button></div><span class="field-help">选中的 MCP 工具会直接加入 Agent 的模型工具列表。</span></div>
                <div class="field full option-block"><label>Skills</label><div><button v-for="item in skills" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.skills.includes(item.id) }" @click="toggle(form.skills,item.id)">{{ item.name }}</button></div></div>
              </section>

              <section v-else class="settings-section preview-section">
                <div class="settings-section-title"><span><Activity :size="16" /></span><div><h3>真实 RAG 链路测试</h3><p>执行与正式对话相同的检索配置，不调用答案模型，也不写入对话或知识库。</p></div></div>
                <div class="preview-query"><textarea v-model="previewQuery" class="textarea" placeholder="例如：虚拟内存包括哪五点？请完整列出。" /><div class="preview-actions"><button class="btn btn-primary" :disabled="previewLoading || !editingAgentId" @click="runRagPreview"><Play :size="14" />{{ previewLoading ? '检索中' : '运行测试' }}</button><button class="btn" :disabled="evaluationLoading || !editingAgentId" @click="runRagEvaluation"><Gauge :size="14" />{{ evaluationLoading ? '评测中' : '运行评测集' }}</button></div></div>
                <div v-if="!editingAgentId" class="preview-hint">创建新 Agent 时请先保存，再进入设置执行链路测试。</div>
                <div v-if="ragEvaluation?.summary" class="evaluation-summary"><div><strong>{{ Math.round(ragEvaluation.summary.recall_at_k * 100) }}%</strong><span>Recall@K</span></div><div><strong>{{ Math.round(ragEvaluation.summary.mrr * 100) }}%</strong><span>MRR</span></div><div><strong>{{ Math.round(ragEvaluation.summary.ndcg * 100) }}%</strong><span>NDCG</span></div><div><strong>{{ ragEvaluation.summary.average_latency_ms }} ms</strong><span>平均延迟</span></div></div>
                <template v-if="ragPreview">
                  <div class="preview-metrics"><div><strong>{{ ragPreview.chunks?.length || 0 }}</strong><span>最终证据</span></div><div><strong>{{ ragPreview.trace?.fused_candidates || 0 }}</strong><span>融合候选</span></div><div><strong>{{ ragPreview.trace?.context_chars || 0 }}</strong><span>上下文字符</span></div><div><strong>{{ ragPreview.pipeline?.length || 0 }}</strong><span>链路步骤</span></div></div>
                  <div class="preview-standalone"><b>独立检索问题</b><span>{{ ragPreview.standalone_query }}</span></div>
                  <div class="evidence-list"><article v-for="(item,index) in ragPreview.chunks" :key="item.id"><header><b>[资料 {{ index + 1 }}] {{ item.title }}</b><span>{{ Number(item.score || 0).toFixed(3) }}</span></header><p>{{ item.context }}</p><small>{{ item.citation }}</small></article></div>
                  <details class="prompt-preview"><summary>查看最终 Prompt</summary><pre>{{ ragPreview.rendered_prompt }}</pre></details>
                </template>
              </section>
            </div>
          </div>
          <footer class="factory-modal-footer"><button class="btn" @click="closeForm">取消</button><button class="btn btn-primary" @click="saveAgent"><Save :size="15" />{{ editingAgentId ? '保存修改' : '创建 Agent' }}</button></footer>
        </section>
      </div>
    </Transition>
  </Teleport>

  <Teleport to="body">
    <Transition name="factory-modal">
      <div v-if="groupModalOpen" class="factory-modal-layer">
        <button class="factory-modal-backdrop" aria-label="关闭分组窗口" @click="closeGroupModal" />
        <section class="group-modal" role="dialog" aria-modal="true" :aria-label="editingGroupId ? '编辑 Agent 分组' : '新建 Agent 分组'">
          <header>
            <div><span>AGENT GROUP</span><h2>{{ editingGroupId ? '编辑分组' : '新建自定义分组' }}</h2><p>用简短名称组织同类 Agent，分组数量不受页面高度限制。</p></div>
            <button title="关闭" @click="closeGroupModal"><X :size="17" /></button>
          </header>
          <div class="group-modal-body">
            <div class="field full"><label>分组名称</label><input v-model="groupForm.name" class="input" maxlength="80" placeholder="例如：论文写作"></div>
            <div class="field full"><label>分组说明</label><textarea v-model="groupForm.description" class="textarea" maxlength="300" placeholder="说明这个分组主要包含哪些 Agent" /></div>
            <div class="group-appearance">
              <div class="field"><label>标识颜色</label><div class="color-picker"><input v-model="groupForm.color" type="color"><code>{{ groupForm.color }}</code></div></div>
              <div class="field"><label>排序值</label><input v-model.number="groupForm.sort_order" type="number" min="0" max="10000" class="input"><span class="field-help">数字越小越靠前。</span></div>
            </div>
          </div>
          <footer>
            <button v-if="editingGroup" class="btn btn-danger delete-group-button" @click="deleteGroup(editingGroup)"><Trash2 :size="14" />删除分组</button>
            <span />
            <button class="btn" @click="closeGroupModal">取消</button>
            <button class="btn btn-primary" @click="saveGroup"><Save :size="14" />保存分组</button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.factory-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:20px}.factory-summary>div{display:flex;align-items:center;gap:10px;padding:13px 15px;border:1px solid #dce8f1;border-radius:10px;background:rgba(255,255,255,.78)}.summary-icon{display:grid;width:34px;height:34px;place-items:center;border-radius:9px;color:#1769c2;background:#eaf5fc}.active-summary .summary-icon{color:#177a53;background:#e8f7f0}.candidate-summary .summary-icon{color:#a2690c;background:#fff3d8}.archived-summary .summary-icon{color:#667b8d;background:#eaf0f4}.factory-summary p{margin:0}.factory-summary strong{display:block;font-size:17px;color:#173f65}.factory-summary small{display:block;font-size:9px;color:#7890a5}.agent-groups{display:flex;flex-direction:column;gap:24px}.agent-group{padding:18px;border:1px solid #dce7ef;border-radius:14px;background:rgba(247,251,254,.72)}.group-header{display:flex;align-items:center;gap:11px;margin-bottom:14px}.group-icon{display:grid;width:39px;height:39px;place-items:center;border-radius:10px}.group-header h2{margin:0;color:#173e63;font-size:14px}.group-header h2 small{display:inline-grid;min-width:20px;height:20px;margin-left:5px;place-items:center;border-radius:10px;color:#6e879a;background:#e7eff5;font-size:9px}.group-header p{margin:3px 0 0;color:#71879a;font-size:10px}.agent-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.agent-card{display:flex;min-width:0;min-height:210px;flex-direction:column;padding:15px;border:1px solid #dce7ef;border-radius:12px;background:#fff;box-shadow:0 5px 18px rgba(23,61,94,.05);transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.agent-card:hover{transform:translateY(-2px);border-color:#9dc5e1;box-shadow:0 10px 24px rgba(23,61,94,.1)}.agent-card-top{display:flex;align-items:center;justify-content:space-between}.agent-avatar{display:grid;width:38px;height:38px;place-items:center;border-radius:11px;color:#fff}.agent-card-copy h3{margin:12px 0 5px;color:#153b62;font-size:14px}.agent-card-copy h3 small{color:#8aa0b2;font-size:8px}.agent-card-copy p{display:-webkit-box;min-height:35px;margin:0;overflow:hidden;color:#637a8e;font-size:10px;line-height:1.6;-webkit-line-clamp:2;-webkit-box-orient:vertical}.capability-row{display:flex;min-height:44px;align-content:flex-start;flex-wrap:wrap;gap:4px;margin:10px 0}.capability-row span{height:18px;padding:3px 6px;border-radius:5px;color:#57738b;background:#eef4f8;font-size:8px}.agent-card-actions{display:grid;grid-template-columns:1fr auto;gap:7px;margin-top:auto}.chat-button{justify-content:center;color:#1769c2;border-color:#a9cae1;background:#f5faff}.chat-button:hover{color:#fff;background:#1769c2}.chat-button:disabled{color:#8b9ba8;border-color:#d5e0e8;background:#f1f4f6;cursor:not-allowed}.settings-button{padding-inline:10px}.factory-empty{min-height:220px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px}.factory-empty strong{color:#315673}.factory-empty span{font-size:10px}@media(max-width:1150px){.factory-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.agent-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:720px){.factory-summary{grid-template-columns:1fr 1fr}.agent-grid{grid-template-columns:1fr}.agent-group{padding:12px}}
</style>

<style scoped>
.factory-settings-layout{min-height:0;display:grid;grid-template-columns:180px minmax(0,1fr);overflow:hidden}.settings-tabs{padding:12px 9px;border-right:1px solid #dfe9f0;display:flex;flex-direction:column;gap:4px;background:linear-gradient(180deg,#f5f9fc,#eef5f9)}.settings-tabs>button{width:100%;padding:10px;border:1px solid transparent;border-radius:9px;display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:9px;color:#678197;background:transparent;text-align:left;cursor:pointer}.settings-tabs>button:hover{color:#1769c2;background:#fff}.settings-tabs>button.active{color:#1769c2;border-color:#b8d5e8;background:#fff;box-shadow:0 5px 14px rgba(22,83,126,.09)}.settings-tabs span{min-width:0;display:flex;flex-direction:column;gap:2px}.settings-tabs strong{font-size:10px}.settings-tabs small{color:#8ca0af;font-size:7px}.factory-settings-layout>.factory-modal-body{min-height:0;padding:0;overflow:auto;background:#fff}.settings-section{padding:20px}.settings-section.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.settings-section .full{grid-column:1/-1}.settings-section-title{min-height:52px;padding:0 0 14px;border-bottom:1px solid #e2eaf0;display:flex;align-items:center;gap:10px}.settings-section-title>span{width:35px;height:35px;flex:0 0 35px;border-radius:10px;display:grid;place-items:center;color:#1769c2;background:#e9f4fb}.settings-section-title>div{min-width:0}.settings-section-title h3{margin:0;color:#183f61;font-size:13px}.settings-section-title p{margin:4px 0 0;color:#7d91a2;font-size:8px;line-height:1.5}.settings-section .textarea.large{min-height:130px}.settings-section .textarea.compact{min-height:72px}.prompt-editor{min-height:270px!important;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:10px!important;line-height:1.7!important}.master-switch{margin-left:auto;display:flex;align-items:center;gap:6px;color:#5d788e;font-size:9px}.master-switch input{display:none}.master-switch i{position:relative;width:34px;height:19px;border-radius:99px;background:#a9b8c4;transition:.16s}.master-switch i:after{content:"";position:absolute;left:3px;top:3px;width:13px;height:13px;border-radius:50%;background:#fff;transition:.16s}.master-switch input:checked+i{background:#1b8a67}.master-switch input:checked+i:after{transform:translateX(15px)}.triple-input{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.triple-input input{min-width:0;height:36px;padding:0 8px;border:1px solid #cbd9e6;border-radius:7px;color:#345673;background:#fff}.settings-section input[type=range]{width:100%;accent-color:#1883bd}.switch-grid{padding:10px;border:1px solid #dfe8ef;border-radius:10px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;background:#f8fbfd}.switch-grid>label{min-height:50px;padding:8px;border:1px solid #e0e9ef;border-radius:8px;display:flex;align-items:center;gap:8px;background:#fff;cursor:pointer}.switch-grid input{width:15px;height:15px;accent-color:#1677b8}.switch-grid span{display:flex;flex-direction:column;gap:3px}.switch-grid b{color:#315773;font-size:9px}.switch-grid small{color:#8396a5;font-size:7px}.preview-section{display:flex;flex-direction:column;gap:14px}.preview-query{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:9px}.preview-query .textarea{min-height:72px}.preview-actions{display:flex;flex-direction:column;gap:6px}.preview-query .btn{height:33px}.preview-hint{padding:15px;border:1px dashed #c7dbe8;border-radius:9px;color:#788fa2;background:#f7fbfd;text-align:center;font-size:9px}.preview-metrics,.evaluation-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}.preview-metrics>div,.evaluation-summary>div{padding:10px;border:1px solid #d9e6ee;border-radius:9px;display:flex;align-items:baseline;justify-content:space-between;background:#f6fafc}.preview-metrics strong,.evaluation-summary strong{color:#1769a9;font-size:15px}.preview-metrics span,.evaluation-summary span{color:#7c91a2;font-size:7px}.evaluation-summary>div{border-color:#c9e4d6;background:#f0faf5}.evaluation-summary strong{color:#167655}.preview-standalone{padding:10px;border-left:3px solid #2291b8;border-radius:4px 8px 8px 4px;display:flex;flex-direction:column;gap:5px;color:#4d6e85;background:#eff8fc;font-size:9px}.preview-standalone b{color:#1f628d}.evidence-list{display:flex;flex-direction:column;gap:8px}.evidence-list article{padding:11px;border:1px solid #d9e5ed;border-radius:9px;background:#fff;box-shadow:0 3px 10px rgba(25,70,103,.04)}.evidence-list header{display:flex;justify-content:space-between;gap:8px;color:#235271;font-size:9px}.evidence-list header span{color:#16815e}.evidence-list p{max-height:120px;margin:7px 0;overflow:auto;color:#4e687b;font-size:9px;line-height:1.65;white-space:pre-wrap}.evidence-list small{color:#8598a7;font-size:7px}.prompt-preview{border:1px solid #d7e4ec;border-radius:9px;overflow:hidden}.prompt-preview summary{padding:10px;color:#2a607f;background:#f2f8fb;font-size:9px;font-weight:700;cursor:pointer}.prompt-preview pre{max-height:320px;margin:0;padding:13px;overflow:auto;color:#355267;background:#fbfdfe;font-size:9px;line-height:1.65;white-space:pre-wrap}.factory-modal{height:min(760px,calc(100vh - 56px))}@media(max-width:760px){.factory-settings-layout{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.settings-tabs{padding:7px;border-right:0;border-bottom:1px solid #dfe9f0;display:grid;grid-template-columns:repeat(3,1fr)}.settings-tabs>button{padding:7px}.settings-tabs small{display:none}.settings-section.form-grid{grid-template-columns:1fr}.settings-section .full{grid-column:auto}.switch-grid{grid-template-columns:1fr}.preview-metrics,.evaluation-summary{grid-template-columns:repeat(2,1fr)}}
</style>

<style scoped>
.group-nav-item .group-select{padding-right:8px}
.group-nav-item .group-select b{display:block}
.group-nav-item:hover .group-select b,.group-nav-item.active .group-select b{visibility:hidden}
</style>

<style>
.group-modal{position:relative;width:min(520px,calc(100vw - 44px));overflow:hidden;border:1px solid #bdd4e5;border-radius:16px;background:#fff;box-shadow:0 30px 90px rgba(8,31,52,.32)}.group-modal>header{padding:18px 20px;border-bottom:1px solid #e0e9f0;display:flex;align-items:flex-start;justify-content:space-between;gap:18px;background:linear-gradient(120deg,#f8fbfe,#eef7fd)}.group-modal>header span{color:#3484bd;font-size:8px;letter-spacing:1.4px}.group-modal>header h2{margin:4px 0;color:#173e63;font-size:17px}.group-modal>header p{margin:0;color:#7b90a3;font-size:9px;line-height:1.5}.group-modal>header>button{width:31px;height:31px;flex:0 0 31px;padding:0;border:1px solid #ccdce8;border-radius:8px;display:grid;place-items:center;color:#668096;background:#fff}.group-modal-body{padding:20px;display:grid;gap:14px}.group-modal-body .textarea{min-height:86px}.group-appearance{display:grid;grid-template-columns:1fr 1fr;gap:14px}.color-picker{height:37px;padding:0 9px;border:1px solid #cbd9e6;border-radius:7px;display:flex;align-items:center;gap:9px}.color-picker input{width:23px;height:23px;padding:0;border:0;background:transparent}.color-picker code{color:#526d84;font-size:10px}.group-modal>footer{padding:13px 20px;border-top:1px solid #e0e9f0;display:grid;grid-template-columns:auto 1fr auto auto;gap:8px;background:#f8fbfd}@media(max-width:560px){.group-appearance{grid-template-columns:1fr}.group-modal>footer{grid-template-columns:1fr 1fr}.group-modal>footer span{display:none}}
</style>

<style scoped>
.factory-layout{display:grid;grid-template-columns:224px minmax(0,1fr);gap:16px;align-items:start}.group-sidebar{position:sticky;top:82px;max-height:calc(100vh - 108px);overflow:hidden;border:1px solid #d9e5ef;border-radius:14px;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;background:#f8fbfe;box-shadow:0 5px 18px rgba(23,61,94,.045)}.group-sidebar>header{height:54px;padding:0 13px;border-bottom:1px solid #e1eaf1;display:flex;align-items:center;justify-content:space-between;background:#fff}.group-sidebar>header strong,.group-sidebar>header span{display:block}.group-sidebar>header strong{color:#1c4265;font-size:12px}.group-sidebar>header span{margin-top:3px;color:#8a9baa;font-size:8px}.group-sidebar>header button,.compact-icon-btn{width:29px;height:29px;padding:0;border:1px solid #d4e1eb;border-radius:7px;display:grid;place-items:center;color:#47708f;background:#f8fbfe}.group-sidebar>header button:hover,.compact-icon-btn:hover{color:#1769c2;border-color:#9fc5e3;background:#eef7ff}.group-search,.agent-search{display:flex;align-items:center;gap:6px;color:#7890a3;background:#fff}.group-search{height:34px;margin:10px 10px 3px;padding:0 8px;border:1px solid #d7e3ec;border-radius:7px}.group-search input,.agent-search input{min-width:0;width:100%;border:0;outline:0;color:#294d6b;background:transparent;font-size:9px}.group-navigation{padding:8px;overflow-y:auto;overscroll-behavior:contain}.special-group,.group-select{width:100%;min-width:0;border:0;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;align-items:center;color:#47647c;background:transparent;text-align:left}.special-group{min-height:44px;padding:6px 8px;border-radius:8px}.special-group:hover,.special-group.active{color:#1769c2;background:#eaf4fc}.special-group>span:nth-child(2),.group-select>span:nth-child(2){min-width:0}.special-group strong,.special-group small,.group-select strong,.group-select small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.special-group strong,.group-select strong{font-size:10px}.special-group small,.group-select small{margin-top:3px;color:#8b9cab;font-size:7px}.special-group b,.group-select b{min-width:21px;padding:3px 5px;border-radius:99px;color:#657f94;background:#e8eff4;text-align:center;font-size:8px}.nav-folder{width:28px;height:28px;border-radius:7px;display:grid;place-items:center}.nav-folder.all{color:#1769c2;background:#dfeffc}.nav-folder.ungrouped{color:#73879a;background:#e9eef3}.group-nav-item{position:relative;margin:2px 0;border-radius:8px}.group-nav-item:hover,.group-nav-item.active{background:#fff;box-shadow:0 2px 8px rgba(25,67,103,.07)}.group-nav-item.active{box-shadow:inset 2px 0 #2680ca,0 2px 8px rgba(25,67,103,.07)}.group-select{min-height:47px;padding:6px 29px 6px 8px;border-radius:8px}.group-edit{position:absolute;right:4px;top:6px;width:24px;height:24px;padding:0;border:0;border-radius:6px;display:none;place-items:center;color:#71889a;background:transparent}.group-nav-item:hover .group-edit,.group-nav-item.active .group-edit{display:grid}.group-edit:hover{color:#1769c2;background:#e6f2fb}.group-select b{display:none}.group-search-empty{padding:20px 8px;color:#8a9cac;text-align:center;font-size:8px}.group-sidebar>footer{padding:10px;border-top:1px solid #e1eaf1;display:flex;align-items:center;gap:6px;color:#8297a8;background:#fff;font-size:8px}.agent-browser{min-width:0;min-height:440px;border:1px solid #d9e5ef;border-radius:14px;background:rgba(247,251,254,.72)}.browser-header{min-height:66px;padding:12px 16px;border-bottom:1px solid #dce7ef;display:flex;align-items:center;justify-content:space-between;gap:18px;background:#fff;border-radius:14px 14px 0 0}.current-group{min-width:0;display:flex;align-items:center;gap:10px}.current-group>span{width:39px;height:39px;flex:0 0 39px;border-radius:10px;display:grid;place-items:center}.current-group h2{margin:0;color:#173e63;font-size:14px}.current-group h2 small{display:inline-grid;min-width:20px;height:20px;margin-left:5px;place-items:center;border-radius:99px;color:#657f94;background:#e9f0f5;font-size:8px}.current-group p{margin:4px 0 0;overflow:hidden;color:#7a8fa1;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.browser-actions{display:flex;align-items:center;gap:7px}.agent-search{width:220px;height:33px;padding:0 9px;border:1px solid #d2e0ea;border-radius:8px;background:#f8fbfd}.status-agent-sections{padding:14px;display:grid;gap:14px}.status-agent-section{overflow:hidden;border:1px solid #dbe6ee;border-radius:12px;background:#f8fbfd}.status-agent-section>header{min-height:58px;padding:10px 12px;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid #e2ebf1;background:#fff}.status-section-heading{min-width:0;display:flex;align-items:center;gap:9px}.status-section-heading>span{width:32px;height:32px;flex:0 0 32px;display:grid;place-items:center;border-radius:8px;color:#177a53;background:#e8f7f0}.status-section-candidate .status-section-heading>span{color:#9a650f;background:#fff2d7}.status-section-archived .status-section-heading>span{color:#687d8f;background:#eaf0f4}.status-section-heading h3{margin:0;color:#1e4567;font-size:12px}.status-section-heading h3 small{display:inline-grid;min-width:19px;height:19px;margin-left:4px;place-items:center;border-radius:99px;color:#667f94;background:#e9f0f5;font-size:8px}.status-section-heading p{margin:3px 0 0;color:#8194a4;font-size:8px}.status-agent-section>header>button{width:29px;height:29px;padding:0;border:1px solid #d7e3eb;border-radius:7px;display:grid;place-items:center;color:#5c7890;background:#f8fbfd}.status-agent-section>.agent-grid{padding:12px}.status-section-empty{padding:23px;color:#8b9ba8;text-align:center;font-size:9px}.agent-browser>.factory-empty{margin:14px}.agent-card{min-height:255px}.card-classifiers{display:grid;grid-template-columns:1fr 108px;gap:6px;margin:0 0 10px}.card-group-select,.card-status-select{height:28px;margin:0;padding:0 7px;border:1px solid #dce6ed;border-radius:7px;display:flex;align-items:center;gap:5px;color:#6e879a;background:#f7fafc}.card-group-select select,.card-status-select select{width:100%;min-width:0;border:0;outline:0;color:#49677f;background:transparent;font-size:8px}.factory-empty{border:1px dashed #cfdee9;border-radius:11px;background:#fff}.factory-empty span{color:#8395a5}.delete-group-button{margin-right:auto}@media(max-width:1220px){.factory-layout{grid-template-columns:200px minmax(0,1fr)}.agent-search{width:180px}.card-classifiers{grid-template-columns:1fr}}@media(max-width:920px){.factory-layout{grid-template-columns:1fr}.group-sidebar{position:static;max-height:270px}.group-navigation{max-height:170px}.browser-header{align-items:flex-start;flex-direction:column}.browser-actions,.agent-search{width:100%}}@media(max-width:720px){.factory-layout{gap:10px}.status-agent-sections{padding:10px}.factory-summary{gap:7px}.factory-summary>div{padding:10px}}
</style>

<style scoped>
.group-nav-item .group-select{padding-right:8px}
.group-nav-item .group-select b{display:block}
.group-nav-item:hover .group-select b,.group-nav-item.active .group-select b{visibility:hidden}
</style>

<style>
.factory-modal-layer{position:fixed;inset:0;z-index:920;display:flex;align-items:center;justify-content:center;padding:28px}.factory-modal-backdrop{position:absolute;inset:0;border:0;background:rgba(8,28,48,.48);backdrop-filter:blur(5px)}.factory-modal{position:relative;display:grid;width:min(980px,calc(100vw - 56px));max-height:calc(100vh - 56px);grid-template-rows:auto minmax(0,1fr) auto;overflow:hidden;border:1px solid #bdd4e5;border-radius:16px;background:#fff;box-shadow:0 30px 90px rgba(8,31,52,.32)}.factory-modal-header{display:flex;align-items:center;justify-content:space-between;padding:17px 20px;border-bottom:1px solid #dfe9f0;background:linear-gradient(120deg,#f8fbfe,#eef7fd)}.factory-modal-header span{font-size:8px;letter-spacing:1.5px;color:#3484bd}.factory-modal-header h2{margin:3px 0;color:#173e63;font-size:17px}.factory-modal-header p{margin:0;color:#7890a5;font-size:9px}.factory-modal-header>button{display:grid;width:32px;height:32px;place-items:center;border:1px solid #ccdce8;border-radius:8px;color:#668096;background:#fff;cursor:pointer}.factory-modal-body{padding:20px;overflow:auto}.factory-modal-body .option-block{padding:10px;border:1px solid #e1eaf1;border-radius:9px;background:#f9fbfd}.factory-modal-body .option-block .btn{margin:3px}.factory-modal-footer{display:flex;justify-content:flex-end;gap:8px;padding:13px 20px;border-top:1px solid #e0e9f0;background:#f8fbfd}.factory-modal-enter-active,.factory-modal-leave-active{transition:opacity .18s ease}.factory-modal-enter-from,.factory-modal-leave-to{opacity:0}.factory-modal-enter-from .factory-modal,.factory-modal-leave-to .factory-modal{transform:translateY(12px) scale(.985)}@media(max-width:720px){.factory-modal-layer{padding:0}.factory-modal{width:100vw;max-height:100vh;height:100vh;border-radius:0}.factory-modal-body.form-grid{grid-template-columns:1fr}.factory-modal-body .field.full{grid-column:auto}}
</style>
