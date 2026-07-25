<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Activity, BarChart3, BrainCircuit, Check, Clock3, Fingerprint, Gauge,
  LogOut, MessageSquareText, Palette, RefreshCw, Save, Sparkles, UserRound,
} from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'
import { useUserStore } from '../stores/user'

const app = useAppStore()
const userStore = useUserStore()
const usageRange = ref<'day'|'week'|'month'>('day')
const usage = ref<Entity>({ summary:{}, chart:[], records:[] })
const profile = ref<Entity>({ traits:[], top_topics:[], recent_questions:[], favorite_agents:[] })
const styles = ref<Entity[]>([])
const selectedStyle = ref('balanced')
const customStyle = ref('')
const savingStyle = ref(false)
const account = reactive({ display_name:'', avatar_color:'#1769c2', memory_enabled:true })
const maxChartTokens = computed(() => Math.max(1, ...usage.value.chart.map((item:Entity) => item.tokens || 0)))
const rangeLabels = { day:'每日', week:'每周', month:'每月' }
const intentLabels: Record<string,string> = {
  general:'通用问答', web_research:'联网研究', knowledge_query:'知识检索',
  local_file_access:'本地文件', local_workspace_change:'项目修改',
  command_execution:'命令执行', agent_orchestration:'Agent 协作',
}

function formatTokens(value: number) {
  if (value >= 1_000_000) return `${(value/1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value/1_000).toFixed(1)}K`
  return String(value || 0)
}
function formatDate(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) : '—'
}

async function loadUsage() {
  usage.value = await api.get(`/users/me/usage?range=${usageRange.value}`)
}
async function changeRange(value:'day'|'week'|'month') {
  usageRange.value = value
  try { await loadUsage() } catch (error:any) { app.notify(error.message,'error') }
}
async function loadAll() {
  app.loading(true)
  try {
    const [usageData, profileData, styleData] = await Promise.all([
      api.get<Entity>(`/users/me/usage?range=${usageRange.value}`),
      api.get<Entity>('/users/me/profile'),
      api.get<Entity[]>('/reply-styles'),
    ])
    usage.value = usageData
    profile.value = profileData
    styles.value = styleData
    const user = userStore.user || {}
    account.display_name = user.display_name || ''
    account.avatar_color = user.avatar_color || '#1769c2'
    account.memory_enabled = user.memory_enabled !== false
    selectedStyle.value = user.reply_style_id || 'balanced'
    customStyle.value = user.custom_reply_style || ''
  } catch (error:any) { app.notify(error.message,'error') }
  finally { app.loading(false) }
}
async function saveStyle() {
  savingStyle.value = true
  try {
    await api.put('/users/me/reply-style', { style_id:selectedStyle.value, custom_style:customStyle.value })
    await userStore.refresh()
    app.notify('全局回复风格已保存，后续所有 Agent 立即生效')
  } catch (error:any) { app.notify(error.message,'error') }
  finally { savingStyle.value = false }
}
async function saveAccount() {
  try {
    userStore.user = await api.patch('/users/me', account)
    app.notify('用户偏好已更新')
    if (account.memory_enabled) profile.value = await api.get('/users/me/profile')
  } catch (error:any) { app.notify(error.message,'error') }
}
async function refreshProfile() {
  try {
    profile.value = await api.get('/users/me/profile')
    app.notify('用户画像已根据最新提问刷新')
  } catch (error:any) { app.notify(error.message,'error') }
}
async function logout() {
  await userStore.logout()
}

onMounted(loadAll)
</script>

