<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { WebviewWindow } from '@tauri-apps/api/webviewWindow'
import {
  AlertTriangle, BookOpen, Check, CheckCircle2, ChevronRight, Circle,
  Database, FileText, FolderInput, FolderTree, Layers3, LoaderCircle,
  Plus, Search, Settings2, Sparkles, Trash2, X,
} from 'lucide-vue-next'
import FloatingPanel from '../components/FloatingPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import { api, type Entity } from '../services/api'
import {
  importKnowledgeFiles,
  KNOWLEDGE_FILE_ACCEPT,
  selectKnowledgeFiles,
  type KnowledgeFileSelection,
  type KnowledgeImportProgress,
} from '../services/knowledgeFiles'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const bases = ref<Entity[]>([])
const groups = ref<Entity[]>([])
const selected = ref<Entity | null>(null)
const activeGroupId = ref('all')
const searchScope = ref<'all' | 'group' | 'base'>('all')
const searchGroupId = ref('')
const answer = ref('')
const results = ref<Entity[]>([])
const citations = ref<Entity[]>([])
const trace = ref<Entity | null>(null)
const rewrittenQueries = ref<string[]>([])
const queryRunning = ref(false)
const queryAttempted = ref(false)
const queryError = ref('')
type RetrievalStatus = 'pending' | 'running' | 'complete' | 'error'
type RetrievalStep = {
  id: string
  title: string
  description: string
  status: RetrievalStatus
  detail: string
}
const retrievalSteps = ref<RetrievalStep[]>([])
const search = ref('二维结构化网格质量应如何评价？')
const createBase = ref(false)
const showConfig = ref(false)
const showGroupEditor = ref(false)
const editingGroupId = ref('')
const movingBase = ref<Entity | null>(null)
const moveGroupIds = ref<string[]>([])
const deletingBase = ref<Entity | null>(null)
const baseForm = reactive({ name: '', discipline: '', description: '' })
const initialSourceType = ref<'none' | 'files' | 'folder' | 'web' | 'database' | 'api'>('none')
const initialSourceOptions = [
  { id: 'none', label: '暂不添加' },
  { id: 'files', label: 'PPT / PDF / Word' },
  { id: 'folder', label: '整个文件夹' },
  { id: 'web', label: '网页' },
  { id: 'database', label: '数据库' },
  { id: 'api', label: 'API / 第三方' },
] as const
const initialFileSelection = ref<KnowledgeFileSelection>({ files: [], skipped: [], rootName: '' })
const initialImportProgress = reactive<KnowledgeImportProgress>({
  total: 0, completed: 0, imported: 0, duplicates: 0, failed: 0, skipped: 0, current: '',
})
const initialSource = reactive({
  name: '', url: '', max_pages: 1, method: 'GET', headers: '{}', response_path: '',
  connection_url: '', query: 'SELECT * FROM your_table', row_limit: 5000,
})
const groupForm = reactive({ name: '', description: '', color: '#1769c2', knowledge_base_ids: [] as string[] })
const providerConfig = reactive<Entity>({
  embedding_base_url: '', embedding_model: '', rerank_base_url: '', rerank_model: '',
  api_key: '', llm_endpoint_id: null, top_k: 6, candidate_k: 30, context_char_budget: 12000,
})
const endpoints = ref<Entity[]>([])
const filteredBases = computed(() => {
  if (activeGroupId.value === 'all') return bases.value
  const group = groups.value.find(item => item.id === activeGroupId.value)
  const ids = new Set(group?.knowledge_base_ids || [])
  return bases.value.filter(item => ids.has(item.id))
})
const totalDocuments = computed(() => bases.value.reduce((sum, item) => sum + Number(item.document_count || 0), 0))
const ungroupedCount = computed(() => bases.value.filter(item => !groupsForBase(item.id).length).length)
const activeGroupName = computed(() => activeGroupId.value === 'all'
  ? '全部知识库'
  : groups.value.find(item => item.id === activeGroupId.value)?.name || '全部知识库')
const scopeLabel = computed(() => {
  if (searchScope.value === 'base') return selected.value?.name || '当前知识库'
  if (searchScope.value === 'group') return groups.value.find(item => item.id === searchGroupId.value)?.name || '指定分组'
  return '全部知识库'
})
const expandedListItems = computed(() => (trace.value?.list_contexts || [])
  .reduce((sum: number, item: Entity) => sum + Number(item.item_count || 0), 0))

function freshRetrievalSteps(): RetrievalStep[] {
  return [
    { id: 'scope', title: '确认检索范围', description: '解析知识库与分组', status: 'pending', detail: '等待开始' },
    { id: 'rewrite', title: '查询改写', description: '生成互补检索式', status: 'pending', detail: '等待上一步' },
    { id: 'retrieve', title: '混合召回与融合', description: 'Dense + BM25 + RRF', status: 'pending', detail: '等待上一步' },
    { id: 'rerank', title: '相关性重排', description: '筛选高质量证据', status: 'pending', detail: '等待上一步' },
    { id: 'answer', title: '组织证据与回答', description: '扩展父块并保留引用', status: 'pending', detail: '等待上一步' },
  ]
}

function updateRetrievalStep(id: string, status: RetrievalStatus, detail?: string) {
  const step = retrievalSteps.value.find(item => item.id === id)
  if (!step) return
  step.status = status
  if (detail) step.detail = detail
}

function startRetrievalStep(id: string, detail?: string) {
  for (const step of retrievalSteps.value) {
    if (step.status === 'running' && step.id !== id) step.status = 'complete'
  }
  updateRetrievalStep(id, 'running', detail)
}

function finishRetrievalFromResult(result: Entity) {
  const emptyScope = result.trace?.scope === 'empty'
  const noEvidence = !(result.chunks || []).length
  for (const step of retrievalSteps.value) {
    if (step.status === 'pending' || step.status === 'running') {
      step.status = 'complete'
      step.detail = emptyScope
        ? '所选范围暂无知识库'
        : noEvidence ? '未找到可用证据，流程已结束' : '已完成'
    }
  }
}

function handleRetrievalEvent(step: Entity) {
  switch (step.type) {
    case 'stream_connected':
      startRetrievalStep('scope', `正在解析“${scopeLabel.value}”`)
      break
    case 'scope_resolved':
      updateRetrievalStep('scope', 'complete', step.scope === 'all'
        ? '已启用全部知识库'
        : `已锁定 ${step.knowledge_base_count} 个知识库`)
      startRetrievalStep('rewrite', '正在理解问题与检索意图')
      break
    case 'query_rewrite_started':
      startRetrievalStep('rewrite', '正在生成互补检索式')
      break
    case 'query_rewritten':
      rewrittenQueries.value = step.queries || []
      updateRetrievalStep('rewrite', 'complete', `生成 ${step.query_count || 1} 条检索式`)
      startRetrievalStep('retrieve', '正在执行向量与关键词并行召回')
      break
    case 'hybrid_retrieval_started':
      startRetrievalStep('retrieve', `正在调用 ${step.embedding_model || 'Embedding'}`)
      break
    case 'hybrid_retrieval_completed':
      updateRetrievalStep('retrieve', 'running', `Dense ${step.dense_candidates || 0} · BM25 ${step.lexical_candidates || 0} · 正在融合`)
      break
    case 'fusion_completed':
      updateRetrievalStep('retrieve', 'complete', `RRF 融合后保留 ${step.fused_candidates || 0} 个候选`)
      startRetrievalStep('rerank', '正在计算候选证据相关性')
      break
    case 'rerank_started':
      startRetrievalStep('rerank', `正在重排 ${step.candidate_count || 0} 个候选`)
      break
    case 'rerank_completed':
      updateRetrievalStep('rerank', 'complete', `重排 ${step.reranked || 0} 个 · 选中 ${step.selected || 0} 个`)
      startRetrievalStep('answer', '正在扩展父块并组织引用')
      break
    case 'context_assembled':
      updateRetrievalStep(
        'answer',
        'running',
        `上下文 ${step.context_chars || 0} 字符 · ${step.citation_count || 0} 条引用${step.numbered_list_items ? ` · 完整展开 ${step.numbered_list_items} 个列表项` : ''}`,
      )
      break
    case 'answer_generation_started':
      startRetrievalStep('answer', '正在依据证据生成回答')
      break
    case 'answer_generated':
      updateRetrievalStep('answer', 'complete', `回答 ${step.answer_chars || 0} 字符 · ${step.citation_count || 0} 条引用`)
      break
    case 'knowledge_waiting': {
      const current = retrievalSteps.value.find(item => item.status === 'running')
      if (current) current.detail = `${current.detail.replace(/ · 已等待 \d+ 秒$/, '')} · 已等待 ${step.elapsed_seconds} 秒`
      break
    }
  }
}

