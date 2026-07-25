<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { BookOpen, Database, FileText, Layers3, Pencil, Plus, RefreshCw, Save, Settings2, Trash2, Upload, X } from 'lucide-vue-next'
import FloatingPanel from '../components/FloatingPanel.vue'
import { api, type Entity } from '../services/api'
import {
  importKnowledgeFiles,
  KNOWLEDGE_FILE_ACCEPT,
  selectKnowledgeFiles,
  type KnowledgeFileSelection,
  type KnowledgeImportProgress,
} from '../services/knowledgeFiles'
import { useAppStore } from '../stores/app'

const route = useRoute()
const store = useAppStore()
const baseId = computed(() => String(route.params.id || ''))
const base = ref<Entity | null>(null)
const documents = ref<Entity[]>([])
const sources = ref<Entity[]>([])
const overview = ref<Entity | null>(null)
const selectedDocument = ref<Entity | null>(null)
const chunks = ref<Entity[]>([])
const activeTab = ref<'documents' | 'content' | 'chunks' | 'sources' | 'strategy' | 'settings'>('documents')
const chunkLevel = ref<'all' | 'parent' | 'child'>('all')
const chunkLevels = ['all', 'parent', 'child'] as const
const showAddText = ref(false)
const showAddSource = ref(false)
const editingDocument = ref(false)
const fileImportActive = ref(false)
const reindexing = ref(false)
const fileImportErrors = ref<string[]>([])
const fileImportProgress = reactive<KnowledgeImportProgress>({
  total: 0,
  completed: 0,
  imported: 0,
  duplicates: 0,
  failed: 0,
  skipped: 0,
  current: '',
})
const docForm = reactive({ title: '', source: '用户录入', content: '' })
const editDocForm = reactive({ title: '', source: '', content: '' })
const baseForm = reactive({ name: '', discipline: '', description: '' })
const sourceForm = reactive({
  type: 'web', name: '', url: '', max_pages: 1, method: 'GET', headers: '{}',
  response_path: '', connection_url: '', query: 'SELECT * FROM your_table', row_limit: 5000,
})

