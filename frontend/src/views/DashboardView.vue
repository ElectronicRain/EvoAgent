<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ArrowRight, Bot, Boxes, BrainCircuit, CheckCircle2, Clock3, Database,
  GitBranch, Gauge, RefreshCw, Rocket, ShieldCheck, Sparkles, WandSparkles,
} from 'lucide-vue-next'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const overview = ref<Entity>({ counts: {}, recent_runs: [], runtime: {} })
const refreshing = ref(false)

const features = [
  { to: '/agents', label: 'Agent 工厂', caption: '创建、配置并与专业 Agent 对话', icon: Bot, countKey: 'agents', accent: 'blue' },
  { to: '/knowledge', label: '学科知识库', caption: '导入资料，进行混合检索与可信问答', icon: Database, countKey: 'knowledge_bases', accent: 'cyan' },
  { to: '/workflows', label: '协作工作流', caption: '用可视化画布编排 Agent 协作关系', icon: GitBranch, countKey: 'workflows', accent: 'violet' },
  { to: '/extensions', label: '扩展与模型', caption: '管理模型端点、MCP、插件与 Skills', icon: Boxes, countKey: 'extensions', accent: 'amber' },
  { to: '/evolution', label: '进化实验室', caption: '评估 Agent 表现并持续优化能力', icon: BrainCircuit, countKey: '', accent: 'rose' },
  { to: '/governance', label: '安全治理', caption: '配置沙箱、审批策略与审计规则', icon: ShieldCheck, countKey: 'pending_approvals', accent: 'green' },
]

const quickStart = [
  { step: '01', title: '连接模型与能力', description: '配置模型端点，并检查 MCP、插件和 Skills 是否就绪。', to: '/extensions', action: '配置能力' },
  { step: '02', title: '准备专属知识', description: '上传 PDF、文档或网页，让 Agent 能够检索你的真实资料。', to: '/knowledge', action: '建立知识库' },
  { step: '03', title: '创建并开始对话', description: '选择模板或创建 Agent，设置权限后直接开始处理任务。', to: '/agents', action: '进入 Agent 工厂' },
]

