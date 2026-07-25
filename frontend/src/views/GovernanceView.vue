<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  Check, FileClock, FolderLock, HardDrive, Plus, Save, ShieldAlert,
  ShieldCheck, ShieldX, X,
} from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import FloatingPanel from '../components/FloatingPanel.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const tab = ref<'sandbox'|'approvals'|'policies'|'audit'>('sandbox')
const approvals = ref<Entity[]>([]), policies = ref<Entity[]>([]), auditLogs = ref<Entity[]>([])
const showForm = ref(false), rootsText = ref(''), applicationWorkspace = ref('')
const security = reactive({
  filesystem_mode: 'workspace',
  command_mode: 'risk_based',
  block_critical_commands: true,
})
const form = reactive({
  name: '自定义审批策略', description: '按工具和风险等级控制 Agent 行为',
  priority: 100, is_default: false, enabled: true,
  rulesText: JSON.stringify([
    { name: '只读自动执行', when: { risk_levels: ['low'] }, decision: 'auto' },
    { name: '写入人工确认', when: { risk_levels: ['medium','high'] }, decision: 'ask' },
    { name: '关键风险拒绝', when: { risk_levels: ['critical'] }, decision: 'deny' },
  ], null, 2),
})
const filesystemOptions = [
  { value: 'workspace', title: '仅当前工作区', description: '只能访问 EvoAgent 当前工作区，适合日常使用。', icon: ShieldCheck },
  { value: 'custom', title: '指定项目路径', description: '只能访问下方明确列出的一个或多个项目目录。', icon: FolderLock },
  { value: 'unrestricted', title: '完全访问本地', description: '允许访问本机任意路径，请谨慎使用。', icon: HardDrive },
]
const commandOptions = [
  { value: 'risk_based', title: '按 Agent 审批策略', description: '继续使用策略设计中的风险分级规则。' },
  { value: 'always_ask', title: '每次变更都确认', description: '写文件和执行命令前暂停，等待人工批准。' },
  { value: 'auto', title: '自动执行', description: '不弹出审批；仍受目录边界和关键命令硬拦截约束。' },
  { value: 'deny', title: '禁止变更与命令', description: '只允许低风险的目录浏览、读取和搜索。' },
]
const parse = (value: string) => { try { return JSON.parse(value || '[]') } catch { return [] } }

