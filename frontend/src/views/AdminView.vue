<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Activity, AlertTriangle, CheckCircle2, Clock3, Cloud, Database,
  Laptop, RefreshCw, Search, ShieldCheck, UserRound, UsersRound,
} from 'lucide-vue-next'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'
import { useUserStore } from '../stores/user'

const store = useAppStore(), userStore = useUserStore()
const overview = ref<Entity>({ metrics: {}, versions: [], modules: [] })
const users = ref<Entity[]>([]), events = ref<Entity[]>([])
const selectedUser = ref<Entity | null>(null)
const activeTab = ref<'overview'|'users'|'logs'>('overview')
const busy = ref(false), search = ref(''), moduleFilter = ref(''), resultFilter = ref('')
const lastUpdated = ref<Date | null>(null), refreshResult = ref<Entity | null>(null)

const visibleUsers = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return users.value
  return users.value.filter(item => `${item.username} ${item.display_name}`.toLowerCase().includes(keyword))
})
const moduleOptions = computed(() => [...new Set(events.value.map(item => item.module).filter(Boolean))])
const visibleEvents = computed(() => events.value.filter(item => {
  if (moduleFilter.value && item.module !== moduleFilter.value) return false
  if (resultFilter.value && String(item.success) !== resultFilter.value) return false
  if (search.value && !`${item.username} ${item.event_type} ${item.module}`.toLowerCase().includes(search.value.toLowerCase())) return false
  return true
}))

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '暂无记录'
}
function eventLabel(value: string) {
  const labels: Record<string,string> = {
    'user.registered':'创建账户', 'user.logged_in':'用户登录', 'user.login_failed':'登录失败',
    'page.viewed':'访问页面', 'frontend.error':'前端异常', 'frontend.api_error':'接口异常',
    'admin.dashboard_refreshed':'管理员刷新', 'agent.run.created':'Agent 调用',
  }
  return labels[value] || value.replaceAll('.', ' · ')
}
async function load(refresh = false) {
  if (busy.value) return
  busy.value = true
  try {
    if (refresh) refreshResult.value = await api.post<Entity>('/admin/refresh')
    const [summary, userItems, eventItems] = await Promise.all([
      api.get<Entity>('/admin/overview'), api.get<Entity[]>('/admin/users'), api.get<Entity[]>('/admin/events?limit=300'),
    ])
    overview.value = summary; users.value = userItems; events.value = eventItems
    if (selectedUser.value) selectedUser.value = users.value.find(item => item.id === selectedUser.value?.id) || null
    lastUpdated.value = new Date()
    if (refresh) store.notify(summary.remote_available ? '已与中央管理服务同步' : '已刷新本地数据；中央服务尚未连接')
  } catch (error: any) { store.notify(error.message || '管理员数据载入失败', 'error') }
  finally { busy.value = false }
}
async function toggleUser(item: Entity) {
  if (overview.value.scope === 'hub') return
  try {
    const updated = await api.patch<Entity>(`/admin/users/${item.id}`, { status:item.status === 'active' ? 'disabled' : 'active', note:'管理员面板操作' })
    Object.assign(item, updated)
    store.notify(item.status === 'active' ? '账户已恢复' : '账户已停用')
  } catch (error: any) { store.notify(error.message || '账户状态更新失败', 'error') }
}

onMounted(() => { if (userStore.user?.role === 'admin') void load() })
</script>

