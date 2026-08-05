<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  AlertTriangle, Boxes, Cable, CheckCircle2, Eye, FileArchive,
  Image as ImageIcon, KeyRound, Plus, RefreshCw, Server, ShieldCheck, Sparkles, Upload,
} from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import FloatingPanel from '../components/FloatingPanel.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const tab = ref<'models'|'mcp'|'skills'>('models')
const endpoints = ref<Entity[]>([])
const extensions = ref<Entity[]>([])
const skills = ref<Entity[]>([])
const showForm = ref(false)
const uploadInput = ref<HTMLInputElement | null>(null)
const uploadOpen = ref(false)
const uploadFile = ref<File | null>(null)
const uploadReport = ref<Entity | null>(null)
const skillDetail = ref<Entity | null>(null)
const skillDetailOpen = ref(false)
const endpointForm = reactive({ name:'', modality:'chat', provider_type:'openai-compatible', base_url:'', api_key:'', default_model:'', headersText:'{}', optionsText:'{}', timeout_seconds:90, enabled:true })
const mcpForm = reactive({ name:'', description:'', transport:'http', url:'', command:'', argsText:'[]' })
const imageOptionsPlaceholder = '例如：{"size":"1024x1024","quality":"standard"}'
const verifiedCount = computed(() => skills.value.filter(item => item.validation_status === 'verified').length)

