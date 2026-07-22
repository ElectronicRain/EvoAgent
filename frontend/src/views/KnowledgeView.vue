<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { BookOpen, Database, FileText, FolderTree, Globe2, Plus, Search, Settings2, Trash2, Upload } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const bases = ref<Entity[]>([])
const groups = ref<Entity[]>([])
const selected = ref<Entity | null>(null)
const activeGroupId = ref('all')
const searchScope = ref<'all' | 'group' | 'base'>('all')
const searchGroupId = ref('')
const documents = ref<Entity[]>([])
const sources = ref<Entity[]>([])
const overview = ref<Entity | null>(null)
const documentDetail = ref<Entity | null>(null)
const documentChunks = ref<Entity[]>([])
const chunkTotal = ref(0)
const inspectorTab = ref<'content' | 'chunks' | 'strategy'>('content')
const chunkLevel = ref<'all' | 'parent' | 'child'>('all')
const chunkLevels = ['all', 'parent', 'child'] as const
const answer = ref('')
const results = ref<Entity[]>([])
const citations = ref<Entity[]>([])
const trace = ref<Entity | null>(null)
const search = ref('二维结构化网格质量应如何评价？')
const createBase = ref(false)
const addText = ref(false)
const addSource = ref(false)
const showConfig = ref(false)
const showGroupEditor = ref(false)
const editingGroupId = ref('')
const baseForm = reactive({ name: '', discipline: '', description: '' })
const docForm = reactive({ title: '', source: '用户录入', content: '' })
const groupForm = reactive({ name: '', description: '', color: '#1769c2', knowledge_base_ids: [] as string[] })
const sourceForm = reactive({
  type: 'web', name: '', url: '', max_pages: 1, method: 'GET', headers: '{}',
  response_path: '', connection_url: '', query: 'SELECT * FROM your_table', row_limit: 5000,
})
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

function groupsForBase(baseId: string) {
  return groups.value.filter(group => (group.knowledge_base_ids || []).includes(baseId))
}