<template>
  <div class="admin-page">
    <header class="admin-header">
      <div><span><ShieldCheck :size="16" />ADMIN CONSOLE</span><h1>系统管理</h1><p>集中查看用户、设备、版本、使用情况与脱敏运行日志。</p></div>
      <div class="header-actions"><small v-if="lastUpdated">更新于 {{ formatDate(lastUpdated.toISOString()) }}</small><button :disabled="busy" @click="load(true)"><RefreshCw :class="{spin:busy}" :size="15" />刷新并同步</button></div>
    </header>

    <section v-if="userStore.user?.role !== 'admin'" class="access-denied"><ShieldCheck/><h2>仅管理员可访问</h2><p>此页面和所有管理接口均由后端权限验证。</p></section>
    <template v-else>
      <nav class="admin-tabs"><button :class="{active:activeTab==='overview'}" @click="activeTab='overview'"><Activity/>系统概况</button><button :class="{active:activeTab==='users'}" @click="activeTab='users'"><UsersRound/>使用者</button><button :class="{active:activeTab==='logs'}" @click="activeTab='logs'"><Database/>运行日志</button></nav>

      <div class="sync-banner" :class="{online:overview.remote_available}"><Cloud :size="18"/><div><strong>{{ overview.remote_available ? '中央管理服务已连接' : '当前显示本机数据' }}</strong><span>{{ overview.remote_available ? '管理员电脑关闭期间，其他用户已上传的数据仍保存在中央服务。' : '日志继续保存在本地离线队列；配置中央服务后会自动补传。' }}</span></div><b>待同步 {{ overview.pending_local_events || 0 }}</b></div>

      <section v-if="activeTab==='overview'" class="overview-section">
        <div class="metric-grid">
          <article><UserRound/><span><b>{{ overview.metrics.total_users || 0 }}</b><small>全部使用者</small></span></article>
          <article><UsersRound/><span><b>{{ overview.metrics.active_users_7d || 0 }}</b><small>近 7 日活跃</small></span></article>
          <article><Laptop/><span><b>{{ overview.metrics.devices || 0 }}</b><small>已登记设备</small></span></article>
          <article><Activity/><span><b>{{ overview.metrics.events_today || 0 }}</b><small>今日事件</small></span></article>
          <article><CheckCircle2/><span><b>{{ overview.metrics.success_rate || 0 }}%</b><small>运行成功率</small></span></article>
          <article class="warning"><AlertTriangle/><span><b>{{ overview.metrics.errors || 0 }}</b><small>异常事件</small></span></article>
        </div>
        <div class="summary-grid">
          <article><header>模块使用分布</header><div v-if="!overview.modules?.length" class="empty">暂无使用记录</div><div v-for="item in overview.modules" :key="item.module" class="bar-row"><span>{{ item.module }}</span><i><b :style="{width:`${Math.max(4,100*item.events/Math.max(1,overview.metrics.events))}%`}"/></i><strong>{{ item.events }}</strong></div></article>
          <article><header>客户端版本分布</header><div v-if="!overview.versions?.length" class="empty">暂无版本记录</div><div v-for="item in overview.versions" :key="item.version" class="version-row"><span>V{{ item.version }}</span><b>{{ item.events }} 次事件</b></div></article>
        </div>
      </section>

      <section v-else-if="activeTab==='users'" class="data-section">
        <header class="filter-bar"><label><Search/><input v-model="search" placeholder="搜索用户名或显示名称"></label><span>共 {{ visibleUsers.length }} 名使用者</span></header>
        <div class="user-layout">
          <div class="user-list"><button v-for="item in visibleUsers" :key="`${item.installation_id||'local'}-${item.id}`" :class="{active:selectedUser?.id===item.id}" @click="selectedUser=item"><span :style="{background:item.avatar_color||'#1769c2'}"><UserRound/></span><div><strong>{{ item.display_name || item.username }}</strong><small>@{{ item.username }} · {{ item.role }}</small></div><em :class="item.status">{{ item.status==='active'?'正常':'停用' }}</em></button><p v-if="!visibleUsers.length" class="empty">没有匹配的使用者</p></div>
          <aside v-if="selectedUser" class="user-detail"><header><span :style="{background:selectedUser.avatar_color||'#1769c2'}"><UserRound/></span><div><h2>{{ selectedUser.display_name }}</h2><p>@{{ selectedUser.username }}</p></div></header><dl><div><dt>账户编号</dt><dd>{{ selectedUser.id }}</dd></div><div><dt>注册时间</dt><dd>{{ formatDate(selectedUser.created_at) }}</dd></div><div><dt>最后活跃</dt><dd>{{ formatDate(selectedUser.last_active_at||selectedUser.last_event_at) }}</dd></div><div><dt>客户端版本</dt><dd>V{{ selectedUser.client_version||'未知' }}</dd></div><div><dt>使用事件</dt><dd>{{ selectedUser.event_count||0 }}</dd></div><div><dt>异常事件</dt><dd>{{ selectedUser.error_count||0 }}</dd></div></dl><button v-if="overview.scope!=='hub'" class="status-button" :class="{restore:selectedUser.status!=='active'}" @click="toggleUser(selectedUser)">{{ selectedUser.status==='active'?'停用账户':'恢复账户' }}</button></aside>
          <aside v-else class="user-detail placeholder"><UserRound/><p>选择一名使用者查看详情</p></aside>
        </div>
      </section>

      <section v-else class="data-section">
        <header class="filter-bar"><label><Search/><input v-model="search" placeholder="搜索用户、事件或模块"></label><select v-model="moduleFilter"><option value="">全部模块</option><option v-for="item in moduleOptions" :key="item">{{ item }}</option></select><select v-model="resultFilter"><option value="">全部结果</option><option value="true">成功</option><option value="false">失败</option></select><span>{{ visibleEvents.length }} 条</span></header>
        <div class="event-list"><article v-for="item in visibleEvents" :key="item.id" :class="{failed:!item.success}"><span class="event-icon"><CheckCircle2 v-if="item.success"/><AlertTriangle v-else/></span><div><header><strong>{{ eventLabel(item.event_type) }}</strong><b>{{ item.module }}</b></header><p>{{ item.username }} · {{ item.resource_type || 'system' }}<template v-if="item.duration_ms"> · {{ item.duration_ms }}ms</template></p><details v-if="Object.keys(item.detail||{}).length"><summary>查看脱敏详情</summary><pre>{{ JSON.stringify(item.detail,null,2) }}</pre></details></div><time><Clock3/>{{ formatDate(item.occurred_at) }}</time></article><p v-if="!visibleEvents.length" class="empty">暂无符合条件的日志</p></div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.admin-page{min-height:100%;color:#29485e}.admin-header{padding:4px 2px 16px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #d9e3e9}.admin-header>div:first-child>span{display:flex;align-items:center;gap:6px;color:#1769c2;font-size:8px;font-weight:800;letter-spacing:.1em}.admin-header h1{margin:5px 0 3px;font-size:23px}.admin-header p{margin:0;color:#78909f;font-size:9px}.header-actions{display:flex;align-items:center;gap:10px}.header-actions small{color:#8a9ba6;font-size:8px}.header-actions button,.status-button{height:34px;padding:0 13px;border:1px solid #1769c2;border-radius:7px;display:flex;align-items:center;gap:6px;color:#fff;background:#1769c2;cursor:pointer}.header-actions button:disabled{opacity:.55}.admin-tabs{height:48px;display:flex;align-items:center;gap:5px}.admin-tabs button{height:31px;padding:0 11px;border:1px solid transparent;border-radius:6px;display:flex;align-items:center;gap:6px;color:#60798b;background:transparent;font-size:9px;cursor:pointer}.admin-tabs svg{width:14px}.admin-tabs button.active{color:#1769c2;border-color:#b9d3e4;background:#edf6fc}.sync-banner{min-height:52px;padding:0 14px;border:1px solid #e4d5ae;border-radius:9px;display:flex;align-items:center;gap:10px;color:#9a6b22;background:#fffaf0}.sync-banner.online{color:#197555;border-color:#bddfce;background:#f1fbf6}.sync-banner div{min-width:0;flex:1;display:grid;gap:2px}.sync-banner strong{font-size:9px}.sync-banner span{color:#718897;font-size:8px}.sync-banner b{font-size:8px}.metric-grid{margin-top:14px;display:grid;grid-template-columns:repeat(6,1fr);gap:9px}.metric-grid article{min-height:83px;padding:13px;border:1px solid #dbe5eb;border-radius:9px;display:flex;align-items:center;gap:10px;background:#fff}.metric-grid svg{width:19px;color:#2678b6}.metric-grid span{display:grid;gap:3px}.metric-grid b{font-size:20px}.metric-grid small{color:#8396a3;font-size:8px}.metric-grid .warning svg,.metric-grid .warning b{color:#b75c45}.summary-grid{margin-top:10px;display:grid;grid-template-columns:2fr 1fr;gap:10px}.summary-grid>article,.data-section{border:1px solid #dbe5eb;border-radius:9px;background:#fff}.summary-grid>article{min-height:280px;padding:14px}.summary-grid>article>header{margin-bottom:14px;font-size:10px;font-weight:800}.bar-row{height:28px;display:grid;grid-template-columns:100px 1fr 40px;align-items:center;gap:8px;font-size:8px}.bar-row i{height:5px;border-radius:5px;background:#e6edf2}.bar-row i b{display:block;height:100%;border-radius:5px;background:#2c80bd}.bar-row strong{text-align:right}.version-row{padding:9px 0;border-bottom:1px solid #eef2f4;display:flex;justify-content:space-between;font-size:9px}.version-row b{color:#768c9b}.data-section{min-height:520px;overflow:hidden}.filter-bar{height:50px;padding:0 12px;border-bottom:1px solid #dfe7ec;display:flex;align-items:center;gap:8px}.filter-bar label{width:280px;height:31px;padding:0 9px;border:1px solid #cfdae2;border-radius:6px;display:flex;align-items:center;gap:6px}.filter-bar label svg{width:13px}.filter-bar input{min-width:0;flex:1;border:0;outline:0;font-size:9px}.filter-bar select{height:31px;border:1px solid #cfdae2;border-radius:6px;color:#4c687b;background:#fff;font-size:8px}.filter-bar>span{margin-left:auto;color:#8093a0;font-size:8px}.user-layout{height:calc(100vh - 280px);min-height:460px;display:grid;grid-template-columns:minmax(360px,1fr) 330px}.user-list{padding:8px;overflow:auto;border-right:1px solid #e0e8ed}.user-list>button{width:100%;height:55px;padding:0 10px;border:1px solid transparent;border-radius:7px;display:flex;align-items:center;gap:9px;text-align:left;background:#fff;cursor:pointer}.user-list>button:hover,.user-list>button.active{border-color:#b9d5e7;background:#f2f8fc}.user-list button>span,.user-detail header>span{width:31px;height:31px;border-radius:50%;display:grid;place-items:center;color:#fff}.user-list svg,.user-detail header svg{width:14px}.user-list button>div{min-width:0;flex:1;display:grid;gap:3px}.user-list strong{font-size:9px}.user-list small{color:#8396a3;font-size:7px}.user-list em{padding:3px 6px;border-radius:20px;color:#258065;background:#eaf8f2;font-size:7px;font-style:normal}.user-list em.disabled{color:#a45545;background:#fbeeee}.user-detail{padding:20px}.user-detail>header{display:flex;align-items:center;gap:10px}.user-detail h2,.user-detail p{margin:0}.user-detail h2{font-size:15px}.user-detail p{margin-top:3px;color:#8396a3;font-size:8px}.user-detail dl{margin:18px 0}.user-detail dl>div{padding:9px 0;border-bottom:1px solid #edf1f4;display:grid;grid-template-columns:90px 1fr;font-size:8px}.user-detail dt{color:#8194a1}.user-detail dd{margin:0;overflow-wrap:anywhere}.status-button{justify-content:center}.status-button.restore{color:#1769c2;background:#fff}.user-detail.placeholder{display:grid;place-content:center;justify-items:center;color:#9aabb5}.event-list{max-height:calc(100vh - 280px);min-height:460px;padding:5px 12px;overflow:auto}.event-list article{padding:10px 4px;border-bottom:1px solid #e9eef1;display:grid;grid-template-columns:30px 1fr 155px;gap:8px}.event-icon{width:27px;height:27px;border-radius:50%;display:grid;place-items:center;color:#238064;background:#eaf7f1}.event-list article.failed .event-icon{color:#b45145;background:#faeeec}.event-icon svg{width:13px}.event-list article>div>header{display:flex;align-items:center;gap:7px}.event-list article strong{font-size:9px}.event-list article b{padding:2px 5px;border-radius:4px;color:#1769c2;background:#eaf4fa;font-size:7px}.event-list p{margin:4px 0 0;color:#7e919e;font-size:8px}.event-list time{display:flex;align-items:center;gap:5px;color:#8799a5;font-size:7px}.event-list time svg{width:11px}.event-list details{margin-top:6px;color:#668093;font-size:7px}.event-list pre{max-height:170px;padding:8px;overflow:auto;border-radius:5px;background:#f3f6f8;font:7px/1.5 Consolas}.empty{padding:30px;text-align:center;color:#98a7b0;font-size:9px}.access-denied{min-height:400px;display:grid;place-content:center;justify-items:center;color:#8095a3}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:1150px){.metric-grid{grid-template-columns:repeat(3,1fr)}.user-layout{grid-template-columns:1fr}.user-detail{display:none}}
</style>