function groupsForBase(baseId: string) {
  return groups.value.filter(group => (group.knowledge_base_ids || []).includes(baseId))
}

async function openKnowledgeWindow(item: Entity) {
  selected.value = item
  const routeUrl = `/#/knowledge/${item.id}`
  if ('__TAURI_INTERNALS__' in window) {
    const label = `knowledge-${String(item.id).replace(/[^a-zA-Z0-9-]/g, '-')}`
    const existing = await WebviewWindow.getByLabel(label)
    if (existing) {
      await existing.setFocus()
      return
    }
    const detailWindow = new WebviewWindow(label, {
      url: routeUrl,
      title: `EvoAgent · ${item.name}`,
      width: 1380,
      height: 860,
      minWidth: 980,
      minHeight: 680,
      center: true,
      resizable: true,
    })
    detailWindow.once('tauri://error', () => window.open(routeUrl, '_blank'))
    return
  }
  window.open(routeUrl, '_blank', 'width=1380,height=860')
}

async function load() {
  store.loading(true)
  try {
    bases.value = await api.get('/knowledge-bases')
    selected.value = bases.value.find(item => item.id === selected.value?.id) || bases.value[0] || null
    const [config, modelEndpoints, groupRows] = await Promise.all([
      api.get<Entity>('/knowledge/config'),
      api.get<Entity[]>('/model-endpoints'),
      api.get<Entity[]>('/knowledge-groups'),
    ])
    Object.assign(providerConfig, config, { api_key: '' })
    endpoints.value = modelEndpoints
    groups.value = groupRows
    if (!searchGroupId.value && groupRows.length) searchGroupId.value = groupRows[0].id
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function selectGroupFilter(groupId: string) {
  activeGroupId.value = groupId
  const target = groupId === 'all'
    ? bases.value[0]
    : bases.value.find(item => groups.value.find(group => group.id === groupId)?.knowledge_base_ids?.includes(item.id))
  if (target) selected.value = target
}

function openNewGroup() {
  editingGroupId.value = ''
  Object.assign(groupForm, { name: '', description: '', color: '#1769c2', knowledge_base_ids: [] })
  showGroupEditor.value = true
}

function openEditGroup(group: Entity) {
  editingGroupId.value = group.id
  Object.assign(groupForm, {
    name: group.name,
    description: group.description || '',
    color: group.color || '#1769c2',
    knowledge_base_ids: [...(group.knowledge_base_ids || [])],
  })
  showGroupEditor.value = true
}

async function saveGroup() {
  store.loading(true)
  try {
    if (editingGroupId.value) {
      await api.patch(`/knowledge-groups/${editingGroupId.value}`, {
        name: groupForm.name, description: groupForm.description, color: groupForm.color,
      })
      await api.put(`/knowledge-groups/${editingGroupId.value}/members`, {
        knowledge_base_ids: groupForm.knowledge_base_ids,
      })
    } else {
      await api.post('/knowledge-groups', groupForm)
    }
    showGroupEditor.value = false
    await load()
    store.notify('知识库分组已保存')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function removeGroup(group: Entity) {
  if (!window.confirm(`删除分组“${group.name}”？知识库和资料不会被删除。`)) return
  store.loading(true)
  try {
    await api.delete(`/knowledge-groups/${group.id}`)
    if (activeGroupId.value === group.id) activeGroupId.value = 'all'
    if (searchGroupId.value === group.id) searchGroupId.value = ''
    await load()
    store.notify('分组已删除，知识库资料保持不变')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function openMoveBase(item: Entity) {
  movingBase.value = item
  moveGroupIds.value = groupsForBase(item.id).map(group => group.id)
}

async function saveBaseGroups() {
  if (!movingBase.value) return
  const baseId = movingBase.value.id
  const targetIds = new Set(moveGroupIds.value)
  const changedGroups = groups.value.filter(group => {
    const currentlyIncluded = (group.knowledge_base_ids || []).includes(baseId)
    return currentlyIncluded !== targetIds.has(group.id)
  })
  store.loading(true)
  try {
    await Promise.all(changedGroups.map(group => {
      const members = new Set<string>(group.knowledge_base_ids || [])
      if (targetIds.has(group.id)) members.add(baseId)
      else members.delete(baseId)
      return api.put(`/knowledge-groups/${group.id}/members`, {
        knowledge_base_ids: [...members],
      })
    }))
    const name = movingBase.value.name
    movingBase.value = null
    await load()
    store.notify(changedGroups.length ? `“${name}”的所属分组已更新` : '所属分组没有变化')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function requestDeleteBase(item: Entity) {
  deletingBase.value = item
}

async function deleteBase() {
  if (!deletingBase.value) return
  const item = deletingBase.value
  store.loading(true)
  try {
    await api.delete(`/knowledge-bases/${item.id}`)
    if (selected.value?.id === item.id) selected.value = null
    if (searchScope.value === 'base') searchScope.value = 'all'
    deletingBase.value = null
    await load()
    store.notify(`知识库“${item.name}”及其索引已删除`)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function saveBase() {
  if (
    (initialSourceType.value === 'files' || initialSourceType.value === 'folder')
    && !initialFileSelection.value.files.length
  ) {
    store.notify('请选择至少一个支持的文档', 'error')
    return
  }
  store.loading(true)
  try {
    const item = await api.post<Entity>('/knowledge-bases', baseForm)
    let importSummary = ''
    if (initialSourceType.value === 'files' || initialSourceType.value === 'folder') {
      const result = await importKnowledgeFiles(item.id, initialFileSelection.value, progress => {
        Object.assign(initialImportProgress, progress)
      })
      importSummary = `导入 ${result.imported}，重复 ${result.duplicates}，失败 ${result.failed}，跳过 ${result.skipped}`
    } else if (initialSourceType.value === 'web') {
      await api.post(`/knowledge-bases/${item.id}/sources/web`, {
        name: initialSource.name || '初始网页', url: initialSource.url,
        max_pages: initialSource.max_pages, sync_now: true,
      })
    } else if (initialSourceType.value === 'database') {
      await api.post(`/knowledge-bases/${item.id}/sources/database`, {
        name: initialSource.name || '初始数据库', connection_url: initialSource.connection_url,
        query: initialSource.query, row_limit: initialSource.row_limit, sync_now: true,
      })
    } else if (initialSourceType.value === 'api') {
      await api.post(`/knowledge-bases/${item.id}/sources/api`, {
        name: initialSource.name || '初始 API', url: initialSource.url,
        method: initialSource.method, headers: JSON.parse(initialSource.headers || '{}'),
        response_path: initialSource.response_path, sync_now: true,
      })
    }
    createBase.value = false
    await load()
    store.notify(importSummary ? `知识库已创建；${importSummary}` : '知识库及初始数据源已创建')
    await openKnowledgeWindow(item)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

function selectInitialFiles(event: Event) {
  const input = event.target as HTMLInputElement
  initialFileSelection.value = selectKnowledgeFiles(input.files || [])
}

function selectInitialFolder(event: Event) {
  const input = event.target as HTMLInputElement
  initialFileSelection.value = selectKnowledgeFiles(input.files || [])
}

async function saveConfig() {
  const payload: Entity = { ...providerConfig }
  delete payload.id
  delete payload.created_at
  delete payload.updated_at
  delete payload.has_api_key
  if (!payload.api_key) delete payload.api_key
  store.loading(true)
  try {
    const saved = await api.put<Entity>('/knowledge/config', payload)
    Object.assign(providerConfig, saved, { api_key: '' })
    showConfig.value = false
    store.notify('知识库模型配置已保存；已有资料可点击重新向量化')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function testConfig() {
  store.loading(true)
  try {
    const result = await api.post<Entity>('/knowledge/config/test')
    const mode = result.status === 'healthy' ? 'SiliconFlow 在线模式' : '本地降级模式'
    store.notify(`${mode}：Embedding ${result.dimensions} 维，Rerank 正常`)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function ask() {
  if (!search.value.trim()) return
  if (searchScope.value === 'group' && !searchGroupId.value) {
    store.notify('请先选择一个知识库分组', 'error')
    return
  }
  store.loading(true)
  queryRunning.value = true
  queryAttempted.value = true
  answer.value = ''
  results.value = []
  citations.value = []
  trace.value = null
  rewrittenQueries.value = []
  queryError.value = ''
  retrievalSteps.value = freshRetrievalSteps()
  try {
    let response: Entity | null = null
    let streamError = ''
    await api.stream('/knowledge/query/stream', {
      query: search.value,
      knowledge_base_ids: searchScope.value === 'base' && selected.value ? [selected.value.id] : [],
      knowledge_group_ids: searchScope.value === 'group' && searchGroupId.value ? [searchGroupId.value] : [],
      generate_answer: true,
    }, event => {
      if (event.type === 'step') handleRetrievalEvent(event.step || {})
      if (event.type === 'knowledge_result') response = event.result
      if (event.type === 'error') streamError = event.message || '知识检索失败'
    })
    if (streamError) throw new Error(streamError)
    const result = response as Entity | null
    if (!result) throw new Error('知识检索未返回结果，请重试')
    answer.value = result.answer
    results.value = result.chunks || []
    citations.value = result.citations || []
    trace.value = result.trace
    rewrittenQueries.value = result.rewritten_queries || [search.value]
    finishRetrievalFromResult(result)
  } catch (error: any) {
    queryError.value = error.message
    const current = retrievalSteps.value.find(item => item.status === 'running')
    if (current) {
      current.status = 'error'
      current.detail = error.message
    }
    store.notify(error.message, 'error')
  } finally {
    queryRunning.value = false
    store.loading(false)
  }
}

onMounted(load)
</script>

<template>
  <PageHeader eyebrow="TRUSTED RAG" title="学科知识库" description="多源接入、结构化切分、混合向量检索与可追溯问答。">
    <button class="btn" @click="showConfig = true"><Settings2 :size="15" />模型配置</button>
    <button class="btn btn-primary" @click="createBase = true"><Plus :size="15" />新建知识库</button>
  </PageHeader>

  <section class="knowledge-summary">
    <div><span class="summary-icon blue"><Database :size="18" /></span><p><b>{{ bases.length }}</b><small>知识库</small></p></div>
    <div><span class="summary-icon violet"><FileText :size="18" /></span><p><b>{{ totalDocuments }}</b><small>已入库资料</small></p></div>
    <div><span class="summary-icon green"><FolderTree :size="18" /></span><p><b>{{ groups.length }}</b><small>知识分组</small></p></div>
    <div><span class="summary-icon amber"><Layers3 :size="18" /></span><p><b>{{ ungroupedCount }}</b><small>待整理知识库</small></p></div>
  </section>

  <section class="card group-bar">
    <div class="card-header">
      <div><h2>按分组浏览</h2><p>知识库可同时加入多个分组，移动时不会复制或删除资料。</p></div>
      <button class="btn btn-sm" @click="openNewGroup"><Plus :size="14" />新建分组</button>
    </div>
    <div class="card-body group-pills">
      <button :class="{active:activeGroupId==='all'}" @click="selectGroupFilter('all')"><FolderTree :size="14" />全部知识库 <span>{{ bases.length }}</span></button>
      <button v-for="group in groups" :key="group.id" :class="{active:activeGroupId===group.id}" :style="{'--group-color':group.color}" @click="selectGroupFilter(group.id)">
        <i :style="{background:group.color}"></i>{{ group.name }} <span>{{ group.knowledge_base_count }}</span>
        <em title="编辑分组" @click.stop="openEditGroup(group)">设置</em><Trash2 :size="13" title="删除分组" @click.stop="removeGroup(group)" />
      </button>
    </div>
  </section>

  <section class="card base-library">
    <div class="card-header">
      <div><h2>{{ activeGroupName }}</h2><p>在卡面内滚动浏览；打开知识库可管理文档、分块、向量与数据源。</p></div>
      <span class="count-badge">{{ filteredBases.length }} 个</span>
    </div>
    <div class="card-body knowledge-grid" role="region" :aria-label="`${activeGroupName}列表`" tabindex="0">
      <article v-for="item in filteredBases" :key="item.id" class="knowledge-card" :class="{ active: selected?.id === item.id }">
        <button class="knowledge-card-main" @click="openKnowledgeWindow(item)">
          <span class="knowledge-card-icon"><Database :size="21" /></span>
          <span class="knowledge-card-copy">
            <small>{{ item.discipline || '通用' }}</small>
            <strong>{{ item.name }}</strong>
            <em>{{ item.description || '暂无知识库说明' }}</em>
          </span>
          <ChevronRight :size="18" />
        </button>
        <div class="knowledge-card-meta">
          <span><FileText :size="13" />{{ item.document_count }} 份资料</span>
          <div class="base-groups"><span v-for="group in groupsForBase(item.id)" :key="group.id" :style="{borderColor:group.color,color:group.color}">{{ group.name }}</span><span v-if="!groupsForBase(item.id).length" class="ungrouped">未分组</span></div>
        </div>
        <div class="knowledge-card-actions">
          <button @click="openMoveBase(item)"><FolderInput :size="14" />移动分组</button>
          <button class="danger-link" @click="requestDeleteBase(item)"><Trash2 :size="14" />删除</button>
        </div>
      </article>
      <div v-if="!filteredBases.length" class="empty base-empty"><FolderTree :size="28" /><strong>该分组还没有知识库</strong><span>可移动现有知识库到这里，或新建一个知识库。</span></div>
    </div>
  </section>

  <FloatingPanel v-model="createBase" title="新建知识库" eyebrow="NEW KNOWLEDGE BASE" description="配置知识库基础信息，并可在创建时直接导入首批资料。" size="wide">
    <div class="form-grid">
      <div class="field"><label>名称</label><input v-model="baseForm.name" class="input"></div>
      <div class="field"><label>学科</label><input v-model="baseForm.discipline" class="input"></div>
      <div class="field full"><label>说明</label><input v-model="baseForm.description" class="input"></div>
      <div class="field full"><label>创建时加入初始数据</label><div class="initial-source-types"><button v-for="item in initialSourceOptions" :key="item.id" class="btn" :class="{'btn-primary':initialSourceType===item.id}" @click="initialSourceType=item.id">{{ item.label }}</button></div></div>
      <div v-if="initialSourceType==='files'" class="field full"><label class="upload-zone">选择一个或多个文档<input type="file" multiple :accept="KNOWLEDGE_FILE_ACCEPT" @change="selectInitialFiles"><span>已选择 {{ initialFileSelection.files.length }} 个，跳过 {{ initialFileSelection.skipped.length }} 个</span></label></div>
      <div v-else-if="initialSourceType==='folder'" class="field full"><label class="upload-zone">选择文件夹并递归导入<input type="file" multiple webkitdirectory directory @change="selectInitialFolder"><span>{{ initialFileSelection.rootName || '尚未选择' }} · 可导入 {{ initialFileSelection.files.length }} 个，跳过 {{ initialFileSelection.skipped.length }} 个</span></label><p class="folder-hint">支持递归读取子文件夹；目录层级会作为文档来源路径保留。</p></div>
      <div v-if="initialImportProgress.total" class="field full"><div class="folder-progress"><span>导入 {{ initialImportProgress.completed }}/{{ initialImportProgress.total }}</span><span>成功 {{ initialImportProgress.imported }}</span><span>重复 {{ initialImportProgress.duplicates }}</span><span>失败 {{ initialImportProgress.failed }}</span><small>{{ initialImportProgress.current }}</small></div></div>
      <template v-if="initialSourceType==='web'"><div class="field"><label>数据源名称</label><input v-model="initialSource.name" class="input"></div><div class="field"><label>抓取页数</label><input v-model.number="initialSource.max_pages" type="number" min="1" max="20" class="input"></div><div class="field full"><label>网页 URL</label><input v-model="initialSource.url" class="input"></div></template>
      <template v-else-if="initialSourceType==='database'"><div class="field full"><label>数据源名称</label><input v-model="initialSource.name" class="input"></div><div class="field full"><label>数据库连接地址</label><input v-model="initialSource.connection_url" class="input" placeholder="sqlite:///D:/data/source.db"></div><div class="field full"><label>只读查询</label><textarea v-model="initialSource.query" class="textarea"></textarea></div></template>
      <template v-else-if="initialSourceType==='api'"><div class="field"><label>数据源名称</label><input v-model="initialSource.name" class="input"></div><div class="field"><label>请求方法</label><select v-model="initialSource.method" class="input"><option>GET</option><option>POST</option></select></div><div class="field full"><label>API URL</label><input v-model="initialSource.url" class="input"></div><div class="field"><label>响应路径</label><input v-model="initialSource.response_path" class="input" placeholder="data.items"></div><div class="field full"><label>请求头 JSON</label><textarea v-model="initialSource.headers" class="textarea"></textarea></div></template>
      <div class="field full"><button class="btn btn-primary" @click="saveBase">创建知识库并导入初始数据</button></div>
    </div>
  </FloatingPanel>

  <FloatingPanel v-model="showGroupEditor" :title="editingGroupId ? '编辑知识库分组' : '新建知识库分组'" eyebrow="KNOWLEDGE GROUP" description="集中维护分组信息与知识库成员，页面内容不会被向下推移。" size="large"><div class="form-grid"><div class="field"><label>分组名称</label><input v-model="groupForm.name" class="input" placeholder="例如：计算流体力学"></div><div class="field"><label>标识颜色</label><input v-model="groupForm.color" type="color" class="input" style="height:40px"></div><div class="field full"><label>说明</label><input v-model="groupForm.description" class="input"></div><div class="field full"><label>选择分组中的知识库</label><div class="member-grid"><label v-for="item in bases" :key="item.id"><input v-model="groupForm.knowledge_base_ids" type="checkbox" :value="item.id"><span><strong>{{ item.name }}</strong><small>{{ item.discipline }} · {{ item.document_count }} 份资料</small></span></label><div v-if="!bases.length" class="empty">请先创建知识库</div></div></div><div class="field full"><button class="btn btn-primary" @click="saveGroup">保存分组与成员</button></div></div></FloatingPanel>

  <!-- Knowledge-base contents and data sources are managed in the detached detail window.
  <section v-if="selected" class="card" style="margin-top:20px">
    <div class="card-header"><h2>{{ selected.name }} · 数据源</h2><div style="display:flex;gap:8px"><button class="btn btn-sm" @click="addSource=true"><Globe2 :size="14" />网页 / 数据库 / API</button><button class="btn btn-sm" @click="addText=true"><FileText :size="14" />粘贴文本</button><label class="btn btn-sm"><Upload :size="14" />上传文档<input type="file" accept=".pdf,.docx,.pptx,.txt,.md,.csv,.json,.html" hidden @change="upload"></label><button class="btn btn-sm" @click="reindex">重新向量化</button></div></div>
    <div v-if="overview" class="knowledge-metrics"><div><strong>{{ overview.statistics.documents }}</strong><span>文档</span></div><div><strong>{{ overview.statistics.parent_chunks }}</strong><span>父块</span></div><div><strong>{{ overview.statistics.child_chunks }}</strong><span>检索子块</span></div><div><strong>{{ overview.statistics.embeddings }}</strong><span>已向量化</span></div><div><strong>{{ overview.statistics.sources }}</strong><span>数据源</span></div></div>
    <div class="table-wrap"><table><thead><tr><th>资料名称</th><th>来源</th><th>类型</th><th>状态</th><th>字符数</th><th>内部结构</th></tr></thead><tbody><tr v-for="item in documents" :key="item.id" :class="{ 'selected-row': documentDetail?.id === item.id }"><td>{{ item.title }}</td><td>{{ item.source }}</td><td>{{ item.mime_type }}</td><td>{{ item.status }}</td><td>{{ item.char_count }}</td><td><button class="btn btn-sm" @click="inspectDocument(item)">查看内容与索引</button></td></tr></tbody></table><div v-if="!documents.length" class="empty"><FileText :size="28" /><br>还没有资料</div></div>
    <div v-if="sources.length" class="card-body"><div class="list-stack"><div v-for="item in sources" :key="item.id" class="list-item"><div><strong>{{ item.name }}</strong><p>{{ item.source_type }} · {{ item.uri }}</p></div><span>{{ item.status }}</span></div></div></div>
  </section>

  <section v-if="selected && (documentDetail || overview)" class="card knowledge-inspector" style="margin-top:20px">
    <div class="card-header"><div><h2>知识库内部检查器</h2><p v-if="documentDetail">当前文档：{{ documentDetail.title }}</p></div><div class="inspector-tabs"><button :class="{active:inspectorTab==='content'}" @click="inspectorTab='content'">文字内容</button><button :class="{active:inspectorTab==='chunks'}" @click="inspectorTab='chunks'">分块索引 {{ documentDetail?.child_chunk_count || 0 }}</button><button :class="{active:inspectorTab==='strategy'}" @click="inspectorTab='strategy'">检索策略</button></div></div>

    <div v-if="inspectorTab==='content' && documentDetail" class="card-body inspector-content">
      <div class="document-facts"><span>原始字符 {{ documentDetail.cleaning_stats?.original_chars ?? documentDetail.char_count }}</span><span>清洗后 {{ documentDetail.cleaning_stats?.cleaned_chars ?? documentDetail.char_count }}</span><span>去除噪声行 {{ documentDetail.cleaning_stats?.noise_lines_removed || 0 }}</span><span>去除重复行 {{ documentDetail.cleaning_stats?.repeated_lines_removed || 0 }}</span><span>内容哈希 {{ shortHash(documentDetail.content_hash) }}</span></div>
      <pre class="document-text">{{ documentDetail.cleaned_content || '该文档没有可显示的清洗后正文。' }}</pre>
    </div>

    <div v-else-if="inspectorTab==='chunks' && documentDetail" class="card-body">
      <div class="chunk-toolbar"><div><button v-for="level in chunkLevels" :key="level" class="btn btn-sm" :class="{'btn-primary':chunkLevel===level}" @click="changeChunkLevel(level)">{{ level === 'all' ? '全部' : level === 'parent' ? '父块' : '检索子块' }}</button></div><span>显示 {{ documentChunks.length }} / {{ chunkTotal }}</span></div>
      <div class="chunk-list"><article v-for="item in documentChunks" :key="item.id" class="chunk-card" :class="item.level"><header><div><span class="chunk-level">{{ item.level === 'parent' ? '父块' : '子块' }}</span><strong>#{{ item.chunk_index }}</strong><span v-if="item.metadata?.locator">{{ item.metadata.locator }}</span></div><span :class="item.embedding?.indexed ? 'vector-ready' : 'vector-missing'">{{ item.embedding?.indexed ? `${item.embedding.dimensions} 维 · 已索引` : item.level === 'parent' ? '上下文块' : '未向量化' }}</span></header><p>{{ item.content }}</p><footer><span>Token 估算 {{ item.token_count }}</span><span v-if="item.parent_chunk_id">父块 {{ shortHash(item.parent_chunk_id) }}</span><span>Hash {{ shortHash(item.content_hash) }}</span><span v-if="item.embedding?.model">{{ item.embedding.provider }} · {{ item.embedding.model }}</span></footer></article><div v-if="!documentChunks.length" class="empty">当前筛选条件下没有分块</div></div>
    </div>

    <div v-else-if="inspectorTab==='strategy' && overview" class="card-body strategy-panel">
      <div class="strategy-flow"><div><b>1</b><strong>查询改写</strong><p>{{ overview.retrieval_strategy.query_rewrite }}</p></div><i>→</i><div><b>2</b><strong>多路召回</strong><p>{{ overview.retrieval_strategy.retrievers.join(' + ') }}</p></div><i>→</i><div><b>3</b><strong>候选融合</strong><p>{{ overview.retrieval_strategy.fusion }}</p></div><i>→</i><div><b>4</b><strong>重排序</strong><p>{{ overview.retrieval_strategy.rerank_model }}</p></div><i>→</i><div><b>5</b><strong>上下文生成</strong><p>{{ overview.retrieval_strategy.context_expansion }}</p></div></div>
      <div class="strategy-grid"><div><label>初筛候选</label><strong>Top {{ overview.retrieval_strategy.candidate_k }}</strong></div><div><label>最终片段</label><strong>Top {{ overview.retrieval_strategy.top_k }}</strong></div><div><label>上下文预算</label><strong>{{ overview.retrieval_strategy.context_char_budget }} 字符</strong></div><div><label>多样性约束</label><strong>{{ overview.retrieval_strategy.diversity }}</strong></div></div>
      <h3>当前向量索引</h3><div v-if="overview.vector_indexes.length" class="list-stack"><div v-for="item in overview.vector_indexes" :key="`${item.model}-${item.dimensions}`" class="list-item"><div><strong>{{ item.model }}</strong><p>{{ item.provider }} · {{ item.dimensions }} 维</p></div><span>{{ item.count }} 个向量</span></div></div><div v-else class="notice">尚未生成向量索引。配置模型后上传资料或点击“重新向量化”。</div>
      <p class="citation-policy">引用策略：{{ overview.retrieval_strategy.citation_policy }}</p>
    </div>
  </section>

  <section v-if="addText" class="card" style="margin-top:20px"><div class="card-header"><h2>录入资料</h2><button class="btn btn-sm" @click="addText=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>标题</label><input v-model="docForm.title" class="input"></div><div class="field"><label>来源</label><input v-model="docForm.source" class="input"></div><div class="field full"><label>正文</label><textarea v-model="docForm.content" class="textarea" style="min-height:220px" /></div><div class="field full"><button class="btn btn-primary" @click="saveText">清洗、切分并向量化</button></div></div></section>

  <section v-if="addSource" class="card" style="margin-top:20px"><div class="card-header"><h2>添加外部数据源</h2><button class="btn btn-sm" @click="addSource=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>类型</label><select v-model="sourceForm.type" class="input"><option value="web">网页</option><option value="database">数据库</option><option value="api">API / 第三方</option></select></div><div class="field"><label>名称</label><input v-model="sourceForm.name" class="input"></div><template v-if="sourceForm.type==='web'"><div class="field full"><label>网页 URL</label><input v-model="sourceForm.url" class="input"></div><div class="field"><label>最多抓取页数</label><input v-model.number="sourceForm.max_pages" type="number" min="1" max="20" class="input"></div></template><template v-else-if="sourceForm.type==='api'"><div class="field full"><label>API URL</label><input v-model="sourceForm.url" class="input"></div><div class="field"><label>方法</label><select v-model="sourceForm.method" class="input"><option>GET</option><option>POST</option></select></div><div class="field"><label>JSON 响应路径</label><input v-model="sourceForm.response_path" class="input" placeholder="data.items"></div><div class="field full"><label>请求头 JSON（将加密保存）</label><textarea v-model="sourceForm.headers" class="textarea" /></div></template><template v-else><div class="field full"><label>SQLAlchemy 连接地址（凭据将加密保存）</label><input v-model="sourceForm.connection_url" class="input" placeholder="sqlite:///D:/data/source.db"></div><div class="field full"><label>只读 SELECT / WITH 查询</label><textarea v-model="sourceForm.query" class="textarea" /></div></template><div class="field full"><button class="btn btn-primary" @click="saveSource">保存并同步</button></div></div></section>
  -->

  <section class="card knowledge-qa">
    <div class="qa-heading">
      <div class="qa-heading-icon"><Sparkles :size="22" /></div>
      <div><span>KNOWLEDGE RETRIEVAL</span><h2>知识库问答</h2><p>每一步检索都会实时呈现，答案可回溯到原始证据。</p></div>
    </div>
    <div class="card-body qa-body">
      <div class="query-panel">
        <div class="search-scope"><label>检索范围</label><select v-model="searchScope" class="input"><option value="all">全部知识库</option><option value="group" :disabled="!groups.length">指定分组</option><option value="base" :disabled="!selected">当前知识库</option></select><select v-if="searchScope==='group'" v-model="searchGroupId" class="input"><option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }}（{{ group.knowledge_base_count }} 个知识库）</option></select><span v-if="searchScope==='base' && selected">{{ selected.name }}</span></div>
        <div class="question-row"><Search :size="18" /><input v-model="search" class="input" :disabled="queryRunning" placeholder="输入你希望从知识库中查证的问题" @keyup.enter="ask"><button class="btn btn-primary" :disabled="queryRunning" @click="ask"><LoaderCircle v-if="queryRunning" class="spin" :size="15" /><Search v-else :size="14" />{{ queryRunning ? '正在检索' : '开始检索' }}</button></div>
      </div>

      <div v-if="queryAttempted" class="qa-results-layout">
        <aside class="retrieval-process" :class="{failed:queryError}">
          <div class="process-head"><div><strong>精简检索过程</strong><small>基于真实后端事件逐步推进</small></div><span>{{ queryRunning ? '执行中' : queryError ? '失败' : '完成' }}</span></div>
          <div class="process-steps">
            <div v-for="(step,index) in retrievalSteps" :key="step.id" :class="step.status">
              <span class="step-marker">
                <Check v-if="step.status==='complete'" :size="14" />
                <LoaderCircle v-else-if="step.status==='running'" class="spin" :size="14" />
                <X v-else-if="step.status==='error'" :size="14" />
                <Circle v-else :size="12" />
              </span>
              <span class="step-copy"><small>步骤 {{ index + 1 }}</small><strong>{{ step.title }}</strong><em>{{ step.description }}</em><p>{{ step.detail }}</p></span>
            </div>
          </div>
          <div v-if="rewrittenQueries.length" class="rewrite-list"><label>实际检索式</label><span v-for="item in rewrittenQueries" :key="item">{{ item }}</span></div>
          <p v-if="queryError" class="process-error"><AlertTriangle :size="14" />{{ queryError }}</p>
        </aside>

        <main class="answer-panel">
          <div v-if="queryRunning && !answer" class="answer-waiting"><LoaderCircle class="spin" :size="24" /><strong>正在建立可信回答</strong><p>已完成的步骤与候选数量会实时显示在左侧。</p></div>
          <template v-else-if="answer">
            <div class="answer-title"><span><CheckCircle2 :size="17" />知识库回答</span><small>{{ citations.length }} 条引用 · {{ results.length }} 个证据片段<span v-if="expandedListItems"> · 已完整展开 {{ expandedListItems }} 个列表项</span></small></div>
            <article class="answer-card">{{ answer }}</article>
            <div v-if="results.length" class="evidence-section"><h3>检索证据</h3><div class="result-list"><article v-for="(item,index) in results" :key="item.id" class="evidence-card"><header><b>资料 {{ index+1 }}</b><strong>{{ item.title }}</strong><span>{{ Number(item.score || 0).toFixed(3) }}</span></header><p class="result-content">{{ item.content }}</p><footer><span>{{ item.citation }}</span><a v-if="item.metadata?.url" :href="item.metadata.url" target="_blank" rel="noreferrer">打开原始来源</a></footer></article></div></div>
          </template>
          <div v-else class="answer-waiting empty-answer"><BookOpen :size="25" /><strong>没有生成回答</strong><p>请调整检索范围或换一种问法后重试。</p></div>
        </main>
      </div>
      <div v-else class="qa-placeholder"><span><BookOpen :size="22" /></span><div><strong>从可信资料中寻找答案</strong><p>选择范围并输入问题，系统会显式展示范围解析、改写、召回、重排和生成过程。</p></div></div>
    </div>
  </section>

  <FloatingPanel v-model="showConfig" title="知识库模型配置" eyebrow="RETRIEVAL MODELS" description="配置向量化、重排序与答案生成端点。" size="large"><div class="form-grid"><div class="field full"><label>SiliconFlow API Key（留空表示保持不变）</label><input v-model="providerConfig.api_key" type="password" class="input" :placeholder="providerConfig.has_api_key ? '已安全保存' : 'sk-...'" autocomplete="new-password"></div><div class="field"><label>Embedding 模型</label><input v-model="providerConfig.embedding_model" class="input"></div><div class="field"><label>Embedding URL</label><input v-model="providerConfig.embedding_base_url" class="input"></div><div class="field"><label>Rerank 模型</label><input v-model="providerConfig.rerank_model" class="input"></div><div class="field"><label>Rerank URL</label><input v-model="providerConfig.rerank_base_url" class="input"></div><div class="field"><label>答案生成模型端点</label><select v-model="providerConfig.llm_endpoint_id" class="input"><option :value="null">离线摘要</option><option v-for="item in endpoints" :key="item.id" :value="item.id">{{ item.name }} · {{ item.default_model }}</option></select></div><div class="field"><label>最终 Top-K</label><input v-model.number="providerConfig.top_k" type="number" min="1" max="20" class="input"></div><div class="field"><label>候选数量</label><input v-model.number="providerConfig.candidate_k" type="number" min="5" max="100" class="input"></div><div class="field full" style="display:flex;gap:8px"><button class="btn" @click="testConfig">测试连接</button><button class="btn btn-primary" @click="saveConfig">保存配置</button></div></div></FloatingPanel>

  <div v-if="movingBase" class="modal-backdrop" role="presentation" @click.self="movingBase=null">
    <section class="action-modal" role="dialog" aria-modal="true" aria-labelledby="move-base-title">
      <header><span class="modal-icon move"><FolderInput :size="20" /></span><div><h2 id="move-base-title">移动知识库分组</h2><p>调整“{{ movingBase.name }}”所在的分组，不会移动或复制底层资料。</p></div><button class="icon-button" aria-label="关闭" @click="movingBase=null"><X :size="17" /></button></header>
      <div v-if="groups.length" class="move-group-list"><label v-for="group in groups" :key="group.id" :class="{selected:moveGroupIds.includes(group.id)}"><input v-model="moveGroupIds" type="checkbox" :value="group.id"><i :style="{background:group.color}"></i><span><strong>{{ group.name }}</strong><small>{{ group.knowledge_base_count }} 个知识库</small></span><Check v-if="moveGroupIds.includes(group.id)" :size="16" /></label></div>
      <div v-else class="modal-empty"><FolderTree :size="26" /><strong>还没有可用分组</strong><p>请先创建分组，再移动知识库。</p></div>
      <footer><button class="btn" @click="movingBase=null">取消</button><button v-if="groups.length" class="btn btn-primary" @click="saveBaseGroups">保存分组</button><button v-else class="btn btn-primary" @click="movingBase=null;openNewGroup()">新建分组</button></footer>
    </section>
  </div>

  <div v-if="deletingBase" class="modal-backdrop" role="presentation" @click.self="deletingBase=null">
    <section class="action-modal delete-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-base-title">
      <header><span class="modal-icon delete"><AlertTriangle :size="21" /></span><div><h2 id="delete-base-title">确认删除知识库？</h2><p>“{{ deletingBase.name }}”将被永久删除。</p></div><button class="icon-button" aria-label="关闭" @click="deletingBase=null"><X :size="17" /></button></header>
      <div class="delete-warning"><strong>此操作不可撤销</strong><p>将同时清理 {{ deletingBase.document_count || 0 }} 份文档、父子分块、向量索引、数据源记录及全部分组关系。</p></div>
      <footer><button class="btn" @click="deletingBase=null">取消</button><button class="btn danger-solid" @click="deleteBase"><Trash2 :size="14" />确认永久删除</button></footer>
    </section>
  </div>
</template>

<style scoped>
.initial-source-types{display:flex;flex-wrap:wrap;gap:7px;margin-top:6px}.upload-zone{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px;border:1px dashed #83afd6;border-radius:9px;background:#f4f9fe;color:#315b7e;cursor:pointer}.upload-zone span{font-size:11px;color:#1769c2}.folder-hint{margin:7px 2px 0;color:#60758b;font-size:10px}.folder-progress{display:flex;flex-wrap:wrap;align-items:center;gap:7px;padding:10px 12px;border:1px solid #cfe1f1;border-radius:9px;background:#f5faff}.folder-progress span{padding:4px 7px;border-radius:6px;background:#e6f1fb;color:#285a85;font-size:10px}.folder-progress small{flex:1;min-width:180px;overflow:hidden;color:#60758b;text-align:right;text-overflow:ellipsis;white-space:nowrap}
.group-bar{margin-bottom:20px}.group-bar .card-header p{margin:4px 0 0;font-size:11px;color:#60758b}.group-pills{display:flex;flex-wrap:wrap;gap:8px}.group-pills button{display:flex;align-items:center;gap:7px;border:1px solid #d5e3f0;border-radius:9px;background:#fff;color:#3c5d79;padding:8px 10px;cursor:pointer}.group-pills button.active{border-color:var(--group-color,#1769c2);background:#eaf4fe;color:#174f88;box-shadow:inset 0 0 0 1px var(--group-color,#1769c2)}.group-pills button>i{width:9px;height:9px;border-radius:50%}.group-pills button>span{display:grid;place-items:center;min-width:19px;height:19px;border-radius:10px;background:#edf3f8;font-size:10px}.group-pills button>em{font-style:normal;font-size:10px;color:#1769c2;margin-left:3px}.base-groups{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}.base-groups span{border:1px solid;border-radius:999px;padding:2px 6px;font-size:9px;background:#fff}.member-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:10px;border:1px solid #d7e4f0;border-radius:9px;background:#f8fbfe}.member-grid>label{display:flex;align-items:flex-start;gap:8px;padding:9px;border-radius:7px;background:#fff;cursor:pointer}.member-grid input{margin-top:3px}.member-grid span{display:flex;flex-direction:column}.member-grid small{margin-top:3px;color:#6a8094}.search-scope{display:flex;align-items:center;gap:8px;margin-bottom:10px}.search-scope label{font-size:11px;color:#60758b;white-space:nowrap}.search-scope select{max-width:240px}.search-scope>span{font-size:11px;color:#1769c2;background:#edf6ff;padding:7px 10px;border-radius:7px}
.knowledge-metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:#dbe8f5;border-top:1px solid #dbe8f5;border-bottom:1px solid #dbe8f5}.knowledge-metrics div{display:flex;flex-direction:column;gap:3px;padding:13px 18px;background:#f8fbff}.knowledge-metrics strong{font-size:20px;color:#174f88}.knowledge-metrics span{font-size:11px;color:#60758b}.selected-row{background:#eef6ff}.knowledge-inspector>.card-header{align-items:flex-end}.knowledge-inspector .card-header p{margin:4px 0 0;font-size:11px;color:#60758b}.inspector-tabs{display:flex;gap:5px}.inspector-tabs button{border:0;border-radius:8px;background:#edf3f9;color:#49657f;padding:8px 13px;cursor:pointer}.inspector-tabs button.active{background:#1769c2;color:white}.document-facts{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:13px}.document-facts span{padding:6px 9px;border:1px solid #d7e5f2;border-radius:7px;background:#f5f9fd;color:#49657f;font-size:11px}.document-text{max-height:560px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:0;padding:20px;border:1px solid #d5e4f2;border-radius:10px;background:white;color:#263f57;font:13px/1.85 "Microsoft YaHei",sans-serif}.chunk-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;color:#60758b;font-size:11px}.chunk-toolbar>div{display:flex;gap:6px}.chunk-list{display:grid;gap:10px;max-height:620px;overflow:auto;padding-right:4px}.chunk-card{border:1px solid #d6e3ef;border-left:4px solid #65a3df;border-radius:10px;padding:13px 15px;background:#fff}.chunk-card.parent{border-left-color:#254f78;background:#f8fbff}.chunk-card header,.chunk-card footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.chunk-card header>div,.chunk-card footer{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.chunk-card header span,.chunk-card footer{font-size:10px;color:#687f95}.chunk-card p{white-space:pre-wrap;color:#2f4b65;font-size:12px;line-height:1.7}.chunk-level{padding:3px 7px;border-radius:999px;background:#e7f2fc;color:#1769c2!important}.parent .chunk-level{background:#dce8f3;color:#254f78!important}.vector-ready{color:#13835c!important}.vector-missing{color:#9a6b1c!important}.strategy-panel h3{margin-top:22px}.strategy-flow{display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr auto 1fr;align-items:stretch;gap:7px}.strategy-flow>div{min-width:0;padding:14px;border:1px solid #d6e4f1;border-radius:10px;background:linear-gradient(180deg,#fff,#f5f9fd)}.strategy-flow b{display:inline-grid;place-items:center;width:23px;height:23px;border-radius:50%;background:#1769c2;color:white;margin-bottom:8px}.strategy-flow strong{display:block;color:#244e75}.strategy-flow p{font-size:10px;line-height:1.5;color:#60758b}.strategy-flow i{align-self:center;color:#72a6d5}.strategy-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}.strategy-grid>div{padding:13px;border-radius:9px;background:#edf5fc}.strategy-grid label{display:block;font-size:10px;color:#60758b;margin-bottom:5px}.strategy-grid strong{font-size:12px;color:#234f78}.citation-policy{font-size:11px;color:#60758b;margin:14px 0 0}@media(max-width:1200px){.strategy-flow{grid-template-columns:1fr}.strategy-flow i{display:none}.strategy-grid{grid-template-columns:repeat(2,1fr)}}
.question-row{display:flex;gap:9px}.question-row .input{flex:1}.question-row .btn{min-width:92px}.retrieval-process{margin-top:14px;padding:13px 15px;border:1px solid #cfe1f1;border-radius:11px;background:linear-gradient(180deg,#f8fcff,#f1f7fd)}.retrieval-process.failed{border-color:#e8caca;background:#fff9f9}.process-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:11px}.process-head strong{font-size:12px;color:#234f75}.process-head span{font-size:10px;color:#1769c2}.failed .process-head span{color:#b13f3f}.process-steps{display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr;align-items:center;gap:8px}.process-steps>div{display:flex;align-items:center;gap:9px;min-width:0;padding:9px 10px;border:1px solid #d8e6f2;border-radius:9px;background:#fff}.process-steps>div.running{border-color:#7fb5e4;box-shadow:0 0 0 2px #dceeff}.process-steps b{display:grid;place-items:center;flex:0 0 auto;width:23px;height:23px;border-radius:50%;background:#1769c2;color:#fff;font-size:10px}.process-steps span{display:flex;min-width:0;flex-direction:column;gap:3px}.process-steps strong{font-size:11px;color:#2a5073}.process-steps small{overflow:hidden;color:#6b8093;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.process-steps i{color:#74a6d2;font-style:normal}.process-error{margin:10px 0 0;color:#b13f3f;font-size:10px}.answer-card{margin-top:16px;white-space:pre-wrap;line-height:1.8}.result-list{margin-top:14px}.result-content{font-size:12px;color:#385570;line-height:1.7}@media(max-width:1100px){.process-steps{grid-template-columns:1fr 1fr}.process-steps>i{display:none}}

/* Knowledge workspace refresh */
.knowledge-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}.knowledge-summary>div{display:flex;align-items:center;gap:12px;padding:14px 16px;border:1px solid #d8e5f0;border-radius:12px;background:#fff;box-shadow:0 5px 18px #174f8810}.summary-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:10px}.summary-icon.blue{background:#e7f3ff;color:#1769c2}.summary-icon.violet{background:#f0eaff;color:#7652be}.summary-icon.green{background:#e6f7f0;color:#16815e}.summary-icon.amber{background:#fff2db;color:#b06d12}.knowledge-summary p{display:flex;flex-direction:column;margin:0}.knowledge-summary b{font-size:20px;color:#183f62}.knowledge-summary small{color:#6b8093;font-size:10px}.group-bar{margin-bottom:18px}.base-library{margin-bottom:20px;overflow:hidden}.count-badge{padding:5px 9px;border-radius:999px;background:#edf5fc;color:#1769c2;font-size:10px}.knowledge-grid{display:grid;max-height:min(540px,calc(100vh - 285px));min-height:210px;grid-template-columns:repeat(3,minmax(0,1fr));align-content:start;gap:12px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding-right:14px}.knowledge-grid:focus-visible{outline:2px solid #7ab0dc;outline-offset:-3px}.knowledge-grid::-webkit-scrollbar{width:8px}.knowledge-grid::-webkit-scrollbar-track{border-radius:99px;background:#eef4f8}.knowledge-grid::-webkit-scrollbar-thumb{border:2px solid #eef4f8;border-radius:99px;background:#9db7ca}.knowledge-grid::-webkit-scrollbar-thumb:hover{background:#6f9cbd}.knowledge-card{overflow:hidden;border:1px solid #d8e4ef;border-radius:12px;background:#fff;transition:transform .16s,border-color .16s,box-shadow .16s}.knowledge-card:hover{transform:translateY(-2px);border-color:#8ab7de;box-shadow:0 10px 24px #1b5d9215}.knowledge-card.active{border-color:#72a8d7}.knowledge-card-main{display:flex;width:100%;align-items:center;gap:11px;border:0;background:transparent;padding:15px;text-align:left;cursor:pointer}.knowledge-card-main>svg{margin-left:auto;color:#7c94a8}.knowledge-card-icon{display:grid;place-items:center;flex:0 0 auto;width:43px;height:43px;border-radius:11px;background:linear-gradient(145deg,#e5f3ff,#f3f8fd);color:#1769c2}.knowledge-card-copy{display:flex;min-width:0;flex:1;flex-direction:column;gap:3px}.knowledge-card-copy small{color:#1769c2;font-size:9px;font-weight:700;letter-spacing:.8px;text-transform:uppercase}.knowledge-card-copy strong{overflow:hidden;color:#244d70;font-size:13px;text-overflow:ellipsis;white-space:nowrap}.knowledge-card-copy em{overflow:hidden;color:#74889b;font-size:10px;font-style:normal;text-overflow:ellipsis;white-space:nowrap}.knowledge-card-meta{display:flex;min-height:40px;align-items:center;justify-content:space-between;gap:8px;padding:8px 14px;border-top:1px solid #edf2f6;background:#f8fbfd}.knowledge-card-meta>span{display:flex;align-items:center;gap:5px;color:#60788d;font-size:10px;white-space:nowrap}.knowledge-card-meta .base-groups{justify-content:flex-end;margin:0}.base-groups .ungrouped{border-color:#cbd8e3!important;color:#8294a5!important;background:#f5f7f9}.knowledge-card-actions{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid #e8eef4}.knowledge-card-actions button{display:flex;align-items:center;justify-content:center;gap:6px;border:0;background:#fff;padding:9px;color:#47667f;font-size:10px;cursor:pointer}.knowledge-card-actions button+button{border-left:1px solid #e8eef4}.knowledge-card-actions button:hover{background:#f2f8fd;color:#1769c2}.knowledge-card-actions .danger-link:hover{background:#fff5f5;color:#b43c3c}.base-empty{display:flex;min-height:180px;align-items:center;justify-content:center;flex-direction:column;gap:7px;border:1px dashed #c9dbe9;border-radius:11px;background:#f8fbfe}.base-empty strong{color:#365a78}.base-empty span{font-size:10px}

/* Explicit retrieval */
.knowledge-qa{margin-top:20px;overflow:hidden}.qa-heading{display:flex;align-items:center;gap:12px;padding:17px 20px;border-bottom:1px solid #dce8f2;background:linear-gradient(110deg,#f6fbff,#edf6ff 58%,#f7f4ff)}.qa-heading-icon{display:grid;place-items:center;width:43px;height:43px;border-radius:12px;background:#1769c2;color:#fff;box-shadow:0 7px 18px #1769c23b}.qa-heading span{font-size:8px;font-weight:700;letter-spacing:1.4px;color:#1769c2}.qa-heading h2{margin:2px 0;color:#214a6e;font-size:16px}.qa-heading p{margin:0;color:#687f93;font-size:10px}.qa-body{padding:18px}.query-panel{padding:13px;border:1px solid #d7e5f0;border-radius:12px;background:#f8fbfe}.question-row{position:relative;align-items:center}.question-row>svg{position:absolute;left:12px;color:#6e8ba3}.question-row .input{height:42px;padding-left:39px;border-color:#cbddeb;background:#fff;font-size:12px}.question-row .btn{height:42px;min-width:112px}.spin{animation:spin .85s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.qa-results-layout{display:grid;grid-template-columns:330px minmax(0,1fr);gap:14px;margin-top:16px}.retrieval-process{margin:0;padding:14px;border-color:#d2e2ef;background:#f7fbfe}.process-head{align-items:flex-start;padding-bottom:11px;border-bottom:1px solid #deebf4}.process-head>div{display:flex;flex-direction:column;gap:3px}.process-head>div>small{color:#7890a3;font-size:9px}.process-head>span{padding:4px 7px;border-radius:999px;background:#e7f2fc;font-weight:700}.process-steps{display:grid;grid-template-columns:1fr;gap:0}.process-steps>div{position:relative;display:grid;grid-template-columns:29px minmax(0,1fr);gap:9px;border:0;border-radius:0;background:transparent;padding:9px 0}.process-steps>div:not(:last-child)::after{position:absolute;top:38px;bottom:-7px;left:13px;width:2px;background:#d9e6f0;content:""}.process-steps>div.running{border:0;box-shadow:none}.process-steps>div.running::after{background:linear-gradient(#64a7df,#d9e6f0)}.step-marker{z-index:1;display:grid!important;width:27px;height:27px!important;place-items:center;border:2px solid #d5e2ec;border-radius:50%;background:#fff;color:#9aaaba}.complete .step-marker{border-color:#24a378;background:#24a378;color:#fff}.running .step-marker{border-color:#1769c2;background:#eaf4fd;color:#1769c2}.error .step-marker{border-color:#c94a4a;background:#c94a4a;color:#fff}.step-copy{display:flex!important;flex-direction:column;gap:2px!important}.step-copy>small{color:#8a9bab!important;font-size:8px!important;letter-spacing:.4px}.step-copy>strong{font-size:11px!important}.step-copy>em{color:#7b8fa1;font-size:9px;font-style:normal}.step-copy>p{margin:3px 0 0;color:#476983;font-size:9px;line-height:1.4}.pending .step-copy{opacity:.57}.rewrite-list{display:flex;flex-direction:column;gap:5px;margin-top:9px;padding-top:11px;border-top:1px solid #deebf4}.rewrite-list label{color:#6c8397;font-size:9px}.rewrite-list span{display:block;padding:6px 8px;border-radius:6px;background:#eaf3fb;color:#315d82;font-size:9px}.process-error{display:flex;align-items:flex-start;gap:5px}.answer-panel{min-width:0;min-height:360px;border:1px solid #d8e5f0;border-radius:12px;background:#fff}.answer-waiting{display:flex;min-height:360px;align-items:center;justify-content:center;flex-direction:column;color:#1769c2}.answer-waiting strong{margin-top:10px;color:#325777}.answer-waiting p{margin:5px 0;color:#7a8fa2;font-size:10px}.answer-title{display:flex;align-items:center;justify-content:space-between;padding:13px 16px;border-bottom:1px solid #e1ebf3}.answer-title>span{display:flex;align-items:center;gap:7px;color:#1b7457;font-size:11px;font-weight:700}.answer-title small{color:#7a8fa1;font-size:9px}.answer-card{margin:0;padding:18px 20px;border:0;background:#fff;color:#294a65;font-size:12px;line-height:1.85}.evidence-section{padding:0 16px 16px;border-top:1px solid #e5edf4}.evidence-section h3{margin:14px 0 9px;color:#315774;font-size:12px}.result-list{display:grid;gap:8px;margin:0}.evidence-card{border:1px solid #dce6ef;border-radius:9px;background:#fbfdff;padding:11px 12px}.evidence-card header{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:8px}.evidence-card header b{padding:3px 6px;border-radius:5px;background:#e7f2fc;color:#1769c2;font-size:8px}.evidence-card header strong{overflow:hidden;color:#315672;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.evidence-card header>span{color:#16805d;font-size:9px}.evidence-card p{margin:8px 0}.evidence-card footer{display:flex;justify-content:space-between;gap:8px;color:#7b8e9f;font-size:9px}.evidence-card footer span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.evidence-card footer a{flex:0 0 auto;color:#1769c2}.qa-placeholder{display:flex;align-items:center;gap:12px;margin-top:14px;padding:20px;border:1px dashed #c9dceb;border-radius:11px;background:#f9fcfe}.qa-placeholder>span{display:grid;place-items:center;width:40px;height:40px;border-radius:10px;background:#e8f3fc;color:#1769c2}.qa-placeholder strong{color:#355a77;font-size:11px}.qa-placeholder p{margin:4px 0 0;color:#788da0;font-size:10px}.empty-answer{color:#7990a3}

/* Destructive and group actions */
.modal-backdrop{position:fixed;z-index:1000;inset:0;display:grid;place-items:center;padding:20px;background:#102f4a73;backdrop-filter:blur(3px)}.action-modal{width:min(520px,100%);overflow:hidden;border:1px solid #d7e4ef;border-radius:15px;background:#fff;box-shadow:0 24px 70px #0f2f4d3d}.action-modal>header{display:flex;align-items:flex-start;gap:11px;padding:18px 19px;border-bottom:1px solid #e5edf4}.action-modal>header>div{flex:1}.action-modal h2{margin:0;color:#244d6f;font-size:16px}.action-modal header p{margin:4px 0 0;color:#6e8295;font-size:10px;line-height:1.5}.modal-icon{display:grid;place-items:center;width:39px;height:39px;border-radius:10px}.modal-icon.move{background:#e7f3fd;color:#1769c2}.modal-icon.delete{background:#ffeded;color:#b33d3d}.icon-button{display:grid;place-items:center;border:0;background:transparent;padding:5px;color:#73899c;cursor:pointer}.move-group-list{display:grid;gap:8px;max-height:360px;overflow:auto;padding:15px 18px}.move-group-list label{display:grid;grid-template-columns:auto auto 1fr auto;align-items:center;gap:10px;padding:11px 12px;border:1px solid #dbe6ef;border-radius:9px;cursor:pointer}.move-group-list label.selected{border-color:#7eb2de;background:#f1f8fe}.move-group-list input{accent-color:#1769c2}.move-group-list i{width:9px;height:9px;border-radius:50%}.move-group-list span{display:flex;flex-direction:column;gap:2px}.move-group-list strong{color:#355874;font-size:11px}.move-group-list small{color:#7a8e9f;font-size:9px}.move-group-list label>svg{color:#16815d}.action-modal>footer{display:flex;justify-content:flex-end;gap:8px;padding:13px 18px;border-top:1px solid #e5edf4;background:#f8fafc}.modal-empty{display:flex;align-items:center;flex-direction:column;padding:30px;color:#7690a4}.modal-empty strong{margin-top:7px;color:#365a77}.modal-empty p{margin:4px 0;font-size:10px}.delete-warning{margin:18px;padding:13px 14px;border:1px solid #f0cccc;border-radius:9px;background:#fff7f7}.delete-warning strong{color:#a93838;font-size:11px}.delete-warning p{margin:5px 0 0;color:#7c5c5c;font-size:10px;line-height:1.6}.danger-solid{border:1px solid #b93f3f!important;background:#b93f3f!important;color:#fff!important}.danger-solid:hover{background:#a53434!important}

@media(max-width:1250px){.knowledge-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.qa-results-layout{grid-template-columns:300px minmax(0,1fr)}}@media(max-width:900px){.knowledge-summary{grid-template-columns:repeat(2,1fr)}.knowledge-grid,.qa-results-layout{grid-template-columns:1fr}.search-scope{align-items:stretch;flex-direction:column}.search-scope select{max-width:none}.question-row{align-items:stretch;flex-wrap:wrap}.question-row .input{min-width:0}.retrieval-process{min-height:auto}}

.knowledge-grid{height:clamp(180px,21vh,210px);max-height:none;min-height:0;grid-auto-rows:max-content}
@media(max-width:900px){.knowledge-grid{height:clamp(300px,48vh,460px)}}
</style>