const recentRuns = computed<Entity[]>(() => overview.value.recent_runs || [])
const completedRuns = computed(() => recentRuns.value.filter(item => ['completed', 'success'].includes(item.status)).length)
const failedRuns = computed(() => recentRuns.value.filter(item => ['failed', 'error'].includes(item.status)).length)
const activeRuns = computed(() => recentRuns.value.filter(item => ['running', 'pending'].includes(item.status)).length)
const successRate = computed(() => recentRuns.value.length ? Math.round(completedRuns.value / recentRuns.value.length * 100) : 0)
const maxDuration = computed(() => Math.max(1, ...recentRuns.value.map(item => Number(item.duration_ms || 0))))
const chartPoints = computed(() => {
  const rows = [...recentRuns.value].reverse()
  if (!rows.length) return ''
  return rows.map((item, index) => {
    const x = rows.length === 1 ? 50 : index * (100 / (rows.length - 1))
    const y = 84 - Number(item.duration_ms || 0) / maxDuration.value * 64
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})
const chartArea = computed(() => chartPoints.value ? `0,100 ${chartPoints.value} 100,100` : '')
const runDistribution = computed(() => {
  const total = Math.max(1, recentRuns.value.length)
  const completedEnd = completedRuns.value / total * 360
  const activeEnd = completedEnd + activeRuns.value / total * 360
  const failedEnd = activeEnd + failedRuns.value / total * 360
  return {
    background: `conic-gradient(#2b82d4 0deg ${completedEnd}deg, #65c8d0 ${completedEnd}deg ${activeEnd}deg, #ef7b83 ${activeEnd}deg ${failedEnd}deg, #dfe9f2 ${failedEnd}deg 360deg)`,
  }
})

const workspaceName = computed(() => {
  const value = String(overview.value.runtime?.workspace || '')
  return value.split(/[\\/]/).filter(Boolean).at(-1) || '本地工作区'
})

function featureCount(key: string) {
  if (!key) return '已启用'
  if (key === 'pending_approvals') {
    const count = Number(overview.value.counts?.[key] || 0)
    return count ? `${count} 项待处理` : '策略正常'
  }
  return `${Number(overview.value.counts?.[key] || 0)} 个可用`
}

function time(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '-'
}

function duration(value: number) {
  if (!value) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`
}

async function load() {
  refreshing.value = true
  try {
    overview.value = await api.get('/overview')
  } catch (error: any) {
    store.notify(error.message, 'error')
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="dashboard">
    <section class="welcome-hero">
      <div class="hero-grid" />
      <div class="hero-orb orb-one" />
      <div class="hero-orb orb-two" />
      <div class="hero-copy">
        <div class="hero-kicker"><Sparkles :size="14" /> EVOAGENT WORKSPACE</div>
        <h1>让每一种智能能力，<br><span>都能快速投入工作。</span></h1>
        <p>从 Agent、知识库到安全策略，所有功能都在本地工作台中清晰可见、随时可用。</p>
        <div class="hero-actions">
          <RouterLink to="/agents" class="hero-btn primary"><WandSparkles :size="16" />创建或对话<ArrowRight :size="15" /></RouterLink>
          <RouterLink to="/knowledge" class="hero-btn"><Database :size="16" />检索知识库</RouterLink>
        </div>
      </div>
      <div class="hero-console">
        <div class="console-head"><span><i />系统已就绪</span><Gauge :size="16" /></div>
        <div class="console-score">
          <strong>{{ store.backendOnline ? 'ONLINE' : 'CONNECTING' }}</strong>
          <span>本地能力运行状态</span>
        </div>
        <div class="console-stats">
          <div><b>{{ overview.counts?.agents || 0 }}</b><span>Agent</span></div>
          <div><b>{{ overview.counts?.knowledge_bases || 0 }}</b><span>知识库</span></div>
          <div><b>{{ overview.counts?.extensions || 0 }}</b><span>扩展</span></div>
        </div>
        <div class="console-workspace"><CheckCircle2 :size="14" /><span>{{ workspaceName }} 已连接</span></div>
      </div>
    </section>

    <div class="section-heading">
      <div><span>AVAILABLE CAPABILITIES</span><h2>现在可以使用的功能</h2><p>直接进入对应模块，无需从复杂流程开始。</p></div>
      <button class="refresh-btn" :class="{ spinning: refreshing }" @click="load"><RefreshCw :size="15" />刷新状态</button>
    </div>

    <section class="feature-grid">
      <RouterLink
        v-for="(item, index) in features"
        :key="item.to"
        :to="item.to"
        class="feature-card"
        :class="`accent-${item.accent}`"
        :style="{ animationDelay: `${index * 55}ms` }"
      >
        <div class="feature-icon"><component :is="item.icon" :size="21" /></div>
        <div class="feature-main"><strong>{{ item.label }}</strong><p>{{ item.caption }}</p></div>
        <div class="feature-meta"><span><i />{{ featureCount(item.countKey) }}</span><ArrowRight :size="16" /></div>
      </RouterLink>
    </section>

    <section class="insight-grid">
      <article class="insight-card activity-card">
        <header><div><span>ACTIVITY</span><h3>运行活跃度</h3></div><small>最近 {{ recentRuns.length }} 次运行</small></header>
        <div class="chart-shell">
          <svg v-if="chartPoints" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="最近运行耗时趋势">
            <defs>
              <linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#2f86d5" stop-opacity=".32" />
                <stop offset="100%" stop-color="#2f86d5" stop-opacity="0" />
              </linearGradient>
            </defs>
            <line v-for="line in [20, 40, 60, 80]" :key="line" x1="0" :y1="line" x2="100" :y2="line" class="chart-grid-line" />
            <polygon :points="chartArea" fill="url(#chart-fill)" />
            <polyline :points="chartPoints" class="chart-line" />
          </svg>
          <div v-else class="chart-empty"><Clock3 :size="25" /><span>运行 Agent 后，这里将展示趋势</span></div>
        </div>
        <footer>
          <div><span>最高耗时</span><strong>{{ duration(maxDuration === 1 && !recentRuns.length ? 0 : maxDuration) }}</strong></div>
          <div><span>成功运行</span><strong>{{ completedRuns }}</strong></div>
          <div><span>当前活跃</span><strong>{{ activeRuns }}</strong></div>
        </footer>
      </article>

      <article class="insight-card health-card">
        <header><div><span>RELIABILITY</span><h3>运行完成情况</h3></div><ShieldCheck :size="19" /></header>
        <div class="donut-row">
          <div class="donut" :style="runDistribution"><div><strong>{{ successRate }}%</strong><span>完成率</span></div></div>
          <div class="donut-legend">
            <div><i class="done" /><span>已完成</span><b>{{ completedRuns }}</b></div>
            <div><i class="active" /><span>进行中</span><b>{{ activeRuns }}</b></div>
            <div><i class="failed" /><span>异常</span><b>{{ failedRuns }}</b></div>
          </div>
        </div>
        <div class="trust-line"><span>本地数据层</span><b>SQLite · WAL</b></div>
        <div class="trust-line"><span>安全边界</span><b>{{ overview.runtime?.safety || '工作区隔离' }}</b></div>
      </article>

      <article class="insight-card recent-card">
        <header><div><span>RECENT</span><h3>最近运行</h3></div><RouterLink to="/agents">查看全部</RouterLink></header>
        <div v-if="recentRuns.length" class="recent-list">
          <div v-for="item in recentRuns.slice(0, 4)" :key="item.id" class="recent-item">
            <StatusBadge :status="item.status" />
            <div><strong>{{ item.input_text || '未命名任务' }}</strong><span>{{ time(item.created_at) }}</span></div>
            <small>{{ duration(item.duration_ms) }}</small>
          </div>
        </div>
        <div v-else class="recent-empty"><Rocket :size="27" /><strong>从第一次对话开始</strong><span>运行记录会自动汇总在这里</span></div>
      </article>
    </section>

    <section class="getting-started">
      <div class="start-copy">
        <span>QUICK START</span>
        <h2>三步开始使用</h2>
        <p>按自己的需要进入任何一步，已有配置可以直接跳过。</p>
        <div class="start-decoration"><Rocket :size="30" /></div>
      </div>
      <div class="start-steps">
        <article v-for="item in quickStart" :key="item.step">
          <div class="step-number">{{ item.step }}</div>
          <div><strong>{{ item.title }}</strong><p>{{ item.description }}</p><RouterLink :to="item.to">{{ item.action }}<ArrowRight :size="14" /></RouterLink></div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dashboard{display:grid;gap:28px;padding-bottom:18px}.welcome-hero{min-height:330px;padding:46px 48px;position:relative;overflow:hidden;border:1px solid rgba(87,153,211,.32);border-radius:22px;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(310px,.72fr);gap:44px;align-items:center;color:#fff;background:linear-gradient(125deg,#072c53 0%,#0a477b 55%,#0c6494 100%);box-shadow:0 18px 44px rgba(7,47,89,.18)}.hero-grid{position:absolute;inset:0;opacity:.14;background-image:linear-gradient(rgba(255,255,255,.15) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.15) 1px,transparent 1px);background-size:36px 36px;mask-image:linear-gradient(90deg,#000,transparent 72%)}.hero-orb{position:absolute;border-radius:50%;filter:blur(2px);pointer-events:none}.orb-one{width:260px;height:260px;right:22%;top:-160px;background:radial-gradient(circle,rgba(90,213,225,.42),transparent 68%);animation:orb-drift 7s ease-in-out infinite}.orb-two{width:190px;height:190px;left:45%;bottom:-140px;background:radial-gradient(circle,rgba(103,153,255,.38),transparent 68%);animation:orb-drift 9s ease-in-out infinite reverse}.hero-copy,.hero-console{position:relative;z-index:1}.hero-kicker{width:max-content;padding:7px 10px;border:1px solid rgba(168,222,255,.26);border-radius:99px;display:flex;align-items:center;gap:7px;color:#bde3ff;background:rgba(9,37,68,.24);font-size:9px;font-weight:750;letter-spacing:.14em}.hero-copy h1{margin:18px 0 13px;font-size:34px;line-height:1.26;letter-spacing:-.035em}.hero-copy h1 span{color:#8edcf1}.hero-copy p{max-width:620px;margin:0;color:#c3dced;font-size:13px;line-height:1.8}.hero-actions{margin-top:25px;display:flex;flex-wrap:wrap;gap:10px}.hero-btn{height:42px;padding:0 15px;border:1px solid rgba(189,224,249,.3);border-radius:10px;display:inline-flex;align-items:center;gap:8px;color:#e7f5ff;background:rgba(255,255,255,.08);text-decoration:none;font-size:12px;font-weight:700;transition:.2s ease}.hero-btn:hover{background:rgba(255,255,255,.15);transform:translateY(-2px)}.hero-btn.primary{color:#0a4677;background:#fff;border-color:#fff;box-shadow:0 8px 22px rgba(1,28,53,.2)}.hero-btn.primary:hover{color:#075d9b;background:#ecf8ff}.hero-console{padding:20px;border:1px solid rgba(178,224,252,.26);border-radius:17px;background:linear-gradient(145deg,rgba(255,255,255,.14),rgba(255,255,255,.06));box-shadow:inset 0 1px rgba(255,255,255,.15),0 14px 34px rgba(0,26,50,.18);backdrop-filter:blur(12px)}.console-head{display:flex;align-items:center;justify-content:space-between;color:#b9d9ee;font-size:10px}.console-head span{display:flex;align-items:center;gap:7px}.console-head i{width:7px;height:7px;border-radius:50%;background:#57e2ae;box-shadow:0 0 0 5px rgba(87,226,174,.12),0 0 16px rgba(87,226,174,.6);animation:status-pulse 2s ease-out infinite}.console-score{padding:24px 0 20px;border-bottom:1px solid rgba(198,229,248,.18)}.console-score strong,.console-score span{display:block}.console-score strong{font-size:24px;letter-spacing:.08em}.console-score span{margin-top:5px;color:#9ec8e3;font-size:10px}.console-stats{padding:18px 0;display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.console-stats div{padding-right:10px;border-right:1px solid rgba(196,227,247,.16)}.console-stats div:last-child{border:0}.console-stats b,.console-stats span{display:block}.console-stats b{font-size:20px}.console-stats span{margin-top:4px;color:#9fc6de;font-size:9px}.console-workspace{padding:10px;border-radius:8px;display:flex;align-items:center;gap:7px;color:#c8e4f4;background:rgba(2,34,62,.24);font-size:9px}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:24px}.section-heading span,.start-copy>span,.insight-card header span{color:#2881cf;font-size:9px;font-weight:800;letter-spacing:.14em}.section-heading h2,.start-copy h2{margin:5px 0 0;color:#12375b;font-size:22px;letter-spacing:-.02em}.section-heading p,.start-copy p{margin:6px 0 0;color:#71869a;font-size:11px}.refresh-btn{height:35px;padding:0 12px;border:1px solid #cbddea;border-radius:9px;display:flex;align-items:center;gap:7px;color:#35617f;background:#fff;font-size:10px;font-weight:700}.refresh-btn:hover{color:#1769c2;border-color:#8ebde3;background:#f6fbff}.refresh-btn.spinning svg{animation:spin .8s linear infinite}.feature-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.feature-card{min-height:148px;padding:18px;position:relative;overflow:hidden;border:1px solid #d9e5ef;border-radius:15px;display:grid;grid-template-columns:auto 1fr;grid-template-rows:1fr auto;gap:0 13px;color:inherit;background:#fff;text-decoration:none;box-shadow:0 6px 18px rgba(19,59,95,.045);transition:.22s ease;animation:card-enter .45s both}.feature-card::after{content:'';position:absolute;width:90px;height:90px;right:-45px;top:-45px;border-radius:50%;background:var(--soft);transition:.25s ease}.feature-card:hover{border-color:var(--edge);box-shadow:0 14px 30px rgba(22,67,105,.11);transform:translateY(-4px)}.feature-card:hover::after{transform:scale(1.35)}.feature-icon{width:42px;height:42px;border-radius:11px;display:grid;place-items:center;color:var(--accent);background:var(--soft);border:1px solid var(--edge)}.feature-main{min-width:0}.feature-main strong{display:block;color:#193d61;font-size:14px}.feature-main p{margin:7px 0 0;color:#71859a;font-size:10px;line-height:1.6}.feature-meta{grid-column:1/-1;margin-top:15px;padding-top:12px;border-top:1px solid #edf2f6;display:flex;align-items:center;justify-content:space-between;color:#658096}.feature-meta span{display:flex;align-items:center;gap:6px;font-size:9px}.feature-meta i{width:6px;height:6px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 4px var(--soft)}.feature-meta>svg{color:var(--accent);transition:.2s}.feature-card:hover .feature-meta>svg{transform:translateX(4px)}.accent-blue{--accent:#2479c8;--soft:#e9f4ff;--edge:#c9e1f6}.accent-cyan{--accent:#16899a;--soft:#e7f8fa;--edge:#c1e8ec}.accent-violet{--accent:#6d63c8;--soft:#f0efff;--edge:#d9d5fa}.accent-amber{--accent:#b97818;--soft:#fff5df;--edge:#f2dfb4}.accent-rose{--accent:#bd5b79;--soft:#fff0f5;--edge:#f2ceda}.accent-green{--accent:#23835d;--soft:#eaf8f1;--edge:#caeadb}.insight-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(270px,.72fr) minmax(310px,.9fr);gap:15px}.insight-card{min-width:0;padding:19px;border:1px solid #d8e4ee;border-radius:15px;background:#fff;box-shadow:0 5px 18px rgba(19,59,95,.045)}.insight-card header{display:flex;align-items:start;justify-content:space-between;gap:12px}.insight-card header h3{margin:4px 0 0;color:#1a3d5f;font-size:14px}.insight-card header small,.recent-card header a{color:#8395a7;font-size:9px;text-decoration:none}.chart-shell{height:150px;margin-top:12px}.chart-shell svg{width:100%;height:100%;overflow:visible}.chart-grid-line{stroke:#e5edf4;stroke-width:.6;stroke-dasharray:2 2}.chart-line{fill:none;stroke:#267dca;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke;filter:drop-shadow(0 3px 3px rgba(38,125,202,.18));animation:line-reveal .8s ease both}.chart-empty{height:100%;display:grid;place-content:center;justify-items:center;gap:8px;color:#8ca0b2;background:linear-gradient(180deg,#f9fcff,#f4f8fb);border-radius:10px;font-size:9px}.activity-card footer{padding-top:13px;border-top:1px solid #eaf0f5;display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.activity-card footer div{padding-left:10px;border-left:2px solid #d9e8f4}.activity-card footer span,.activity-card footer strong{display:block}.activity-card footer span{color:#8a9baa;font-size:8px}.activity-card footer strong{margin-top:4px;color:#254a6b;font-size:12px}.health-card header>svg{color:#23835d}.donut-row{margin:24px 0;display:flex;align-items:center;gap:19px}.donut{width:112px;height:112px;flex:0 0 112px;padding:10px;border-radius:50%;display:grid;place-items:center;animation:donut-enter .75s ease both}.donut>div{width:100%;height:100%;border-radius:50%;display:grid;place-content:center;text-align:center;background:#fff;box-shadow:inset 0 0 0 1px #edf2f6}.donut strong,.donut span{display:block}.donut strong{color:#173f63;font-size:21px}.donut span{margin-top:3px;color:#8799aa;font-size:8px}.donut-legend{min-width:0;display:grid;gap:11px;flex:1}.donut-legend div{display:grid;grid-template-columns:8px 1fr auto;gap:7px;align-items:center;color:#71869a;font-size:9px}.donut-legend i{width:7px;height:7px;border-radius:50%}.donut-legend .done{background:#2b82d4}.donut-legend .active{background:#65c8d0}.donut-legend .failed{background:#ef7b83}.donut-legend b{color:#274a69}.trust-line{padding:9px 0;border-top:1px solid #edf2f6;display:flex;justify-content:space-between;gap:12px;color:#7a8fa1;font-size:9px}.trust-line b{max-width:55%;overflow:hidden;color:#31536f;text-overflow:ellipsis;white-space:nowrap}.recent-card header a{color:#2475bb}.recent-list{margin-top:14px;display:grid;gap:5px}.recent-item{min-width:0;padding:9px 8px;border-radius:8px;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;align-items:center;transition:.15s}.recent-item:hover{background:#f5f9fc}.recent-item>div{min-width:0}.recent-item strong,.recent-item span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.recent-item strong{color:#34516c;font-size:9px;font-weight:650}.recent-item span{margin-top:4px;color:#91a0ae;font-size:8px}.recent-item small{color:#6f8598;font-size:8px}.recent-empty{height:180px;display:grid;place-content:center;justify-items:center;gap:6px;color:#82a5c0;text-align:center}.recent-empty strong{color:#315979;font-size:11px}.recent-empty span{color:#94a5b4;font-size:8px}.getting-started{padding:28px;border:1px solid #d5e4f0;border-radius:18px;display:grid;grid-template-columns:270px 1fr;gap:28px;background:linear-gradient(135deg,#f7fbff,#edf7ff);box-shadow:0 8px 24px rgba(25,75,115,.055)}.start-copy{position:relative;padding-right:20px;border-right:1px solid #d9e8f3}.start-copy p{max-width:220px;line-height:1.7}.start-decoration{width:58px;height:58px;margin-top:22px;border-radius:16px;display:grid;place-items:center;color:#2479c8;background:#fff;box-shadow:0 8px 20px rgba(36,121,200,.12);transform:rotate(-5deg)}.start-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.start-steps article{min-width:0;padding:15px;border:1px solid #dce9f3;border-radius:12px;display:grid;grid-template-columns:auto 1fr;gap:11px;background:rgba(255,255,255,.74);transition:.2s}.start-steps article:hover{background:#fff;border-color:#a9cae4;transform:translateY(-2px)}.step-number{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;color:#1b72bc;background:#e5f2fd;font-size:9px;font-weight:800}.start-steps strong{color:#244969;font-size:11px}.start-steps p{min-height:46px;margin:6px 0 11px;color:#75899b;font-size:9px;line-height:1.65}.start-steps a{display:flex;align-items:center;gap:5px;color:#1769c2;text-decoration:none;font-size:9px;font-weight:700}@keyframes orb-drift{50%{transform:translate(18px,14px) scale(1.08)}}@keyframes status-pulse{70%{box-shadow:0 0 0 12px rgba(87,226,174,0),0 0 16px rgba(87,226,174,.5)}}@keyframes spin{to{transform:rotate(360deg)}}@keyframes card-enter{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}@keyframes line-reveal{from{opacity:0;stroke-dasharray:200;stroke-dashoffset:200}to{opacity:1;stroke-dasharray:200;stroke-dashoffset:0}}@keyframes donut-enter{from{opacity:0;transform:rotate(-35deg) scale(.8)}to{opacity:1;transform:none}}@media(max-width:1320px){.insight-grid{grid-template-columns:1.2fr .8fr}.recent-card{grid-column:1/-1}.recent-list{grid-template-columns:repeat(2,1fr)}.feature-grid{grid-template-columns:repeat(2,1fr)}.getting-started{grid-template-columns:230px 1fr}.start-steps{grid-template-columns:1fr}.start-steps p{min-height:0}}@media(max-width:980px){.welcome-hero{padding:34px;grid-template-columns:1fr}.hero-console{display:none}.insight-grid{grid-template-columns:1fr}.recent-card{grid-column:auto}.recent-list{grid-template-columns:1fr}.getting-started{grid-template-columns:1fr}.start-copy{padding:0 0 20px;border:0;border-bottom:1px solid #d9e8f3}.start-decoration{display:none}}@media(prefers-reduced-motion:reduce){.dashboard *{animation:none!important;transition:none!important}}
</style>