<template>
  <div class="perception-page">
    <PageHeader eyebrow="USER & PERCEPTION" title="用户与感知" description="管理本地身份、Token 用量、提问记忆和所有 Agent 的全局回复风格。">
      <button class="btn" @click="refreshProfile"><RefreshCw :size="14" />刷新画像</button>
      <button class="btn logout" @click="logout"><LogOut :size="14" />退出登录</button>
    </PageHeader>

    <section class="identity-hero">
      <div class="identity-orbit"><i /><i /><i /><span :style="{background:account.avatar_color}"><UserRound :size="30" /></span></div>
      <div class="identity-copy"><span>LOCAL USER SPACE</span><h2>{{ userStore.user?.display_name }}</h2><p>@{{ userStore.user?.username }} · 本地用户空间已启用</p><div><em><Fingerprint :size="12" />独立身份</em><em><BrainCircuit :size="12" />持续感知</em><em><Palette :size="12" />全局风格</em></div></div>
      <div class="identity-stats">
        <article><span>累计 Token</span><strong>{{ formatTokens(usage.summary.total_tokens) }}</strong><small>所有 Agent 运行</small></article>
        <article><span>提问记忆</span><strong>{{ profile.question_count || 0 }}</strong><small>{{ account.memory_enabled ? '持续学习中' : '已暂停记录' }}</small></article>
        <article><span>完成率</span><strong>{{ usage.summary.success_rate || 0 }}%</strong><small>{{ usage.summary.total_runs || 0 }} 次运行</small></article>
      </div>
    </section>

    <section class="perception-grid">
      <article class="usage-panel panel-card">
        <header class="card-heading">
          <div><span><BarChart3 :size="15" />TOKEN USAGE</span><h3>用量趋势与消耗记录</h3><p>按每日、每周或每月查看本地模型调用量。</p></div>
          <div class="range-tabs"><button v-for="(_,key) in rangeLabels" :key="key" :class="{active:usageRange===key}" @click="changeRange(key as any)">{{ rangeLabels[key as keyof typeof rangeLabels] }}</button></div>
        </header>
        <div class="usage-summary">
          <div><Gauge :size="17" /><span>本周期<strong>{{ formatTokens(usage.summary.period_tokens) }}</strong></span></div>
          <div><Activity :size="17" /><span>运行次数<strong>{{ usage.summary.period_runs || 0 }}</strong></span></div>
          <div><Sparkles :size="17" /><span>平均消耗<strong>{{ formatTokens(usage.summary.average_tokens) }}</strong></span></div>
        </div>
        <div class="token-chart">
          <div v-for="item in usage.chart" :key="item.label" class="chart-column">
            <span>{{ item.tokens ? formatTokens(item.tokens) : '' }}</span>
            <div><i :style="{height:`${Math.max(item.tokens ? 8 : 2, item.tokens/maxChartTokens*100)}%`}" /></div>
            <small>{{ item.label }}</small>
          </div>
        </div>
        <div class="records">
          <header><span>最近消耗明细</span><small>共 {{ usage.summary.total_runs || 0 }} 条</small></header>
          <div class="record-table">
            <div class="record-row table-head"><span>时间</span><span>Agent / 任务</span><span>状态</span><span>Token</span></div>
            <div v-for="item in usage.records" :key="item.id" class="record-row">
              <span>{{ formatDate(item.created_at) }}</span>
              <span><strong>{{ item.agent_name }}</strong><small>{{ item.input }}</small></span>
              <span :class="['run-state',item.status]">{{ item.status==='completed'?'已完成':item.status==='running'?'运行中':'异常' }}</span>
              <span class="token-value">{{ formatTokens(item.tokens) }}</span>
            </div>
            <div v-if="!usage.records.length" class="empty-line">完成一次 Agent 对话后，这里会出现 Token 消耗记录。</div>
          </div>
        </div>
      </article>

      <aside class="profile-panel panel-card">
        <header class="card-heading"><div><span><BrainCircuit :size="15" />USER PROFILE</span><h3>提问形成的用户画像</h3><p>只分析本地对话中的用户问题。</p></div><b>{{ profile.question_count || 0 }} 问</b></header>
        <div class="traits"><span v-for="trait in profile.traits" :key="trait"><Check :size="11" />{{ trait }}</span></div>
        <div class="profile-block"><header><span>关注主题</span><small>关键词频次</small></header><div class="topic-cloud"><span v-for="(topic,index) in profile.top_topics" :key="topic.name" :style="{fontSize:`${9+Math.max(0,5-index/2)}px`}">{{ topic.name }} <b>{{ topic.count }}</b></span><em v-if="!profile.top_topics?.length">继续对话后自动形成主题画像</em></div></div>
        <div class="profile-block"><header><span>常用 Agent</span></header><div class="agent-ranks"><div v-for="agent in profile.favorite_agents" :key="agent.name"><span>{{ agent.name }}</span><b>{{ agent.count }} 次</b></div><em v-if="!profile.favorite_agents?.length">暂无数据</em></div></div>
        <div class="profile-block recent-questions"><header><span>最近提问记忆</span><small>平均 {{ profile.average_question_length || 0 }} 字</small></header><div v-for="item in profile.recent_questions?.slice(0,5)" :key="item.id"><Clock3 :size="11" /><p>{{ item.question }}</p><span>{{ intentLabels[item.category] || item.category }}</span></div><em v-if="!profile.recent_questions?.length">还没有提问记忆</em></div>
      </aside>
    </section>

    <section class="bottom-grid">
      <article class="style-panel panel-card">
        <header class="card-heading"><div><span><Palette :size="15" />RESPONSE STYLE</span><h3>所有 AI 的全局回复风格</h3><p>选择后将同时影响 Agent 对话及 Agent 调用的子 Agent。</p></div><button class="btn btn-primary" :disabled="savingStyle" @click="saveStyle"><Save :size="13" />保存并应用</button></header>
        <div class="style-cards">
          <button v-for="item in styles" :key="item.id" :class="{active:selectedStyle===item.id}" @click="selectedStyle=item.id"><span><i /><strong>{{ item.name }}</strong><Check v-if="selectedStyle===item.id" :size="14" /></span><p>{{ item.description }}</p></button>
        </div>
        <div v-if="selectedStyle==='custom'" class="custom-style"><label>自定义回复要求</label><textarea v-model="customStyle" class="textarea" placeholder="例如：先用一句话给结论，再给三项行动建议；技术细节使用表格；不要使用过多标题。" /><small>这段要求会添加到每个 Agent 的系统上下文中，但不会覆盖安全和事实准确性规则。</small></div>
      </article>

      <aside class="account-panel panel-card">
        <header class="card-heading"><div><span><UserRound :size="15" />ACCOUNT</span><h3>账号与记忆设置</h3><p>修改本地显示信息和感知开关。</p></div></header>
        <label>显示名称<input v-model="account.display_name"></label>
        <label>头像颜色<input v-model="account.avatar_color" type="color"></label>
        <div class="memory-setting"><div><MessageSquareText :size="17" /><span><strong>记住对话中的问题</strong><small>用于生成主题、习惯和常用 Agent 画像</small></span></div><button :class="{active:account.memory_enabled}" @click="account.memory_enabled=!account.memory_enabled"><i /></button></div>
        <button class="btn btn-primary save-account" @click="saveAccount"><Save :size="13" />保存账号设置</button>
        <p class="privacy-note"><Fingerprint :size="13" />用户账号、提问记忆和画像仅存储在本地 EvoAgent 数据库中。</p>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.perception-page{display:grid;gap:18px}.identity-hero{min-height:190px;padding:24px 28px;display:grid;grid-template-columns:130px minmax(250px,1fr) minmax(410px,.9fr);align-items:center;gap:24px;overflow:hidden;border-radius:17px;color:#fff;background:radial-gradient(circle at 75% 5%,#2ba9bd88,transparent 34%),linear-gradient(125deg,#073d69,#086188 65%,#087b90);box-shadow:0 15px 35px #164f7624}.identity-orbit{position:relative;width:112px;height:112px;display:grid;place-items:center}.identity-orbit:before,.identity-orbit:after{content:"";position:absolute;inset:5px;border:1px solid #73dbe855;border-radius:50%;animation:orbit 10s linear infinite}.identity-orbit:after{inset:-8px;border-style:dashed;animation-direction:reverse}.identity-orbit>i{position:absolute;width:7px;height:7px;border-radius:50%;background:#76e6ef;box-shadow:0 0 14px #76e6ef}.identity-orbit>i:nth-child(1){top:6px;left:51px}.identity-orbit>i:nth-child(2){right:2px;bottom:30px}.identity-orbit>i:nth-child(3){left:10px;bottom:18px}.identity-orbit>span{position:relative;z-index:2;width:72px;height:72px;display:grid;place-items:center;border:5px solid #ffffff28;border-radius:50%;color:#fff;box-shadow:0 10px 25px #04294355}.identity-copy>span{font-size:8px;letter-spacing:2.2px;color:#80e4ec}.identity-copy h2{margin:7px 0 4px;font-size:24px}.identity-copy p{margin:0;color:#b9dbe6;font-size:9px}.identity-copy>div{margin-top:16px;display:flex;flex-wrap:wrap;gap:7px}.identity-copy em{padding:5px 8px;display:flex;align-items:center;gap:4px;border:1px solid #ffffff2d;border-radius:99px;color:#d2eaf0;background:#ffffff12;font-size:8px;font-style:normal}.identity-stats{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid #ffffff25;border-radius:12px;background:#ffffff0d;backdrop-filter:blur(10px)}.identity-stats article{padding:18px;border-right:1px solid #ffffff20;display:flex;flex-direction:column}.identity-stats article:last-child{border:0}.identity-stats span{color:#b4d5df;font-size:8px}.identity-stats strong{margin:7px 0 2px;font-size:22px}.identity-stats small{color:#8ebcc9;font-size:7px}.perception-grid{display:grid;grid-template-columns:minmax(620px,1.55fr) minmax(320px,.75fr);gap:18px}.bottom-grid{display:grid;grid-template-columns:minmax(650px,1.5fr) minmax(300px,.6fr);gap:18px}.panel-card{min-width:0;border:1px solid #d9e5ee;border-radius:14px;background:#fff;box-shadow:0 8px 24px #254d6c0d}.card-heading{min-height:76px;padding:16px 18px;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid #e4ecf2}.card-heading>div>span{display:flex;align-items:center;gap:6px;color:#2985ba;font-size:8px;font-weight:800;letter-spacing:1.2px}.card-heading h3{margin:5px 0 3px;color:#214765;font-size:14px}.card-heading p{margin:0;color:#8596a5;font-size:8px}.range-tabs{padding:3px;display:flex;border-radius:8px;background:#edf3f7}.range-tabs button{padding:6px 10px;border:0;border-radius:6px;color:#708596;background:transparent;font-size:8px}.range-tabs button.active{color:#1769c2;background:#fff;box-shadow:0 2px 7px #234d6e1c}.usage-summary{margin:14px 18px 0;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.usage-summary>div{padding:11px;display:flex;align-items:center;gap:9px;border-radius:9px;color:#2477b1;background:#f1f7fb}.usage-summary span{display:flex;flex-direction:column;color:#7890a2;font-size:8px}.usage-summary strong{margin-top:2px;color:#244c6a;font-size:13px}.token-chart{height:185px;margin:15px 18px;padding:15px 12px 7px;display:flex;align-items:stretch;gap:9px;border-radius:10px;background:linear-gradient(#f8fbfd,#f1f7fa)}.chart-column{min-width:0;flex:1;display:grid;grid-template-rows:15px 1fr 18px;justify-items:center}.chart-column>span{color:#63819a;font-size:7px}.chart-column>div{width:100%;display:flex;align-items:flex-end;justify-content:center;border-bottom:1px solid #cbdce7}.chart-column i{width:min(28px,65%);min-height:2px;border-radius:5px 5px 1px 1px;background:linear-gradient(#32b5c6,#1769c2);box-shadow:0 4px 12px #258ebc2e;transition:height .3s}.chart-column small{padding-top:5px;color:#71879a;font-size:7px}.records{margin-top:8px;padding:0 18px 18px}.records>header,.profile-block>header{height:35px;display:flex;align-items:center;justify-content:space-between;color:#385d78;font-size:9px;font-weight:700}.records>header small,.profile-block>header small{color:#91a0ad;font-size:7px;font-weight:400}.record-table{overflow:hidden;border:1px solid #e0e9f0;border-radius:9px}.record-row{min-height:42px;padding:6px 10px;display:grid;grid-template-columns:90px minmax(190px,1fr) 60px 62px;align-items:center;gap:8px;border-bottom:1px solid #edf2f5;color:#6f8597;font-size:8px}.record-row:last-child{border:0}.record-row>span:nth-child(2){min-width:0;display:flex;flex-direction:column;gap:2px}.record-row strong{color:#385b74;font-size:9px}.record-row small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.table-head{min-height:30px;color:#8295a5;background:#f6f9fb;font-weight:700}.run-state{width:max-content;padding:3px 6px;border-radius:99px;color:#16805b;background:#e5f6ee}.run-state.failed{color:#ad4141;background:#fdecec}.run-state.running{color:#9b651b;background:#fff3dc}.token-value{color:#1769c2!important;font-weight:800}.empty-line{padding:25px;text-align:center;color:#91a2af;font-size:8px}.profile-panel>.card-heading b{padding:5px 8px;border-radius:99px;color:#1769c2;background:#e8f3fb;font-size:8px}.traits{padding:14px 16px 3px;display:flex;flex-wrap:wrap;gap:6px}.traits span{padding:5px 7px;display:flex;align-items:center;gap:4px;border-radius:6px;color:#287358;background:#e9f7f1;font-size:8px}.profile-block{margin:9px 16px;padding-top:6px;border-top:1px solid #edf2f5}.topic-cloud{min-height:85px;padding:10px;display:flex;flex-wrap:wrap;align-content:center;gap:6px;border-radius:8px;background:#f4f8fb}.topic-cloud span{padding:4px 7px;border:1px solid #d4e5f1;border-radius:99px;color:#2c6288;background:#fff}.topic-cloud b{color:#20a0ac;font-size:7px}.topic-cloud em,.agent-ranks em,.recent-questions>em{margin:auto;color:#94a4b1;font-size:8px;font-style:normal}.agent-ranks{display:grid;gap:5px}.agent-ranks>div{padding:6px 8px;display:flex;justify-content:space-between;border-radius:6px;color:#4e6d84;background:#f5f8fa;font-size:8px}.agent-ranks b{color:#2979b1}.recent-questions{padding-bottom:12px}.recent-questions>div{padding:7px 0;display:grid;grid-template-columns:auto 1fr auto;align-items:start;gap:5px;border-bottom:1px dashed #e3ebf0;color:#84a0b5}.recent-questions p{margin:0;overflow:hidden;color:#4d6a80;font-size:8px;line-height:1.45;text-overflow:ellipsis;white-space:nowrap}.recent-questions>div>span{padding:2px 4px;border-radius:4px;color:#3679a6;background:#edf5fa;font-size:7px}.style-panel,.account-panel{padding-bottom:18px}.style-cards{padding:16px 18px;display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.style-cards button{min-height:94px;padding:11px;border:1px solid #dbe6ee;border-radius:9px;text-align:left;color:#7890a3;background:#fff}.style-cards button:hover,.style-cards button.active{border-color:#76b5dd;background:#eef7fd;box-shadow:0 6px 16px #246c9e15}.style-cards button>span{display:flex;align-items:center;gap:6px}.style-cards button i{width:7px;height:7px;border-radius:50%;background:#9eb2c1}.style-cards button.active i{background:#1e91bd;box-shadow:0 0 0 3px #1e91bd22}.style-cards strong{flex:1;color:#315775;font-size:10px}.style-cards button.active svg{color:#16795a}.style-cards p{margin:10px 0 0;font-size:8px;line-height:1.55}.custom-style{margin:0 18px;padding:13px;border-radius:9px;background:#f3f8fb}.custom-style label{display:block;margin-bottom:7px;color:#315a78;font-size:9px;font-weight:700}.custom-style textarea{min-height:90px}.custom-style small{display:block;margin-top:6px;color:#8498a8;font-size:7px;line-height:1.5}.account-panel>label{margin:14px 16px 0;display:grid;grid-template-columns:85px 1fr;align-items:center;color:#527087;font-size:9px}.account-panel input:not([type=color]){height:35px;padding:0 9px;border:1px solid #d0dee8;border-radius:7px;outline:0}.account-panel input[type=color]{width:48px;height:30px;padding:2px;border:1px solid #d0dee8;border-radius:7px;background:#fff}.memory-setting{margin:16px;padding:12px;display:flex;align-items:center;justify-content:space-between;border-radius:9px;background:#f2f7fa}.memory-setting>div{display:flex;align-items:center;gap:8px;color:#2c78a8}.memory-setting span{display:flex;flex-direction:column;gap:3px}.memory-setting strong{color:#355b76;font-size:9px}.memory-setting small{color:#8497a6;font-size:7px}.memory-setting>button{width:36px;height:20px;padding:2px;border:0;border-radius:99px;background:#bdcbd5}.memory-setting>button i{display:block;width:16px;height:16px;border-radius:50%;background:#fff;transition:.2s}.memory-setting>button.active{background:#1c92aa}.memory-setting>button.active i{transform:translateX(16px)}.save-account{margin:0 16px;width:calc(100% - 32px);justify-content:center}.privacy-note{margin:12px 16px 0;padding-top:11px;border-top:1px solid #edf2f5;display:flex;align-items:flex-start;gap:5px;color:#8295a4;font-size:7px;line-height:1.5}.logout{color:#9c4444}@keyframes orbit{to{transform:rotate(360deg)}}@media(max-width:1200px){.identity-hero{grid-template-columns:105px 1fr}.identity-stats{grid-column:1/-1}.perception-grid,.bottom-grid{grid-template-columns:1fr}.style-cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:760px){.identity-hero{grid-template-columns:1fr}.identity-orbit{display:none}.identity-stats{grid-template-columns:1fr}.identity-stats article{border-right:0;border-bottom:1px solid #ffffff20}.perception-grid{grid-template-columns:1fr}.style-cards{grid-template-columns:1fr 1fr}.record-row{grid-template-columns:75px 1fr 50px}.record-row>span:last-child{display:none}}
</style>