async function load() {
  store.loading(true)
  try {
    const [approvalRows, policyRows, logs, runtime] = await Promise.all([
      api.get<Entity[]>('/approvals'), api.get<Entity[]>('/approval-policies'),
      api.get<Entity[]>('/audit?limit=100'), api.get<Entity>('/security/runtime'),
    ])
    approvals.value = approvalRows
    policies.value = policyRows
    auditLogs.value = logs
    security.filesystem_mode = runtime.filesystem_mode
    security.command_mode = runtime.command_mode
    security.block_critical_commands = runtime.block_critical_commands
    rootsText.value = (runtime.workspace_roots || []).join('\n')
    applicationWorkspace.value = runtime.application_workspace || ''
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

async function saveSecurity() {
  const workspace_roots = rootsText.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
  if (security.filesystem_mode === 'custom' && !workspace_roots.length) {
    return store.notify('请至少填写一个授权目录', 'error')
  }
  store.loading(true)
  try {
    const result = await api.put<Entity>('/security/runtime', { ...security, workspace_roots })
    rootsText.value = result.workspace_roots.join('\n')
    store.notify('运行时安全工作区已更新，新任务立即生效')
    await load()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

async function decide(id: string, approved: boolean) {
  store.loading(true)
  try {
    await api.post(`/approvals/${id}/decide`, { approved, decided_by: 'local-user' })
    store.notify(approved ? '操作已批准并执行' : '操作已拒绝')
    await load()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

async function savePolicy() {
  store.loading(true)
  try {
    await api.post('/approval-policies', { ...form, rules: JSON.parse(form.rulesText) })
    store.notify('审批策略已创建')
    showForm.value = false
    await load()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

const riskLabel: Record<string,string> = { low: '低风险', medium: '中风险', high: '高风险', critical: '关键风险' }
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="GOVERNANCE" title="安全治理" description="统一设置本地目录边界、命令确认方式、审批策略和审计记录。">
    <button v-if="tab==='sandbox'" class="btn btn-primary" @click="saveSecurity"><Save :size="15" />保存安全设置</button>
    <button v-if="tab==='policies'" class="btn btn-primary" @click="showForm=true"><Plus :size="15" />自定义策略</button>
  </PageHeader>
  <div class="card">
    <div class="tabs">
      <button class="tab" :class="{active:tab==='sandbox'}" @click="tab='sandbox'"><FolderLock :size="14" /> 工作区限制</button>
      <button class="tab" :class="{active:tab==='approvals'}" @click="tab='approvals'"><ShieldCheck :size="14" /> 审批中心</button>
      <button class="tab" :class="{active:tab==='policies'}" @click="tab='policies'"><ShieldX :size="14" /> 策略设计</button>
      <button class="tab" :class="{active:tab==='audit'}" @click="tab='audit'"><FileClock :size="14" /> 审计日志</button>
    </div>

    <div v-if="tab==='sandbox'" class="security-editor">
      <section>
        <div class="section-heading"><div><h2>文件系统范围</h2><p>决定 Agent 能够读取或修改哪些本地路径。</p></div><span class="runtime-badge">即时生效</span></div>
        <div class="mode-grid">
          <button v-for="item in filesystemOptions" :key="item.value" :class="{selected:security.filesystem_mode===item.value}" @click="security.filesystem_mode=item.value">
            <component :is="item.icon" :size="21" /><span><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span><Check v-if="security.filesystem_mode===item.value" :size="16" />
          </button>
        </div>
      </section>
      <section :class="{muted:security.filesystem_mode!=='custom'}">
        <div class="section-heading"><div><h2>指定项目路径</h2><p v-if="security.filesystem_mode==='workspace'">当前工作区固定为：{{ applicationWorkspace }}</p><p v-else>选择“指定项目路径”时生效；每行一个绝对路径。</p></div></div>
        <textarea v-model="rootsText" class="textarea roots-input" :disabled="security.filesystem_mode!=='custom'" placeholder="D:/Projects/Project-A&#10;D:/Projects/Project-B" />
        <div class="security-tip"><FolderLock :size="15" /><span>设置后所有 Agent 默认继承；每次对话仍可通过安全策略按钮临时切换范围和审批方式。</span></div>
      </section>
      <section>
        <div class="section-heading"><div><h2>命令与变更确认</h2><p>控制写文件与 PowerShell 命令在执行前是否暂停。</p></div></div>
        <div class="command-grid">
          <label v-for="item in commandOptions" :key="item.value" :class="{selected:security.command_mode===item.value}"><input v-model="security.command_mode" type="radio" :value="item.value"><span><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span></label>
        </div>
        <label class="critical-toggle"><input v-model="security.block_critical_commands" type="checkbox"><ShieldAlert :size="17" /><span><strong>始终拦截关键风险命令</strong><small>包括磁盘格式化、系统关机、用户账户与注册表破坏性操作。强烈建议保持开启。</small></span></label>
      </section>
    </div>

    <div v-if="tab==='approvals'" class="card-body"><div class="notice" style="margin-bottom:15px">对话中的 Agent 会原地等待审批；批准或拒绝后将继续本轮任务。</div><div class="list-stack"><article v-for="item in approvals" :key="item.id" class="list-item"><div><strong>{{ item.summary }}</strong><p>{{ riskLabel[item.risk_level] || item.risk_level }} · {{ item.action_type }} · {{ new Date(item.created_at).toLocaleString('zh-CN') }}</p></div><div class="approval-actions"><StatusBadge :status="item.status" /><template v-if="item.status==='pending'"><button class="btn btn-sm btn-primary" @click="decide(item.id,true)"><Check :size="13" />批准</button><button class="btn btn-sm btn-danger" @click="decide(item.id,false)"><X :size="13" />拒绝</button></template></div></article><div v-if="!approvals.length" class="empty"><ShieldCheck :size="30" /><br>当前没有审批请求</div></div></div>
    <div v-if="tab==='policies'" class="card-body"><div class="grid grid-3"><article v-for="item in policies" :key="item.id" class="card policy-card"><div class="card-body"><div class="policy-head"><ShieldCheck :size="20" color="#1769c2" /><span v-if="item.is_default" class="tag">系统默认</span></div><h3>{{ item.name }}</h3><p>{{ item.description }}</p><div class="list-stack"><div v-for="rule in parse(item.rules_json)" :key="rule.name" class="policy-rule"><strong>{{ rule.name }}</strong><span :class="rule.decision">{{ rule.decision.toUpperCase() }}</span></div></div></div></article></div></div>
    <div v-if="tab==='audit'" class="table-wrap"><table><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>结果</th></tr></thead><tbody><tr v-for="item in auditLogs" :key="item.id"><td>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</td><td>{{ item.actor }}</td><td>{{ item.action }}</td><td>{{ item.resource_type }}</td><td><StatusBadge :status="item.success?'completed':'failed'" /></td></tr></tbody></table></div>
  </div>

  <FloatingPanel v-model="showForm" title="自定义审批策略" eyebrow="APPROVAL POLICY" description="按工具、风险等级和 Agent 范围配置审批决策。" size="large"><div class="form-grid"><div class="field"><label>策略名称</label><input v-model="form.name" class="input"></div><div class="field"><label>优先级（越小越优先）</label><input v-model.number="form.priority" type="number" class="input"></div><div class="field full"><label>说明</label><input v-model="form.description" class="input"></div><div class="field full"><label>匹配规则（JSON）</label><textarea v-model="form.rulesText" class="textarea policy-json" /><span class="field-help">条件支持 tools、risk_levels、agent_ids；决策支持 auto、ask、deny。</span></div><div class="field full"><button class="btn btn-primary" @click="savePolicy">保存策略</button></div></div></FloatingPanel>
</template>

<style scoped>
.tabs button{display:inline-flex;align-items:center;gap:5px}.security-editor{display:grid;gap:0;padding:4px 22px 24px}.security-editor section{padding:20px 0;border-bottom:1px solid #e2ebf2}.security-editor section:last-child{border:0}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:13px}.section-heading h2{margin:0;color:#234c6e;font-size:15px}.section-heading p{margin:4px 0 0;color:#71879a;font-size:10px}.runtime-badge{padding:4px 8px;border-radius:999px;background:#e8f6ef;color:#17805b;font-size:9px;font-weight:700}.mode-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.mode-grid>button{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:11px;padding:14px;border:1px solid #d8e4ee;border-radius:10px;text-align:left;color:#668096;background:#fff;cursor:pointer}.mode-grid>button.selected{border-color:#468fcd;background:#f1f8fe;color:#1769c2;box-shadow:0 0 0 2px #d9edfd}.mode-grid span,.command-grid span,.critical-toggle span{display:flex;flex-direction:column;gap:4px}.mode-grid strong,.command-grid strong,.critical-toggle strong{color:#315675;font-size:11px}.mode-grid small,.command-grid small,.critical-toggle small{color:#71879a;font-size:9px;line-height:1.5}.roots-input{min-height:105px;font-family:Consolas,"Microsoft YaHei",sans-serif}.muted{opacity:.57}.security-tip{display:flex;align-items:center;gap:7px;margin-top:8px;color:#55758f;font-size:9px}.command-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.command-grid label{display:flex;align-items:flex-start;gap:9px;padding:11px;border:1px solid #dce6ee;border-radius:8px;cursor:pointer}.command-grid label.selected{border-color:#75a9d4;background:#f4f9fe}.command-grid input,.critical-toggle input{accent-color:#1769c2}.critical-toggle{display:flex;align-items:flex-start;gap:9px;margin-top:12px;padding:12px;border:1px solid #efcfb2;border-radius:9px;background:#fffaf4;color:#bd6f22}.approval-actions,.policy-head{display:flex;align-items:center;gap:8px}.policy-head{justify-content:space-between}.policy-card{box-shadow:none}.policy-card h3{font-size:14px;color:#153b62}.policy-card p{min-height:34px;font-size:11px;color:#667d93}.policy-rule{padding:8px;border-radius:6px;background:#f6f9fc;color:#48627b;font-size:10px}.policy-rule span{float:right}.policy-rule .deny{color:#b43b3b}.policy-rule .auto{color:#177c57}.policy-rule .ask{color:#98640b}.policy-json{min-height:260px;font-family:Consolas,monospace}@media(max-width:900px){.mode-grid,.command-grid{grid-template-columns:1fr}}
</style>
