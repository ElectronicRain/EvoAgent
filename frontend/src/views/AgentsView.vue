<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Archive, Bot, Boxes, CheckCircle2, ChevronDown, ChevronRight, CirclePlus,
  FlaskConical, Folder, FolderPlus, MessagesSquare, Pencil, Save,
  Search, Settings2, Sparkles, Trash2, Users, Wrench, X,
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
const policies = ref<Entity[]>([])
const endpoints = ref<Entity[]>([])
const extensions = ref<Entity[]>([])
const creating = ref(false)
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
  group_id: '',
  model: 'demo-model', temperature: 0.3, tools: [] as string[], skills: [] as string[],
  mcp_extensions: [] as string[], knowledge_bases: [] as string[],
  approval_policy_id: '', security_profile: 'default',
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
    [agents.value, groups.value, tools.value, skills.value, bases.value, policies.value, endpoints.value, extensions.value] = await Promise.all([
      api.get('/agents'), api.get('/agent-groups'), api.get('/tools'), api.get('/skills'), api.get('/knowledge-bases'),
      api.get('/approval-policies'), api.get('/model-endpoints'), api.get('/extensions'),
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
  Object.assign(form, {
    name: '', slug: '', description: '', system_prompt: '', model_endpoint_id: '',
    group_id: groups.value.some(group => group.id === activeGroupId.value) ? activeGroupId.value : '',
    model: 'demo-model', temperature: 0.3,
    tools: tools.value.map(item => item.name),
    skills: skills.value.filter(item => item.enabled).map(item => item.id),
    mcp_extensions: extensions.value.filter(item => item.kind === 'mcp' && item.enabled).map(item => item.id),
    knowledge_bases: [],
    approval_policy_id: policies.value.find(item => item.is_default)?.id || '',
    security_profile: 'default',
  })
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
  const permissions = parseObject(agent.permissions_json)
  Object.assign(form, {
    name: agent.name,
    slug: agent.slug,
    description: agent.description,
    system_prompt: agent.system_prompt,
    model_endpoint_id: agent.model_endpoint_id || '',
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
  })
  editingAgentId.value = agent.id
  creating.value = true
}
function validateAgentForm() {
  if (form.name.trim().length < 2) return 'Agent 名称至少需要 2 个字符'
  if (!/^[a-z0-9][a-z0-9_-]{1,99}$/.test(form.slug)) return '唯一标识需使用小写字母、数字、下划线或连字符，且至少 2 个字符'
  if (form.system_prompt.trim().length < 10) return '系统提示词至少需要 10 个字符'
  return ''
}
async function saveAgent() {
  const validation = validateAgentForm()
  if (validation) return app.notify(validation, 'error')
  app.loading(true)
  try {
    const payload = {
      name: form.name,
      description: form.description,
      system_prompt: form.system_prompt,
      model_endpoint_id: form.model_endpoint_id || null,
      group_id: form.group_id || null,
      model: form.model,
      temperature: form.temperature,
      tools: Array.from(new Set([...form.tools, 'exec'])),
      skills: form.skills,
      knowledge_bases: form.knowledge_bases,
      provider: form.model_endpoint_id ? 'openai-compatible' : 'demo',
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
          <div class="factory-modal-body form-grid">
            <div class="field"><label>名称</label><input v-model="form.name" class="input" placeholder="例如：文献综述 Agent"></div>
            <div class="field"><label>唯一标识</label><input v-model="form.slug" class="input" :disabled="!!editingAgentId" placeholder="literature-reviewer"><span v-if="editingAgentId" class="field-help">唯一标识用于 Agent 联动，保存后不可修改。</span></div>
            <div class="field full"><label>职责说明</label><input v-model="form.description" class="input" placeholder="概括这个 Agent 负责解决的问题"></div>
            <div class="field full"><label>系统提示词</label><textarea v-model="form.system_prompt" class="textarea" placeholder="明确角色、工作边界、输出规范和引用要求。" /></div>
            <div class="field"><label>所属分组</label><select v-model="form.group_id" class="select"><option value="">未分组</option><option v-for="item in groups" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
            <div class="field"><label>大模型 API 接口</label><select v-model="form.model_endpoint_id" class="select"><option value="">离线演示模型</option><option v-for="item in endpoints.filter(endpoint => endpoint.enabled)" :key="item.id" :value="item.id">{{ item.name }} / {{ item.default_model }}</option></select></div>
            <div class="field"><label>模型名覆盖</label><input v-model="form.model" class="input"><span class="field-help">绑定 Endpoint 时默认使用接口中的模型。</span></div>
            <div class="field"><label>审批策略</label><select v-model="form.approval_policy_id" class="select"><option v-for="item in policies" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
            <div class="field"><label>默认安全策略</label><select v-model="form.security_profile" class="select"><option v-for="item in securityProfiles" :key="item.value" :value="item.value">{{ item.label }}</option></select><span class="field-help">对话时仍可临时切换。</span></div>
            <div class="field"><label>Temperature</label><input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" class="input"></div>
            <div class="field full option-block"><label>工具权限</label><div><button v-for="item in tools" :key="item.name" class="btn btn-sm" :disabled="item.name==='exec'" :title="item.name==='exec'?'每个 Agent 固有的命令执行能力，由安全策略约束':''" :class="{ 'btn-primary': item.name==='exec' || form.tools.includes(item.name) }" @click="item.name!=='exec' && toggle(form.tools,item.name)"><Wrench :size="13" />{{ item.name }}<template v-if="item.name==='exec'"> · 固有</template></button></div></div>
            <div class="field full option-block"><label>MCP 服务</label><div><button v-for="item in extensions.filter(extension=>extension.kind==='mcp' && extension.enabled)" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.mcp_extensions.includes(item.id) }" @click="toggle(form.mcp_extensions,item.id)">{{ item.name }}</button></div><span class="field-help">选中的 MCP 工具会直接加入 Agent 的模型工具列表。</span></div>
            <div class="field full option-block"><label>Skills</label><div><button v-for="item in skills" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.skills.includes(item.id) }" @click="toggle(form.skills,item.id)">{{ item.name }}</button></div></div>
            <div class="field full option-block"><label>知识库</label><div><button v-for="item in bases" :key="item.id" class="btn btn-sm" :class="{ 'btn-primary': form.knowledge_bases.includes(item.id) }" @click="toggle(form.knowledge_bases,item.id)">{{ item.name }}</button></div></div>
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