async function load() {
  store.loading(true)
  try {
    bases.value = await api.get('/knowledge-bases')
    selected.value ||= bases.value[0] || null
    const [config, modelEndpoints, groupRows] = await Promise.all([
      api.get<Entity>('/knowledge/config'),
      api.get<Entity[]>('/model-endpoints'),
      api.get<Entity[]>('/knowledge-groups'),
    ])
    Object.assign(providerConfig, config, { api_key: '' })
    endpoints.value = modelEndpoints
    groups.value = groupRows
    if (!searchGroupId.value && groupRows.length) searchGroupId.value = groupRows[0].id
    if (selected.value) await choose(selected.value)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function selectGroupFilter(groupId: string) {
  activeGroupId.value = groupId
  const target = groupId === 'all'
    ? bases.value[0]
    : bases.value.find(item => groups.value.find(group => group.id === groupId)?.knowledge_base_ids?.includes(item.id))
  if (target) await choose(target)
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

async function choose(item: Entity) {
  selected.value = item
  const [documentRows, sourceRows, overviewData] = await Promise.all([
    api.get<Entity[]>(`/knowledge-bases/${item.id}/documents`),
    api.get<Entity[]>(`/knowledge-bases/${item.id}/sources`),
    api.get<Entity>(`/knowledge-bases/${item.id}/overview`),
  ])
  documents.value = documentRows
  sources.value = sourceRows
  overview.value = overviewData
  if (documentRows.length) {
    const current = documentRows.find(row => row.id === documentDetail.value?.id) || documentRows[0]
    await inspectDocument(current)
  } else {
    documentDetail.value = null
    documentChunks.value = []
    chunkTotal.value = 0
  }
}

async function inspectDocument(item: Entity) {
  store.loading(true)
  try {
    const [detail, chunks] = await Promise.all([
      api.get<Entity>(`/knowledge-documents/${item.id}`),
      api.get<Entity>(`/knowledge-documents/${item.id}/chunks?level=${chunkLevel.value}&limit=500`),
    ])
    documentDetail.value = detail
    documentChunks.value = chunks.items || []
    chunkTotal.value = chunks.total || 0
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function changeChunkLevel(level: 'all' | 'parent' | 'child') {
  chunkLevel.value = level
  if (!documentDetail.value) return
  const chunks = await api.get<Entity>(`/knowledge-documents/${documentDetail.value.id}/chunks?level=${level}&limit=500`)
  documentChunks.value = chunks.items || []
  chunkTotal.value = chunks.total || 0
}

function shortHash(value?: string) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : '—'
}

async function saveBase() {
  store.loading(true)
  try {
    const item = await api.post<Entity>('/knowledge-bases', baseForm)
    createBase.value = false
    await load()
    await choose(item)
    store.notify('知识库已创建')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function saveText() {
  if (!selected.value) return
  store.loading(true)
  try {
    await api.post(`/knowledge-bases/${selected.value.id}/documents/text`, docForm)
    addText.value = false
    await choose(selected.value)
    store.notify('资料已完成清洗、分块和向量化')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function upload(event: Event) {
  if (!selected.value) return
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  store.loading(true)
  try {
    await api.upload(`/knowledge-bases/${selected.value.id}/documents/upload`, file)
    await choose(selected.value)
    store.notify('文档已完成解析、清洗、分块和向量化')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
    input.value = ''
  }
}

async function saveSource() {
  if (!selected.value) return
  let path = `/knowledge-bases/${selected.value.id}/sources/${sourceForm.type}`
  let payload: Entity
  try {
    if (sourceForm.type === 'web') {
      payload = { name: sourceForm.name, url: sourceForm.url, max_pages: sourceForm.max_pages, sync_now: true }
    } else if (sourceForm.type === 'api') {
      payload = {
        name: sourceForm.name, url: sourceForm.url, method: sourceForm.method,
        headers: JSON.parse(sourceForm.headers || '{}'), response_path: sourceForm.response_path, sync_now: true,
      }
    } else {
      payload = {
        name: sourceForm.name, connection_url: sourceForm.connection_url,
        query: sourceForm.query, row_limit: sourceForm.row_limit, sync_now: true,
      }
    }
  } catch {
    store.notify('请求头必须是有效的 JSON 对象', 'error')
    return
  }
  store.loading(true)
  try {
    const response = await api.post<Entity>(path, payload)
    await choose(selected.value)
    addSource.value = false
    if (response.sync_error || response.job?.status === 'failed') {
      store.notify(`数据源已保存，但同步失败：${response.sync_error || response.job?.error}`, 'error')
    } else store.notify('数据源同步完成')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
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

async function reindex() {
  if (!selected.value) return
  store.loading(true)
  try {
    const result = await api.post<Entity>(`/knowledge-bases/${selected.value.id}/reindex`)
    store.notify(`重新向量化完成：${result.embedded_chunks} 个片段`)
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
  answer.value = ''
  results.value = []
  try {
    const response = await api.post<Entity>('/knowledge/query', {
      query: search.value,
      knowledge_base_ids: searchScope.value === 'base' && selected.value ? [selected.value.id] : [],
      knowledge_group_ids: searchScope.value === 'group' && searchGroupId.value ? [searchGroupId.value] : [],
      generate_answer: true,
    })
    answer.value = response.answer
    results.value = response.chunks || []
    citations.value = response.citations || []
    trace.value = response.trace
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
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

  <section class="card group-bar">
    <div class="card-header"><div><h2>知识库分组</h2><p>一个知识库可以加入多个分组；分组只影响组织和检索范围，不复制资料。</p></div><button class="btn btn-sm" @click="openNewGroup"><Plus :size="14" />新建分组</button></div>
    <div class="card-body group-pills"><button :class="{active:activeGroupId==='all'}" @click="selectGroupFilter('all')"><FolderTree :size="14" />全部知识库 <span>{{ bases.length }}</span></button><button v-for="group in groups" :key="group.id" :class="{active:activeGroupId===group.id}" :style="{'--group-color':group.color}" @click="selectGroupFilter(group.id)"><i :style="{background:group.color}"></i>{{ group.name }} <span>{{ group.knowledge_base_count }}</span><em title="编辑分组" @click.stop="openEditGroup(group)">设置</em><Trash2 :size="13" title="删除分组" @click.stop="removeGroup(group)" /></button></div>
  </section>

  <div class="split">
    <section class="card">
      <div class="card-header"><h2>知识库</h2><span>{{ bases.length }} 个</span></div>
      <div class="card-body grid grid-2">
        <button v-for="item in filteredBases" :key="item.id" class="list-item" :class="{ active: selected?.id === item.id }" @click="choose(item)">
          <div style="display:flex;gap:11px;text-align:left"><div class="metric-icon"><Database :size="18" /></div><div><strong>{{ item.name }}</strong><p>{{ item.discipline }} · {{ item.document_count }} 份资料</p><div class="base-groups"><span v-for="group in groupsForBase(item.id)" :key="group.id" :style="{borderColor:group.color,color:group.color}">{{ group.name }}</span></div></div></div>
        </button>
        <div v-if="!filteredBases.length" class="empty">该分组还没有知识库，可点击分组“设置”添加成员。</div>
      </div>
    </section>
    <aside class="card">
      <div class="card-header"><h3>检索策略</h3></div>
      <div class="card-body"><div class="notice">查询改写 → 向量与 BM25 并行召回 → RRF 融合 → Rerank → 父块上下文扩展 → 带引用生成。</div></div>
    </aside>
  </div>

  <section v-if="createBase" class="card" style="margin-top:20px">
    <div class="card-header"><h2>新建知识库</h2><button class="btn btn-sm" @click="createBase=false">取消</button></div>
    <div class="card-body form-grid"><div class="field"><label>名称</label><input v-model="baseForm.name" class="input"></div><div class="field"><label>学科</label><input v-model="baseForm.discipline" class="input"></div><div class="field full"><label>说明</label><input v-model="baseForm.description" class="input"></div><div class="field full"><button class="btn btn-primary" @click="saveBase">创建</button></div></div>
  </section>

  <section v-if="showGroupEditor" class="card" style="margin-top:20px"><div class="card-header"><h2>{{ editingGroupId ? '编辑知识库分组' : '新建知识库分组' }}</h2><button class="btn btn-sm" @click="showGroupEditor=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>分组名称</label><input v-model="groupForm.name" class="input" placeholder="例如：计算流体力学"></div><div class="field"><label>标识颜色</label><input v-model="groupForm.color" type="color" class="input" style="height:40px"></div><div class="field full"><label>说明</label><input v-model="groupForm.description" class="input"></div><div class="field full"><label>选择分组中的知识库</label><div class="member-grid"><label v-for="item in bases" :key="item.id"><input v-model="groupForm.knowledge_base_ids" type="checkbox" :value="item.id"><span><strong>{{ item.name }}</strong><small>{{ item.discipline }} · {{ item.document_count }} 份资料</small></span></label><div v-if="!bases.length" class="empty">请先创建知识库</div></div></div><div class="field full"><button class="btn btn-primary" @click="saveGroup">保存分组与成员</button></div></div></section>

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

  <section class="card" style="margin-top:20px"><div class="card-header"><h2>知识库问答</h2><BookOpen :size="18" color="#1769c2" /></div><div class="card-body"><div class="search-scope"><label>检索范围</label><select v-model="searchScope" class="input"><option value="all">全部知识库</option><option value="group" :disabled="!groups.length">指定分组</option><option value="base" :disabled="!selected">当前知识库</option></select><select v-if="searchScope==='group'" v-model="searchGroupId" class="input"><option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }}（{{ group.knowledge_base_count }} 个知识库）</option></select><span v-if="searchScope==='base' && selected">{{ selected.name }}</span></div><div style="display:flex;gap:9px"><input v-model="search" class="input" @keyup.enter="ask"><button class="btn btn-primary" @click="ask"><Search :size="14" />提问</button></div><article v-if="answer" class="notice" style="margin-top:16px;white-space:pre-wrap;line-height:1.8">{{ answer }}</article><div class="list-stack" style="margin-top:14px"><div v-for="(item,index) in results" :key="item.id" class="list-item"><div><strong>[资料 {{ index+1 }}] {{ item.title }}</strong><p style="font-size:12px;color:#385570;line-height:1.7">{{ item.content }}</p><a v-if="item.metadata?.url" :href="item.metadata.url" target="_blank" rel="noreferrer">打开原始来源</a><p>{{ item.citation }}</p></div></div></div><p v-if="trace" style="font-size:11px;color:#60758b;margin-top:12px">范围：{{ trace.scope === 'all' ? '全部知识库' : trace.knowledge_base_ids?.length + ' 个知识库' }}；召回 {{ trace.fused_candidates || 0 }} 条候选，Rerank {{ trace.reranked || 0 }} 条，最终引用 {{ citations.length }} 条；Embedding：{{ trace.embedding_model || '—' }}</p></div></section>

  <section v-if="showConfig" class="card" style="margin-top:20px"><div class="card-header"><h2>知识库模型配置</h2><button class="btn btn-sm" @click="showConfig=false">取消</button></div><div class="card-body form-grid"><div class="field full"><label>SiliconFlow API Key（留空表示保持不变）</label><input v-model="providerConfig.api_key" type="password" class="input" :placeholder="providerConfig.has_api_key ? '已安全保存' : 'sk-...'" autocomplete="new-password"></div><div class="field"><label>Embedding 模型</label><input v-model="providerConfig.embedding_model" class="input"></div><div class="field"><label>Embedding URL</label><input v-model="providerConfig.embedding_base_url" class="input"></div><div class="field"><label>Rerank 模型</label><input v-model="providerConfig.rerank_model" class="input"></div><div class="field"><label>Rerank URL</label><input v-model="providerConfig.rerank_base_url" class="input"></div><div class="field"><label>答案生成模型端点</label><select v-model="providerConfig.llm_endpoint_id" class="input"><option :value="null">离线摘要</option><option v-for="item in endpoints" :key="item.id" :value="item.id">{{ item.name }} · {{ item.default_model }}</option></select></div><div class="field"><label>最终 Top-K</label><input v-model.number="providerConfig.top_k" type="number" min="1" max="20" class="input"></div><div class="field"><label>候选数量</label><input v-model.number="providerConfig.candidate_k" type="number" min="5" max="100" class="input"></div><div class="field full" style="display:flex;gap:8px"><button class="btn" @click="testConfig">测试连接</button><button class="btn btn-primary" @click="saveConfig">保存配置</button></div></div></section>
</template>

<style scoped>
.group-bar{margin-bottom:20px}.group-bar .card-header p{margin:4px 0 0;font-size:11px;color:#60758b}.group-pills{display:flex;flex-wrap:wrap;gap:8px}.group-pills button{display:flex;align-items:center;gap:7px;border:1px solid #d5e3f0;border-radius:9px;background:#fff;color:#3c5d79;padding:8px 10px;cursor:pointer}.group-pills button.active{border-color:var(--group-color,#1769c2);background:#eaf4fe;color:#174f88;box-shadow:inset 0 0 0 1px var(--group-color,#1769c2)}.group-pills button>i{width:9px;height:9px;border-radius:50%}.group-pills button>span{display:grid;place-items:center;min-width:19px;height:19px;border-radius:10px;background:#edf3f8;font-size:10px}.group-pills button>em{font-style:normal;font-size:10px;color:#1769c2;margin-left:3px}.base-groups{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}.base-groups span{border:1px solid;border-radius:999px;padding:2px 6px;font-size:9px;background:#fff}.member-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:10px;border:1px solid #d7e4f0;border-radius:9px;background:#f8fbfe}.member-grid>label{display:flex;align-items:flex-start;gap:8px;padding:9px;border-radius:7px;background:#fff;cursor:pointer}.member-grid input{margin-top:3px}.member-grid span{display:flex;flex-direction:column}.member-grid small{margin-top:3px;color:#6a8094}.search-scope{display:flex;align-items:center;gap:8px;margin-bottom:10px}.search-scope label{font-size:11px;color:#60758b;white-space:nowrap}.search-scope select{max-width:240px}.search-scope>span{font-size:11px;color:#1769c2;background:#edf6ff;padding:7px 10px;border-radius:7px}
.knowledge-metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:#dbe8f5;border-top:1px solid #dbe8f5;border-bottom:1px solid #dbe8f5}.knowledge-metrics div{display:flex;flex-direction:column;gap:3px;padding:13px 18px;background:#f8fbff}.knowledge-metrics strong{font-size:20px;color:#174f88}.knowledge-metrics span{font-size:11px;color:#60758b}.selected-row{background:#eef6ff}.knowledge-inspector>.card-header{align-items:flex-end}.knowledge-inspector .card-header p{margin:4px 0 0;font-size:11px;color:#60758b}.inspector-tabs{display:flex;gap:5px}.inspector-tabs button{border:0;border-radius:8px;background:#edf3f9;color:#49657f;padding:8px 13px;cursor:pointer}.inspector-tabs button.active{background:#1769c2;color:white}.document-facts{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:13px}.document-facts span{padding:6px 9px;border:1px solid #d7e5f2;border-radius:7px;background:#f5f9fd;color:#49657f;font-size:11px}.document-text{max-height:560px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:0;padding:20px;border:1px solid #d5e4f2;border-radius:10px;background:white;color:#263f57;font:13px/1.85 "Microsoft YaHei",sans-serif}.chunk-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;color:#60758b;font-size:11px}.chunk-toolbar>div{display:flex;gap:6px}.chunk-list{display:grid;gap:10px;max-height:620px;overflow:auto;padding-right:4px}.chunk-card{border:1px solid #d6e3ef;border-left:4px solid #65a3df;border-radius:10px;padding:13px 15px;background:#fff}.chunk-card.parent{border-left-color:#254f78;background:#f8fbff}.chunk-card header,.chunk-card footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.chunk-card header>div,.chunk-card footer{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.chunk-card header span,.chunk-card footer{font-size:10px;color:#687f95}.chunk-card p{white-space:pre-wrap;color:#2f4b65;font-size:12px;line-height:1.7}.chunk-level{padding:3px 7px;border-radius:999px;background:#e7f2fc;color:#1769c2!important}.parent .chunk-level{background:#dce8f3;color:#254f78!important}.vector-ready{color:#13835c!important}.vector-missing{color:#9a6b1c!important}.strategy-panel h3{margin-top:22px}.strategy-flow{display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr auto 1fr;align-items:stretch;gap:7px}.strategy-flow>div{min-width:0;padding:14px;border:1px solid #d6e4f1;border-radius:10px;background:linear-gradient(180deg,#fff,#f5f9fd)}.strategy-flow b{display:inline-grid;place-items:center;width:23px;height:23px;border-radius:50%;background:#1769c2;color:white;margin-bottom:8px}.strategy-flow strong{display:block;color:#244e75}.strategy-flow p{font-size:10px;line-height:1.5;color:#60758b}.strategy-flow i{align-self:center;color:#72a6d5}.strategy-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}.strategy-grid>div{padding:13px;border-radius:9px;background:#edf5fc}.strategy-grid label{display:block;font-size:10px;color:#60758b;margin-bottom:5px}.strategy-grid strong{font-size:12px;color:#234f78}.citation-policy{font-size:11px;color:#60758b;margin:14px 0 0}@media(max-width:1200px){.strategy-flow{grid-template-columns:1fr}.strategy-flow i{display:none}.strategy-grid{grid-template-columns:repeat(2,1fr)}}
</style>
