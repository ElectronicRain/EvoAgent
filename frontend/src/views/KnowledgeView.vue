<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { BookOpen, Database, FileText, Globe2, Plus, Search, Settings2, Upload } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const bases = ref<Entity[]>([])
const selected = ref<Entity | null>(null)
const documents = ref<Entity[]>([])
const sources = ref<Entity[]>([])
const answer = ref('')
const results = ref<Entity[]>([])
const citations = ref<Entity[]>([])
const trace = ref<Entity | null>(null)
const search = ref('二维结构化网格质量应如何评价？')
const createBase = ref(false)
const addText = ref(false)
const addSource = ref(false)
const showConfig = ref(false)
const baseForm = reactive({ name: '', discipline: '', description: '' })
const docForm = reactive({ title: '', source: '用户录入', content: '' })
const sourceForm = reactive({
  type: 'web', name: '', url: '', max_pages: 1, method: 'GET', headers: '{}',
  response_path: '', connection_url: '', query: 'SELECT * FROM your_table', row_limit: 5000,
})
const providerConfig = reactive<Entity>({
  embedding_base_url: '', embedding_model: '', rerank_base_url: '', rerank_model: '',
  api_key: '', llm_endpoint_id: null, top_k: 6, candidate_k: 30, context_char_budget: 12000,
})
const endpoints = ref<Entity[]>([])