async function load(selectId?: string) {
  store.loading(true)
  try {
    const [baseRows, documentRows, sourceRows, overviewData] = await Promise.all([
      api.get<Entity[]>('/knowledge-bases'),
      api.get<Entity[]>(`/knowledge-bases/${baseId.value}/documents`),
      api.get<Entity[]>(`/knowledge-bases/${baseId.value}/sources`),
      api.get<Entity>(`/knowledge-bases/${baseId.value}/overview`),
    ])
    base.value = baseRows.find(item => item.id === baseId.value) || null
    if (!base.value) throw new Error('知识库不存在或已被删除')
    documents.value = documentRows
    sources.value = sourceRows
    overview.value = overviewData
    Object.assign(baseForm, {
      name: base.value.name,
      discipline: base.value.discipline,
      description: base.value.description || '',
    })
    const target = documentRows.find(item => item.id === selectId)
      || documentRows.find(item => item.id === selectedDocument.value?.id)
      || documentRows[0]
    if (target) await selectDocument(target)
    else {
      selectedDocument.value = null
      chunks.value = []
    }
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function selectDocument(item: Entity) {
  const [detail, chunkRows] = await Promise.all([
    api.get<Entity>(`/knowledge-documents/${item.id}`),
    api.get<Entity>(`/knowledge-documents/${item.id}/chunks?level=${chunkLevel.value}&limit=500`),
  ])
  selectedDocument.value = detail
  chunks.value = chunkRows.items || []
  Object.assign(editDocForm, {
    title: detail.title,
    source: detail.source,
    content: detail.cleaned_content,
  })
}

async function changeChunkLevel(level: 'all' | 'parent' | 'child') {
  chunkLevel.value = level
  if (!selectedDocument.value) return
  const result = await api.get<Entity>(`/knowledge-documents/${selectedDocument.value.id}/chunks?level=${level}&limit=500`)
  chunks.value = result.items || []
}

async function importFileSelection(input: HTMLInputElement, selection: KnowledgeFileSelection) {
  if (!selection.files.length) {
    store.notify(
      selection.skipped.length ? '所选内容中没有支持的文档格式' : '请选择要导入的文件或文件夹',
      'error',
    )
    input.value = ''
    return
  }
  fileImportActive.value = true
  fileImportErrors.value = []
  Object.assign(fileImportProgress, {
    total: selection.files.length,
    completed: 0,
    imported: 0,
    duplicates: 0,
    failed: 0,
    skipped: selection.skipped.length,
    current: '',
  })
  try {
    const result = await importKnowledgeFiles(baseId.value, selection, progress => {
      Object.assign(fileImportProgress, progress)
    })
    fileImportErrors.value = result.errors
    await load(result.last?.id)
    const summary = `完成 ${result.completed}/${result.total}：新增 ${result.imported}，重复 ${result.duplicates}，失败 ${result.failed}，跳过 ${result.skipped}`
    if (result.failed) store.notify(summary, 'error')
    else store.notify(summary)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    input.value = ''
    fileImportActive.value = false
  }
}

async function uploadFiles(event: Event) {
  const input = event.target as HTMLInputElement
  await importFileSelection(input, selectKnowledgeFiles(input.files || []))
}

async function uploadFolder(event: Event) {
  const input = event.target as HTMLInputElement
  await importFileSelection(input, selectKnowledgeFiles(input.files || []))
}

async function addTextDocument() {
  store.loading(true)
  try {
    const item = await api.post<Entity>(`/knowledge-bases/${baseId.value}/documents/text`, docForm)
    showAddText.value = false
    Object.assign(docForm, { title: '', source: '用户录入', content: '' })
    await load(item.id)
    store.notify('文字资料已创建并建立索引')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function saveDocument() {
  if (!selectedDocument.value) return
  store.loading(true)
  try {
    const updated = await api.patch<Entity>(`/knowledge-documents/${selectedDocument.value.id}`, editDocForm)
    editingDocument.value = false
    await load(updated.id)
    store.notify('正文已重新清洗、分块并向量化')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function deleteDocument() {
  if (!selectedDocument.value || !window.confirm(`删除文档“${selectedDocument.value.title}”及其全部分块和向量？`)) return
  store.loading(true)
  try {
    await api.delete(`/knowledge-documents/${selectedDocument.value.id}`)
    selectedDocument.value = null
    await load()
    store.notify('文档、分块和向量索引已删除')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function addSource() {
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
    store.notify('API 请求头必须是有效 JSON', 'error')
    return
  }
  store.loading(true)
  try {
    const result = await api.post<Entity>(`/knowledge-bases/${baseId.value}/sources/${sourceForm.type}`, payload)
    showAddSource.value = false
    await load()
    if (result.job?.status === 'failed') store.notify(`数据源已保存，但同步失败：${result.job.error}`, 'error')
    else store.notify('数据源已保存并同步')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function syncSource(source: Entity) {
  store.loading(true)
  try {
    const job = await api.post<Entity>(`/knowledge-sources/${source.id}/sync`)
    await load()
    if (job.status === 'failed') store.notify(job.error, 'error')
    else store.notify('数据源同步完成')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function editSource(source: Entity) {
  const name = window.prompt('新的数据源名称', source.name)
  if (!name) return
  const uriLabel = source.source_type === 'database' ? '数据库连接地址' : '网页 / API 地址'
  const uri = window.prompt(uriLabel, source.uri || '')
  if (!uri) return
  if (name === source.name && uri === source.uri) return
  store.loading(true)
  try {
    await api.patch(`/knowledge-sources/${source.id}`, { name, uri })
    await load()
    store.notify('数据源连接已更新，请点击同步应用新配置')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function deleteSource(source: Entity) {
  const deleteDocuments = window.confirm('是否同时删除该数据源已经导入的文档？\n确定：删除文档；取消：仅删除数据源连接。')
  const confirmed = window.confirm(deleteDocuments ? '确认删除数据源及其导入文档？' : '确认仅删除数据源连接？')
  if (!confirmed) return
  await api.delete(`/knowledge-sources/${source.id}?delete_documents=${deleteDocuments}`)
  await load()
  store.notify('数据源已删除')
}

async function saveBase() {
  store.loading(true)
  try {
    base.value = await api.patch<Entity>(`/knowledge-bases/${baseId.value}`, baseForm)
    store.notify('知识库设置已保存')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function reindexBase() {
  if (!base.value || reindexing.value) return
  if (!window.confirm(`重新向量化知识库“${base.value.name}”中的全部检索子块？现有向量索引将按当前 Embedding 配置更新。`)) return
  reindexing.value = true
  store.loading(true)
  try {
    const result = await api.post<Entity>(`/knowledge-bases/${baseId.value}/reindex`)
    await load(selectedDocument.value?.id)
    store.notify(`重新向量化完成：已更新 ${result.embedded_chunks ?? 0} 个检索子块`)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    reindexing.value = false
    store.loading(false)
  }
}

async function deleteBase() {
  if (!base.value || !window.confirm(`永久删除知识库“${base.value.name}”及其文档、分块和向量？此操作不可撤销。`)) return
  await api.delete(`/knowledge-bases/${baseId.value}`)
  window.close()
}

function shortHash(value?: string) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : '—'
}

function closeWindow() {
  window.close()
}

onMounted(load)
</script>

<template>
  <div class="detail-shell">
    <header class="detail-header">
      <div class="detail-brand"><Database :size="25" /><div><span>EVOLUTIONARY KNOWLEDGE</span><h1>{{ base?.name || '知识库详情' }}</h1><p>{{ base?.discipline }} · {{ base?.description }}</p></div></div>
      <button class="btn" @click="closeWindow"><X :size="15" />关闭窗口</button>
    </header>

    <div v-if="overview" class="detail-metrics"><div><b>{{ overview.statistics.documents }}</b><span>文档</span></div><div><b>{{ overview.statistics.sources }}</b><span>数据源</span></div><div><b>{{ overview.statistics.parent_chunks }}</b><span>父块</span></div><div><b>{{ overview.statistics.child_chunks }}</b><span>检索子块</span></div><div><b>{{ overview.statistics.embeddings }}</b><span>向量</span></div></div>

    <nav class="detail-tabs"><button :class="{active:activeTab==='documents'}" @click="activeTab='documents'"><FileText :size="15" />文档管理</button><button :class="{active:activeTab==='content'}" @click="activeTab='content'"><BookOpen :size="15" />正文编辑</button><button :class="{active:activeTab==='chunks'}" @click="activeTab='chunks'"><Layers3 :size="15" />分块索引</button><button :class="{active:activeTab==='sources'}" @click="activeTab='sources'"><Database :size="15" />数据源</button><button :class="{active:activeTab==='strategy'}" @click="activeTab='strategy'"><RefreshCw :size="15" />检索策略</button><button :class="{active:activeTab==='settings'}" @click="activeTab='settings'"><Settings2 :size="15" />设置</button></nav>

    <main class="detail-content">
      <section v-if="activeTab==='documents'" class="panel">
        <div class="panel-head">
          <div><h2>文档与资料</h2><p>可上传单个/多个文档，也可递归导入整个文件夹及其子文件夹。</p></div>
          <div>
            <button class="btn" :disabled="fileImportActive" @click="showAddText=!showAddText"><Plus :size="14" />录入文字</button>
            <label class="btn btn-primary" :class="{disabled:fileImportActive}"><Upload :size="14" />上传文档<input type="file" multiple :accept="KNOWLEDGE_FILE_ACCEPT" hidden :disabled="fileImportActive" @change="uploadFiles"></label>
            <label class="btn btn-primary" :class="{disabled:fileImportActive}"><Database :size="14" />导入文件夹<input type="file" multiple webkitdirectory directory hidden :disabled="fileImportActive" @change="uploadFolder"></label>
          </div>
        </div>
        <div v-if="fileImportActive || fileImportProgress.completed" class="folder-import-progress">
          <div class="progress-track"><i :style="{width:`${fileImportProgress.total ? fileImportProgress.completed / fileImportProgress.total * 100 : 0}%`}"></i></div>
          <div class="facts">
            <span>{{ fileImportActive ? '正在导入' : '导入完成' }} {{ fileImportProgress.completed }}/{{ fileImportProgress.total }}</span>
            <span>新增 {{ fileImportProgress.imported }}</span><span>重复 {{ fileImportProgress.duplicates }}</span>
            <span>失败 {{ fileImportProgress.failed }}</span><span>跳过 {{ fileImportProgress.skipped }}</span>
          </div>
          <p v-if="fileImportProgress.current">当前：{{ fileImportProgress.current }}</p>
          <details v-if="fileImportErrors.length"><summary>查看 {{ fileImportErrors.length }} 条失败信息</summary><p v-for="error in fileImportErrors" :key="error">{{ error }}</p></details>
        </div>
        <div class="document-grid"><button v-for="item in documents" :key="item.id" :class="{active:selectedDocument?.id===item.id}" @click="selectDocument(item)"><FileText :size="19" /><span><strong>{{ item.title }}</strong><small>{{ item.mime_type }} · {{ item.char_count }} 字符</small><small>{{ item.source }}</small></span></button><div v-if="!documents.length" class="empty">还没有文档，请上传、导入文件夹或录入资料。</div></div>
      </section>

      <section v-else-if="activeTab==='content'" class="panel"><div v-if="selectedDocument"><div class="panel-head"><div><h2>清洗后正文</h2><p>保存修改后将删除旧索引并重新执行清洗、父子切分和向量化。</p></div><div><button class="btn" @click="editingDocument=true"><Pencil :size="14" />编辑</button><button class="btn danger" @click="deleteDocument"><Trash2 :size="14" />删除文档</button></div></div><div class="facts"><span>原始 {{ selectedDocument.cleaning_stats?.original_chars ?? selectedDocument.char_count }} 字符</span><span>清洗后 {{ selectedDocument.cleaning_stats?.cleaned_chars ?? selectedDocument.char_count }}</span><span>父块 {{ selectedDocument.parent_chunk_count }}</span><span>子块 {{ selectedDocument.child_chunk_count }}</span><span>向量 {{ selectedDocument.embedding_count }}</span><span>Hash {{ shortHash(selectedDocument.content_hash) }}</span></div><pre class="document-content">{{ selectedDocument.cleaned_content }}</pre></div><div v-else class="empty">请先在“文档管理”中选择文档。</div></section>

      <section v-else-if="activeTab==='chunks'" class="panel"><div class="panel-head"><div><h2>分块与向量索引</h2><p>{{ selectedDocument?.title || '请先选择文档' }}</p></div><div class="level-buttons"><button v-for="level in chunkLevels" :key="level" class="btn" :class="{'btn-primary':chunkLevel===level}" @click="changeChunkLevel(level)">{{ level==='all'?'全部':level==='parent'?'父块':'检索子块' }}</button></div></div><div class="chunk-list"><article v-for="item in chunks" :key="item.id" :class="['chunk',item.level]"><header><b>{{ item.level==='parent'?'父块':'子块' }} #{{ item.chunk_index }}</b><span>{{ item.metadata?.locator }}</span><em>{{ item.embedding?.indexed ? `${item.embedding.dimensions} 维 · 已索引` : '上下文块' }}</em></header><p>{{ item.content }}</p><footer>Token {{ item.token_count }} · Hash {{ shortHash(item.content_hash) }}<span v-if="item.parent_chunk_id"> · Parent {{ shortHash(item.parent_chunk_id) }}</span><span v-if="item.embedding?.model"> · {{ item.embedding.provider }}/{{ item.embedding.model }}</span></footer></article><div v-if="!chunks.length" class="empty">没有符合筛选条件的分块。</div></div></section>

      <section v-else-if="activeTab==='sources'" class="panel"><div class="panel-head"><div><h2>多源数据连接</h2><p>管理文件、网页、数据库和 API/第三方数据连接。</p></div><button class="btn btn-primary" @click="showAddSource=true"><Plus :size="14" />添加数据源</button></div><div class="source-list"><article v-for="item in sources" :key="item.id"><div class="source-icon"><Database :size="18" /></div><div><strong>{{ item.name }}</strong><p>{{ item.source_type }} · {{ item.uri }}</p><small :class="item.status">{{ item.status }}<template v-if="item.last_error"> · {{ item.last_error }}</template></small></div><div class="source-actions"><button class="btn" @click="syncSource(item)"><RefreshCw :size="13" />同步</button><button class="btn" @click="editSource(item)"><Pencil :size="13" />编辑连接</button><button class="btn danger" @click="deleteSource(item)"><Trash2 :size="13" />删除</button></div></article><div v-if="!sources.length" class="empty">尚未添加数据源。</div></div></section>

      <section v-else-if="activeTab==='strategy'" class="panel"><div class="panel-head"><div><h2>当前检索策略</h2><p>以下参数直接读取后端配置。</p></div></div><div v-if="overview" class="strategy-flow"><article><b>1</b><strong>查询改写</strong><p>{{ overview.retrieval_strategy.query_rewrite }}</p></article><i>→</i><article><b>2</b><strong>多路召回</strong><p>{{ overview.retrieval_strategy.retrievers.join(' + ') }}</p></article><i>→</i><article><b>3</b><strong>RRF 融合</strong><p>候选 Top {{ overview.retrieval_strategy.candidate_k }}</p></article><i>→</i><article><b>4</b><strong>Rerank</strong><p>{{ overview.retrieval_strategy.rerank_model }}</p></article><i>→</i><article><b>5</b><strong>上下文</strong><p>Top {{ overview.retrieval_strategy.top_k }} · {{ overview.retrieval_strategy.context_char_budget }} 字符</p></article></div><h3>向量索引实例</h3><div v-if="overview?.vector_indexes?.length" class="source-list"><article v-for="item in overview.vector_indexes" :key="`${item.model}-${item.dimensions}`"><div class="source-icon"><Layers3 :size="18" /></div><div><strong>{{ item.model }}</strong><p>{{ item.provider }} · {{ item.dimensions }} 维</p></div><b>{{ item.count }} 个向量</b></article></div><div v-else class="empty">尚未建立向量索引。</div></section>

      <section v-else class="panel">
        <div class="panel-head"><div><h2>知识库设置</h2><p>修改基础信息、维护向量索引或永久删除该知识库。</p></div></div>
        <div class="settings-form">
          <label>名称<input v-model="baseForm.name" class="input"></label>
          <label>学科<input v-model="baseForm.discipline" class="input"></label>
          <label class="wide">说明<textarea v-model="baseForm.description" class="textarea"></textarea></label>
          <div class="wide actions"><button class="btn btn-primary" @click="saveBase"><Save :size="14" />保存设置</button><button class="btn danger" :disabled="reindexing" @click="deleteBase"><Trash2 :size="14" />删除整个知识库</button></div>
        </div>
        <div class="settings-maintenance">
          <div><strong>向量索引维护</strong><p>使用当前 Embedding 配置重新生成全部检索子块向量。完成后会自动刷新上方统计和分块状态。</p></div>
          <button class="btn btn-primary" :disabled="reindexing" @click="reindexBase"><RefreshCw :size="14" :class="{spin:reindexing}" />{{ reindexing ? '正在重新向量化…' : '重新向量化' }}</button>
        </div>
      </section>
    </main>

    <FloatingPanel v-model="showAddText" title="录入文字资料" eyebrow="NEW DOCUMENT" description="创建后将自动完成清洗、父子切分与向量索引。" size="large">
      <div class="editor-form floating-editor"><input v-model="docForm.title" class="input" placeholder="资料标题"><input v-model="docForm.source" class="input" placeholder="来源"><textarea v-model="docForm.content" class="textarea" placeholder="正文内容"></textarea><button class="btn btn-primary" @click="addTextDocument">创建并索引</button></div>
    </FloatingPanel>

    <FloatingPanel v-model="editingDocument" :title="`编辑 ${selectedDocument?.title || '文档'}`" eyebrow="DOCUMENT EDITOR" description="保存后会删除旧索引，并按当前策略重新清洗、切分和向量化。" size="wide">
      <div class="editor-form full-editor floating-editor"><input v-model="editDocForm.title" class="input"><input v-model="editDocForm.source" class="input"><textarea v-model="editDocForm.content" class="textarea"></textarea><button class="btn btn-primary" @click="saveDocument"><Save :size="14" />保存并重建索引</button></div>
    </FloatingPanel>

    <FloatingPanel v-model="showAddSource" title="添加数据源" eyebrow="DATA CONNECTION" description="配置网页、数据库或 API 数据连接，并在保存后立即同步。" size="large">
      <div class="source-editor floating-editor"><select v-model="sourceForm.type" class="input"><option value="web">网页</option><option value="database">数据库</option><option value="api">API / 第三方</option></select><input v-model="sourceForm.name" class="input" placeholder="数据源名称"><template v-if="sourceForm.type==='web'"><input v-model="sourceForm.url" class="input wide" placeholder="https://example.edu/knowledge"><input v-model.number="sourceForm.max_pages" type="number" min="1" max="20" class="input" placeholder="抓取页数"></template><template v-else-if="sourceForm.type==='database'"><input v-model="sourceForm.connection_url" class="input wide" placeholder="sqlite:///D:/data/source.db"><textarea v-model="sourceForm.query" class="textarea wide"></textarea></template><template v-else><input v-model="sourceForm.url" class="input wide" placeholder="https://api.example.com/data"><select v-model="sourceForm.method" class="input"><option>GET</option><option>POST</option></select><input v-model="sourceForm.response_path" class="input" placeholder="data.items"><textarea v-model="sourceForm.headers" class="textarea wide" placeholder='{"Authorization":"Bearer ..."}'></textarea></template><button class="btn btn-primary" @click="addSource">保存并同步</button></div>
    </FloatingPanel>
  </div>
</template>

<style scoped>
.detail-shell{max-width:1500px;margin:auto}.detail-header{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border:1px solid #d6e4f1;border-radius:14px;background:#fff;box-shadow:0 8px 30px #194f7912}.detail-brand{display:flex;align-items:center;gap:14px;color:#1769c2}.detail-brand span{font-size:9px;letter-spacing:1.8px}.detail-brand h1{margin:2px 0;font-size:22px;color:#173d61}.detail-brand p{margin:0;color:#6a8093;font-size:11px}.detail-metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:14px 0}.detail-metrics div{display:flex;align-items:baseline;gap:8px;padding:13px 17px;border:1px solid #d8e5f1;border-radius:10px;background:#fff}.detail-metrics b{font-size:21px;color:#1769c2}.detail-metrics span{font-size:11px;color:#63798d}.detail-tabs{display:flex;gap:5px;padding:6px;border:1px solid #d6e4f1;border-radius:11px;background:#fff}.detail-tabs button{display:flex;align-items:center;gap:6px;border:0;border-radius:8px;background:transparent;padding:9px 13px;color:#4d687f;cursor:pointer}.detail-tabs button.active{background:#1769c2;color:#fff}.detail-content{margin-top:13px}.panel{border:1px solid #d6e4f1;border-radius:13px;background:#fff;min-height:430px;padding:20px}.panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:17px}.panel-head h2{margin:0;color:#244d72}.panel-head p{margin:4px 0 0;font-size:11px;color:#6a8093}.panel-head>div:last-child{display:flex;gap:7px}.disabled{pointer-events:none;opacity:.55}.spin{animation:spin .85s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.folder-import-progress{margin:-4px 0 17px;padding:12px 14px;border:1px solid #cfe1f1;border-radius:10px;background:#f5faff}.folder-import-progress .progress-track{height:5px;margin-bottom:10px;overflow:hidden;border-radius:5px;background:#dceaf6}.folder-import-progress .progress-track i{display:block;height:100%;border-radius:5px;background:#1769c2;transition:width .2s}.folder-import-progress p,.folder-import-progress details{margin:6px 0 0;font-size:10px;color:#5c7489}.folder-import-progress details p{color:#a34242}.document-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.document-grid>button{display:flex;text-align:left;gap:10px;border:1px solid #d7e4ef;border-radius:10px;background:#fff;padding:13px;color:#1769c2;cursor:pointer}.document-grid>button.active{border-color:#1769c2;background:#edf6ff}.document-grid span{display:flex;min-width:0;flex-direction:column;gap:4px}.document-grid strong{color:#2b4f70}.document-grid small{color:#6a8093;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.editor-form,.source-editor,.settings-form{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:17px;padding:14px;border:1px solid #d8e5f0;border-radius:10px;background:#f7fbff}.floating-editor{margin:0}.editor-form textarea,.editor-form button,.wide{grid-column:1/-1}.editor-form textarea{min-height:160px}.full-editor textarea{min-height:420px}.facts{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:12px}.facts span{padding:6px 9px;border-radius:7px;background:#edf5fc;color:#567087;font-size:10px}.document-content{max-height:590px;overflow:auto;white-space:pre-wrap;margin:0;padding:20px;border:1px solid #d9e5ef;border-radius:9px;font:13px/1.85 "Microsoft YaHei",sans-serif;color:#294760}.level-buttons{display:flex;gap:6px}.chunk-list{display:grid;gap:9px;max-height:610px;overflow:auto}.chunk{border:1px solid #d8e4ef;border-left:4px solid #63a2df;border-radius:9px;padding:12px 14px}.chunk.parent{border-left-color:#254f78;background:#f7fbff}.chunk header{display:flex;gap:10px;align-items:center}.chunk header span,.chunk footer{font-size:10px;color:#718598}.chunk header em{margin-left:auto;font-style:normal;font-size:10px;color:#15805d}.chunk p{white-space:pre-wrap;font-size:12px;line-height:1.7;color:#34516b}.source-editor{grid-template-columns:180px 1fr}.source-list{display:grid;gap:9px}.source-list article{display:flex;align-items:center;gap:11px;padding:13px;border:1px solid #d8e4ef;border-radius:10px}.source-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:9px;background:#eaf4fd;color:#1769c2}.source-list article>div:nth-child(2){flex:1}.source-list strong{color:#294e70}.source-list p{margin:4px 0;font-size:11px;color:#667d92}.source-list small.ready,.source-list small.completed{color:#14805d}.source-list small.failed{color:#b13f3f}.source-actions{display:flex;gap:6px}.danger{color:#b13f3f!important;border-color:#ebcaca!important;background:#fff8f8!important}.strategy-flow{display:grid;grid-template-columns:1fr auto 1fr auto 1fr auto 1fr auto 1fr;align-items:center;gap:8px;margin-bottom:25px}.strategy-flow article{min-height:105px;padding:14px;border:1px solid #d8e5f0;border-radius:10px;background:#f8fbfe}.strategy-flow article b{display:grid;place-items:center;width:24px;height:24px;border-radius:50%;background:#1769c2;color:#fff}.strategy-flow article strong{display:block;margin-top:8px;color:#285173}.strategy-flow article p{font-size:10px;line-height:1.5;color:#647b90}.strategy-flow i{color:#77a9d5}.settings-form label{display:grid;gap:6px;color:#4a667f;font-size:11px}.settings-form .actions{display:flex;justify-content:space-between}.settings-maintenance{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px;border:1px solid #b9d9f2;border-radius:11px;background:linear-gradient(110deg,#f3f9fe,#eaf5fd)}.settings-maintenance strong{color:#214f75;font-size:13px}.settings-maintenance p{margin:5px 0 0;color:#678095;font-size:10px}.settings-maintenance .btn{flex:0 0 auto}.empty{grid-column:1/-1;padding:40px;text-align:center;color:#7b8fa2}@media(max-width:1100px){.document-grid{grid-template-columns:repeat(2,1fr)}.strategy-flow{grid-template-columns:1fr}.strategy-flow>i{display:none}}
</style>