async function load() {
  store.loading(true)
  try {
    [endpoints.value, extensions.value, skills.value] = await Promise.all([
      api.get('/model-endpoints'), api.get('/extensions'), api.get('/skills'),
    ])
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function saveEndpoint() {
  store.loading(true)
  try {
    await api.post('/model-endpoints', {
      name:endpointForm.name, modality:endpointForm.modality, provider_type:endpointForm.provider_type,
      base_url:endpointForm.base_url, api_key:endpointForm.api_key, default_model:endpointForm.default_model,
      headers:JSON.parse(endpointForm.headersText || '{}'), request_options:JSON.parse(endpointForm.optionsText || '{}'),
      timeout_seconds:endpointForm.timeout_seconds, enabled:endpointForm.enabled,
    })
    store.notify(endpointForm.modality === 'image' ? '图片生成 API 已保存' : '对话模型 API 已保存')
    showForm.value = false
    await load()
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function testEndpoint(id:string) {
  store.loading(true)
  try {
    const result:Entity = await api.post(`/model-endpoints/${id}/test`)
    store.notify(result.status === 'healthy' ? '接口连接正常' : result.error, result.status === 'healthy' ? 'success' : 'error')
    await load()
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function saveMcp() {
  store.loading(true)
  try {
    const config = mcpForm.transport === 'http'
      ? { transport:'http', url:mcpForm.url }
      : { transport:'stdio', command:mcpForm.command, args:JSON.parse(mcpForm.argsText || '[]') }
    await api.post('/extensions', { name:mcpForm.name, kind:'mcp', description:mcpForm.description, config, permissions:['network','tools'] })
    store.notify('MCP 服务已注册')
    showForm.value = false
    await load()
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function testExtension(id:string) {
  store.loading(true)
  try {
    const result:Entity = await api.post(`/extensions/${id}/test`)
    store.notify(result.status === 'healthy' ? 'MCP 握手成功' : result.error, result.status === 'healthy' ? 'success' : 'error')
    await load()
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function sync(kind:'skills'|'plugins') {
  store.loading(true)
  try {
    await api.post(kind === 'skills' ? '/skills/sync' : '/extensions/sync-plugins')
    store.notify(kind === 'skills' ? 'Skill 已重新扫描并同步' : '本地插件目录同步完成')
    await load()
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
function chooseUpload() {
  uploadFile.value = null
  uploadReport.value = null
  uploadOpen.value = true
}
function onUploadSelected(event: Event) {
  uploadFile.value = (event.target as HTMLInputElement).files?.[0] || null
  uploadReport.value = null
}
async function uploadSkill() {
  if (!uploadFile.value) return
  store.loading(true)
  try {
    const result:Entity = await api.upload('/skills/upload', uploadFile.value)
    uploadReport.value = result.report
    if (result.accepted) {
      store.notify('Skill 已通过格式与恶意风险校验，可以分配给 Agent')
      await load()
    } else {
      store.notify('Skill 未通过校验，未安装也不会被 Agent 调用', 'error')
    }
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function openSkill(item:Entity) {
  store.loading(true)
  try {
    skillDetail.value = await api.get(`/skills/${item.id}`)
    skillDetailOpen.value = true
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
async function revalidateSkill() {
  if (!skillDetail.value) return
  store.loading(true)
  try {
    const updated:Entity = await api.post(`/skills/${skillDetail.value.id}/validate`)
    skillDetail.value = updated
    store.notify(updated.validation_status === 'verified' ? '重新校验通过' : '重新校验未通过', updated.validation_status === 'verified' ? 'success' : 'error')
    await load()
  } catch (error:any) {
    store.notify(error.message, 'error')
  } finally {
    store.loading(false)
  }
}
function riskLabel(risk:string) {
  return ({ none:'无风险', low:'低风险', medium:'中风险', high:'高风险', critical:'严重风险', unknown:'未校验' } as Record<string,string>)[risk] || risk
}
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="EXTENSIBILITY" title="扩展与模型" description="集中管理模型 API、MCP 服务与经过校验的本地 Skills。">
    <button class="btn btn-primary" @click="tab==='skills' ? chooseUpload() : showForm=true">
      <Upload v-if="tab==='skills'" :size="15" /><Plus v-else :size="15" />
      {{ tab==='models' ? '添加模型接口' : tab==='mcp' ? '添加 MCP' : '上传 Skill' }}
    </button>
  </PageHeader>
  <div class="card">
    <div class="tabs">
      <button class="tab" :class="{active:tab==='models'}" @click="tab='models';showForm=false"><Server :size="14" /> 模型 API</button>
      <button class="tab" :class="{active:tab==='mcp'}" @click="tab='mcp';showForm=false"><Cable :size="14" /> MCP 与插件</button>
      <button class="tab" :class="{active:tab==='skills'}" @click="tab='skills';showForm=false"><Sparkles :size="14" /> Skills</button>
    </div>

    <div v-if="tab==='models'" class="card-body">
      <div class="notice" style="margin-bottom:15px">支持 OpenAI 兼容的对话与图片接口。API Key 加密保存且不会返回明文。</div>
      <div class="grid grid-3">
        <article v-for="item in endpoints" :key="item.id" class="card" style="box-shadow:none"><div class="card-body">
          <div style="display:flex;justify-content:space-between"><div class="metric-icon"><ImageIcon v-if="item.modality==='image'" :size="18" /><KeyRound v-else :size="18" /></div><StatusBadge :status="item.health" /></div>
          <h3 style="font-size:14px;color:#153b62">{{ item.name }}</h3><p style="font-size:10px;color:#6b8095;word-break:break-all">{{ item.base_url }}</p>
          <span class="tag">{{ item.modality==='image'?'图片生成':'对话回答' }}</span><span class="tag">{{ item.default_model }}</span>
          <button class="btn btn-sm" style="width:100%;margin-top:13px" @click="testEndpoint(item.id)">测试连通性</button>
        </div></article>
        <div v-if="!endpoints.length" class="empty">尚未添加真实模型接口。</div>
      </div>
    </div>

    <div v-if="tab==='mcp'" class="card-body">
      <div style="display:flex;justify-content:flex-end;margin-bottom:10px"><button class="btn btn-sm" @click="sync('plugins')"><RefreshCw :size="13" />同步本地插件</button></div>
      <div class="list-stack">
        <div v-for="item in extensions" :key="item.id" class="list-item"><div style="display:flex;gap:11px"><div class="metric-icon"><Boxes :size="18" /></div><div><strong>{{ item.name }}</strong><p>{{ item.kind.toUpperCase() }} · {{ item.description }}</p></div></div><div style="display:flex;gap:8px;align-items:center"><StatusBadge :status="item.health" /><button v-if="item.kind==='mcp'" class="btn btn-sm" @click="testExtension(item.id)">连接测试</button></div></div>
        <div v-if="!extensions.length" class="empty">尚未接入 MCP 或插件</div>
      </div>
    </div>

    <div v-if="tab==='skills'" class="card-body">
      <div class="notice skill-notice"><ShieldCheck :size="18" /><span>只有格式正确且静态恶意扫描通过的 Skill 才会启用并出现在 Agent 工厂中。当前已验证 {{ verifiedCount }} / {{ skills.length }}。</span><button class="btn btn-sm" @click="sync('skills')"><RefreshCw :size="13" />重新扫描本地目录</button></div>
      <div class="grid grid-3">
        <article v-for="item in skills" :key="item.id" class="card skill-card" @click="openSkill(item)"><div class="card-body">
          <div class="skill-card-head"><Sparkles :size="19" color="#1769c2" /><span class="validation-badge" :class="item.validation_status"><CheckCircle2 v-if="item.validation_status==='verified'" :size="12" /><AlertTriangle v-else :size="12" />{{ item.validation_status==='verified'?'已验证':'已拒绝' }}</span></div>
          <h3>{{ item.name }}</h3><p>{{ item.description }}</p>
          <div><span class="tag">v{{ item.version }}</span><span class="tag">{{ riskLabel(item.risk_level) }}</span></div>
          <button class="btn btn-sm view-button"><Eye :size="12" />查看指令与校验报告</button>
        </div></article>
        <div v-if="!skills.length" class="empty">尚无 Skill。可上传 SKILL.md 或 ZIP 包。</div>
      </div>
    </div>
  </div>

  <FloatingPanel v-if="tab==='models'" v-model="showForm" title="自定义模型 API 接口" eyebrow="MODEL ENDPOINT" description="配置对话或图片生成兼容接口。" size="large">
    <div class="form-grid"><div class="field"><label>接口名称</label><input v-model="endpointForm.name" class="input"></div><div class="field"><label>模型能力</label><select v-model="endpointForm.modality" class="select"><option value="chat">对话回答</option><option value="image">图片生成</option></select></div><div class="field"><label>协议类型</label><select v-model="endpointForm.provider_type" class="select"><option value="openai-compatible">OpenAI Compatible</option><option value="spark-compatible">Spark Compatible</option><option value="custom">Custom Compatible</option></select></div><div class="field full"><label>Base URL</label><input v-model="endpointForm.base_url" class="input" placeholder="https://example.com/v1"></div><div class="field"><label>API Key</label><input v-model="endpointForm.api_key" type="password" class="input"></div><div class="field"><label>默认模型</label><input v-model="endpointForm.default_model" class="input"></div><div class="field"><label>请求超时（秒）</label><input v-model.number="endpointForm.timeout_seconds" type="number" min="5" max="300" class="input"></div><div class="field"><label>接口状态</label><label style="display:flex;align-items:center;gap:8px;height:38px"><input v-model="endpointForm.enabled" type="checkbox">启用</label></div><div class="field full"><label>自定义请求头（JSON）</label><textarea v-model="endpointForm.headersText" class="textarea" /></div><div class="field full"><label>附加请求参数（JSON）</label><textarea v-model="endpointForm.optionsText" class="textarea" :placeholder="endpointForm.modality==='image'?imageOptionsPlaceholder:'{}'" /></div><div class="field full"><button class="btn btn-primary" @click="saveEndpoint">加密保存接口</button></div></div>
  </FloatingPanel>
  <FloatingPanel v-if="tab==='mcp'" v-model="showForm" title="注册 MCP 服务" eyebrow="MCP SERVICE" description="接入 Streamable HTTP 或本地 stdio MCP 服务。" size="large">
    <div class="form-grid"><div class="field"><label>名称</label><input v-model="mcpForm.name" class="input"></div><div class="field"><label>Transport</label><select v-model="mcpForm.transport" class="select"><option value="http">Streamable HTTP</option><option value="stdio">stdio</option></select></div><div class="field full"><label>说明</label><input v-model="mcpForm.description" class="input"></div><div v-if="mcpForm.transport==='http'" class="field full"><label>服务 URL</label><input v-model="mcpForm.url" class="input"></div><template v-else><div class="field"><label>Command</label><input v-model="mcpForm.command" class="input"></div><div class="field"><label>Args（JSON 数组）</label><input v-model="mcpForm.argsText" class="input"></div></template><div class="field full"><button class="btn btn-primary" @click="saveMcp">注册服务</button></div></div>
  </FloatingPanel>

  <FloatingPanel v-model="uploadOpen" title="上传并校验 Skill" eyebrow="SKILL SECURITY GATE" description="支持单个 SKILL.md 或包含一个 Skill 的 ZIP 包。校验失败不会安装。" size="large">
    <div class="upload-drop" @click="uploadInput?.click()"><FileArchive :size="28" /><strong>{{ uploadFile?.name || '选择 SKILL.md 或 ZIP' }}</strong><span>最大 6 MB；检查目录越界、可执行文件、凭据外传、持久化、提示注入和破坏性命令。</span><input ref="uploadInput" type="file" accept=".md,.zip" hidden @change="onUploadSelected"></div>
    <button class="btn btn-primary" style="margin-top:14px" :disabled="!uploadFile" @click="uploadSkill"><ShieldCheck :size="14" />校验并安装</button>
    <section v-if="uploadReport" class="report" :class="uploadReport.status">
      <h3>{{ uploadReport.status==='verified'?'校验通过，可以使用':'校验未通过，已阻止安装' }}</h3>
      <p>Skill 格式：{{ uploadReport.is_skill?'有效':'无效' }} · 风险：{{ riskLabel(uploadReport.risk_level) }} · 文件 {{ uploadReport.files?.length || 0 }} 个</p>
      <div v-if="uploadReport.findings?.length" class="finding-list"><div v-for="(finding,index) in uploadReport.findings" :key="index"><AlertTriangle :size="13" /><span><strong>{{ finding.message }}</strong><small>{{ finding.path }}<template v-if="finding.line">:{{ finding.line }}</template> · {{ finding.code }}</small></span></div></div>
    </section>
  </FloatingPanel>

  <FloatingPanel v-model="skillDetailOpen" :title="skillDetail?.name || 'Skill 详情'" eyebrow="VERIFIED SKILL" description="查看实际指令、包内容与静态扫描结论。" size="wide">
    <template v-if="skillDetail">
      <div class="detail-summary"><span class="validation-badge" :class="skillDetail.validation_status">{{ skillDetail.validation_status==='verified'?'已验证可调用':'未通过校验' }}</span><span class="tag">{{ riskLabel(skillDetail.risk_level) }}</span><span class="hash">SHA-256 {{ skillDetail.content_hash || '无' }}</span><button class="btn btn-sm" @click="revalidateSkill"><RefreshCw :size="12" />重新校验</button></div>
      <div class="detail-grid"><section><h3>Skill 指令</h3><pre>{{ skillDetail.instructions }}</pre></section><section><h3>校验报告</h3><div class="check-list"><div v-for="(passed,key) in skillDetail.validation_report?.checks" :key="key" :class="{passed}"><CheckCircle2 v-if="passed" :size="13" /><AlertTriangle v-else :size="13" />{{ key }}：{{ passed?'通过':'未通过' }}</div></div><div class="finding-list"><div v-for="(finding,index) in skillDetail.validation_report?.findings || []" :key="index"><AlertTriangle :size="13" /><span><strong>{{ finding.message }}</strong><small>{{ finding.path }}<template v-if="finding.line">:{{ finding.line }}</template></small></span></div><p v-if="!skillDetail.validation_report?.findings?.length">未发现静态风险项。</p></div><h3>包内文件</h3><span v-for="file in skillDetail.files" :key="file.path" class="tag">{{ file.path }} · {{ file.bytes }} B</span></section></div>
    </template>
  </FloatingPanel>
</template>

<style scoped>
.tabs .tab{display:inline-flex;align-items:center;gap:5px}.skill-notice{display:flex;align-items:center;gap:10px;margin-bottom:16px}.skill-notice span{flex:1}
.skill-card{cursor:pointer;transition:.16s transform,.16s border-color}.skill-card:hover{transform:translateY(-2px);border-color:#85b6df}.skill-card h3{font-size:14px;color:#153b62;margin:12px 0 5px}.skill-card p{min-height:48px;font-size:11px;color:#667d93;line-height:1.55}.skill-card-head,.detail-summary{display:flex;align-items:center;gap:8px}.skill-card-head{justify-content:space-between}.view-button{width:100%;margin-top:13px}
.validation-badge{display:inline-flex;align-items:center;gap:4px;padding:4px 8px;border-radius:999px;background:#fff0ed;color:#b13c2d;font-size:10px}.validation-badge.verified{background:#e8f7ef;color:#16724a}.upload-drop{display:grid;place-items:center;gap:7px;padding:30px;border:1px dashed #8eb8dc;border-radius:12px;background:#f5faff;cursor:pointer;text-align:center}.upload-drop span{font-size:11px;color:#6a8095;max-width:620px}
.report{margin-top:16px;padding:14px;border-radius:10px;background:#fff3f0;border:1px solid #edb4aa}.report.verified{background:#edf9f3;border-color:#a8dac1}.report h3{margin:0 0 5px;font-size:14px}.report p{margin:0;color:#587084;font-size:11px}.finding-list{display:grid;gap:7px;margin-top:11px}.finding-list>div{display:flex;align-items:flex-start;gap:6px;padding:7px;border-radius:7px;background:rgba(255,255,255,.7);font-size:11px}.finding-list span{display:grid}.finding-list small{color:#778a9a}
.detail-summary{margin-bottom:14px}.detail-summary .hash{flex:1;color:#6c8295;font:10px monospace}.detail-grid{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}.detail-grid section{min-width:0}.detail-grid h3{font-size:13px;color:#244967}.detail-grid pre{max-height:520px;overflow:auto;white-space:pre-wrap;padding:13px;border:1px solid #dce8f1;border-radius:9px;background:#f8fafc;font:11px/1.65 monospace}.check-list{display:grid;grid-template-columns:1fr 1fr;gap:5px}.check-list div{display:flex;align-items:center;gap:5px;color:#a33d31;font-size:10px}.check-list .passed{color:#16724a}
@media(max-width:900px){.detail-grid{grid-template-columns:1fr}.skill-notice{align-items:flex-start;flex-wrap:wrap}}
</style>
