<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Bot, Boxes, Clock3, Database, GitBranch, Play, ShieldCheck } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const overview = ref<Entity>({ counts: {}, recent_runs: [], runtime: {} })
const workflows = ref<Entity[]>([])
const selectedWorkflow = ref('')
const task = ref('分析学科垂类智能体如何提升高校科研效率，并给出可验证的实施路径。')
const output = ref('')

async function load() {
  store.loading(true)
  try {
    overview.value = await api.get('/overview')
    workflows.value = await api.get('/workflows')
    selectedWorkflow.value ||= workflows.value[0]?.id || ''
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

async function runWorkflow() {
  if (!selectedWorkflow.value || !task.value.trim()) return
  store.loading(true); output.value = ''
  try {
    const result: Entity = await api.post(`/workflows/${selectedWorkflow.value}/run`, { input: { task: task.value } })
    output.value = result.status === 'completed' ? JSON.stringify(JSON.parse(result.output_json), null, 2) : result.error
    store.notify(result.status === 'completed' ? '协作工作流执行完成' : '工作流执行失败', result.status === 'completed' ? 'success' : 'error')
    await load()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

const time = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="OPERATIONS" title="运行总览" description="集中查看 Agent、工作流、知识和安全状态。">
    <button class="btn" @click="load">刷新数据</button>
  </PageHeader>

  <div class="grid grid-4">
    <div class="card metric-card"><div class="metric-icon"><Bot :size="21" /></div><div><strong>{{ overview.counts.agents || 0 }}</strong><span>Agent 版本</span></div></div>
    <div class="card metric-card"><div class="metric-icon"><GitBranch :size="21" /></div><div><strong>{{ overview.counts.workflows || 0 }}</strong><span>协作工作流</span></div></div>
    <div class="card metric-card"><div class="metric-icon"><Database :size="21" /></div><div><strong>{{ overview.counts.knowledge_bases || 0 }}</strong><span>学科知识库</span></div></div>
    <div class="card metric-card"><div class="metric-icon"><ShieldCheck :size="21" /></div><div><strong>{{ overview.counts.pending_approvals || 0 }}</strong><span>待处理审批</span></div></div>
  </div>

  <div class="split" style="margin-top:20px">
    <section class="card">
      <div class="card-header"><h2>最近 Agent 运行</h2><RouterLink to="/agents" class="btn btn-sm">进入工厂</RouterLink></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>状态</th><th>输入任务</th><th>耗时</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="item in overview.recent_runs" :key="item.id">
              <td><StatusBadge :status="item.status" /></td>
              <td style="max-width:420px"><div class="truncate-cell">{{ item.input_text }}</div></td>
              <td>{{ item.duration_ms }} ms</td><td>{{ time(item.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!overview.recent_runs?.length" class="empty"><Clock3 :size="28" /><br>尚无运行记录</div>
      </div>
    </section>

    <aside class="card">
      <div class="card-header"><h3>系统可信状态</h3></div>
      <div class="card-body list-stack">
        <div class="list-item"><div><strong>SQLite 数据层</strong><p>本地持久化与 WAL 并发模式</p></div><StatusBadge status="healthy" /></div>
        <div class="list-item"><div><strong>工作区隔离</strong><p>{{ overview.runtime.workspace }}</p></div><StatusBadge status="active" /></div>
        <div class="list-item"><div><strong>扩展连接</strong><p>插件、Skills 与 MCP 注册中心</p></div><span class="score">{{ overview.counts.extensions || 0 }}</span></div>
        <div class="notice">所有本地工具调用都经过风险评估、审批策略和审计日志。高危命令即使获批也会被二次规则拦截。</div>
      </div>
    </aside>
  </div>

  <section class="card" style="margin-top:20px">
    <div class="card-header"><h2>快速运行多 Agent 工作流</h2><Boxes :size="18" color="#1769c2" /></div>
    <div class="card-body grid grid-2">
      <div class="field"><label>选择工作流</label><select v-model="selectedWorkflow" class="select"><option v-for="item in workflows" :key="item.id" :value="item.id">{{ item.name }}</option></select></div>
      <div></div>
      <div class="field full"><label>真实任务</label><textarea v-model="task" class="textarea" /></div>
      <div class="field full"><button class="btn btn-primary" @click="runWorkflow"><Play :size="15" />开始协作运行</button></div>
      <div v-if="output" class="field full"><label>运行结果（AI 生成内容）</label><div class="result-box">{{ output }}</div></div>
    </div>
  </section>
</template>