async function load() {
  store.loading(true)
  try {
    bases.value = await api.get('/knowledge-bases')
    selected.value ||= bases.value[0] || null
    const [config, modelEndpoints] = await Promise.all([
      api.get<Entity>('/knowledge/config'),
      api.get<Entity[]>('/model-endpoints'),
    ])
    Object.assign(providerConfig, config, { api_key: '' })
    endpoints.value = modelEndpoints
    if (selected.value) await choose(selected.value)
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}

async function choose(item: Entity) {
  selected.value = item
  const [documentRows, sourceRows] = await Promise.all([
    api.get<Entity[]>(`/knowledge-bases/${item.id}/documents`),
    api.get<Entity[]>(`/knowledge-bases/${item.id}/sources`),
  ])
  documents.value = documentRows
  sources.value = sourceRows
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
  store.loading(true)
  answer.value = ''
  results.value = []
  try {
    const response = await api.post<Entity>('/knowledge/query', {
      query: search.value,
      knowledge_base_ids: selected.value ? [selected.value.id] : [],
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

  <div class="split">
    <section class="card">
      <div class="card-header"><h2>知识库</h2><span>{{ bases.length }} 个</span></div>
      <div class="card-body grid grid-2">
        <button v-for="item in bases" :key="item.id" class="list-item" :class="{ active: selected?.id === item.id }" @click="choose(item)">
          <div style="display:flex;gap:11px;text-align:left"><div class="metric-icon"><Database :size="18" /></div><div><strong>{{ item.name }}</strong><p>{{ item.discipline }} · {{ item.document_count }} 份资料</p></div></div>
        </button>
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

  <section v-if="selected" class="card" style="margin-top:20px">
    <div class="card-header"><h2>{{ selected.name }} · 数据源</h2><div style="display:flex;gap:8px"><button class="btn btn-sm" @click="addSource=true"><Globe2 :size="14" />网页 / 数据库 / API</button><button class="btn btn-sm" @click="addText=true"><FileText :size="14" />粘贴文本</button><label class="btn btn-sm"><Upload :size="14" />上传文档<input type="file" accept=".pdf,.docx,.pptx,.txt,.md,.csv,.json,.html" hidden @change="upload"></label><button class="btn btn-sm" @click="reindex">重新向量化</button></div></div>
    <div class="table-wrap"><table><thead><tr><th>资料名称</th><th>来源</th><th>类型</th><th>状态</th><th>字符数</th></tr></thead><tbody><tr v-for="item in documents" :key="item.id"><td>{{ item.title }}</td><td>{{ item.source }}</td><td>{{ item.mime_type }}</td><td>{{ item.status }}</td><td>{{ item.char_count }}</td></tr></tbody></table><div v-if="!documents.length" class="empty"><FileText :size="28" /><br>还没有资料</div></div>
    <div v-if="sources.length" class="card-body"><div class="list-stack"><div v-for="item in sources" :key="item.id" class="list-item"><div><strong>{{ item.name }}</strong><p>{{ item.source_type }} · {{ item.uri }}</p></div><span>{{ item.status }}</span></div></div></div>
  </section>

  <section v-if="addText" class="card" style="margin-top:20px"><div class="card-header"><h2>录入资料</h2><button class="btn btn-sm" @click="addText=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>标题</label><input v-model="docForm.title" class="input"></div><div class="field"><label>来源</label><input v-model="docForm.source" class="input"></div><div class="field full"><label>正文</label><textarea v-model="docForm.content" class="textarea" style="min-height:220px" /></div><div class="field full"><button class="btn btn-primary" @click="saveText">清洗、切分并向量化</button></div></div></section>

  <section v-if="addSource" class="card" style="margin-top:20px"><div class="card-header"><h2>添加外部数据源</h2><button class="btn btn-sm" @click="addSource=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>类型</label><select v-model="sourceForm.type" class="input"><option value="web">网页</option><option value="database">数据库</option><option value="api">API / 第三方</option></select></div><div class="field"><label>名称</label><input v-model="sourceForm.name" class="input"></div><template v-if="sourceForm.type==='web'"><div class="field full"><label>网页 URL</label><input v-model="sourceForm.url" class="input"></div><div class="field"><label>最多抓取页数</label><input v-model.number="sourceForm.max_pages" type="number" min="1" max="20" class="input"></div></template><template v-else-if="sourceForm.type==='api'"><div class="field full"><label>API URL</label><input v-model="sourceForm.url" class="input"></div><div class="field"><label>方法</label><select v-model="sourceForm.method" class="input"><option>GET</option><option>POST</option></select></div><div class="field"><label>JSON 响应路径</label><input v-model="sourceForm.response_path" class="input" placeholder="data.items"></div><div class="field full"><label>请求头 JSON（将加密保存）</label><textarea v-model="sourceForm.headers" class="textarea" /></div></template><template v-else><div class="field full"><label>SQLAlchemy 连接地址（凭据将加密保存）</label><input v-model="sourceForm.connection_url" class="input" placeholder="sqlite:///D:/data/source.db"></div><div class="field full"><label>只读 SELECT / WITH 查询</label><textarea v-model="sourceForm.query" class="textarea" /></div></template><div class="field full"><button class="btn btn-primary" @click="saveSource">保存并同步</button></div></div></section>

  <section class="card" style="margin-top:20px"><div class="card-header"><h2>知识库问答</h2><BookOpen :size="18" color="#1769c2" /></div><div class="card-body"><div style="display:flex;gap:9px"><input v-model="search" class="input" @keyup.enter="ask"><button class="btn btn-primary" @click="ask"><Search :size="14" />提问</button></div><article v-if="answer" class="notice" style="margin-top:16px;white-space:pre-wrap;line-height:1.8">{{ answer }}</article><div class="list-stack" style="margin-top:14px"><div v-for="(item,index) in results" :key="item.id" class="list-item"><div><strong>[资料 {{ index+1 }}] {{ item.title }}</strong><p style="font-size:12px;color:#385570;line-height:1.7">{{ item.content }}</p><a v-if="item.metadata?.url" :href="item.metadata.url" target="_blank" rel="noreferrer">打开原始来源</a><p>{{ item.citation }}</p></div></div></div><p v-if="trace" style="font-size:11px;color:#60758b;margin-top:12px">召回 {{ trace.fused_candidates }} 条候选，Rerank {{ trace.reranked }} 条，最终引用 {{ citations.length }} 条；Embedding：{{ trace.embedding_model }}</p></div></section>

  <section v-if="showConfig" class="card" style="margin-top:20px"><div class="card-header"><h2>知识库模型配置</h2><button class="btn btn-sm" @click="showConfig=false">取消</button></div><div class="card-body form-grid"><div class="field full"><label>SiliconFlow API Key（留空表示保持不变）</label><input v-model="providerConfig.api_key" type="password" class="input" :placeholder="providerConfig.has_api_key ? '已安全保存' : 'sk-...'" autocomplete="new-password"></div><div class="field"><label>Embedding 模型</label><input v-model="providerConfig.embedding_model" class="input"></div><div class="field"><label>Embedding URL</label><input v-model="providerConfig.embedding_base_url" class="input"></div><div class="field"><label>Rerank 模型</label><input v-model="providerConfig.rerank_model" class="input"></div><div class="field"><label>Rerank URL</label><input v-model="providerConfig.rerank_base_url" class="input"></div><div class="field"><label>答案生成模型端点</label><select v-model="providerConfig.llm_endpoint_id" class="input"><option :value="null">离线摘要</option><option v-for="item in endpoints" :key="item.id" :value="item.id">{{ item.name }} · {{ item.default_model }}</option></select></div><div class="field"><label>最终 Top-K</label><input v-model.number="providerConfig.top_k" type="number" min="1" max="20" class="input"></div><div class="field"><label>候选数量</label><input v-model.number="providerConfig.candidate_k" type="number" min="5" max="100" class="input"></div><div class="field full" style="display:flex;gap:8px"><button class="btn" @click="testConfig">测试连接</button><button class="btn btn-primary" @click="saveConfig">保存配置</button></div></div></section>
</template>
