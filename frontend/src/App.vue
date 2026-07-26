<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  Activity, Bot, Boxes, BrainCircuit, ChevronRight, Database, GitBranch,
  LayoutDashboard, LoaderCircle, LockKeyhole, PanelLeftClose, PanelLeftOpen, ScanFace,
  ShieldCheck, Sparkles, UserRound,
} from 'lucide-vue-next'
import AuthGate from './components/AuthGate.vue'
import { useAppStore } from './stores/app'
import { useAgentChatStore } from './stores/agentChat'
import { useUserStore } from './stores/user'

const AgentChatOverlay = defineAsyncComponent(() => import('./components/AgentChatOverlay.vue'))
const route = useRoute()
const store = useAppStore()
const agentChat = useAgentChatStore()
const userStore = useUserStore()
const pageTitle = computed(() => String(route.meta.title || 'EvoAgent'))
const detached = computed(() => Boolean(route.meta.detached))
const sidebarCollapsed = ref(window.localStorage.getItem('evoagent-sidebar-collapsed') === 'true')
const nav = [
  { to: '/', label: '运行总览', icon: LayoutDashboard },
  { to: '/agents', label: 'Agent 工厂', icon: Bot },
  { to: '/workflows', label: '协作工作流', icon: GitBranch },
  { to: '/knowledge', label: '学科知识库', icon: Database },
  { to: '/extensions', label: '扩展与模型', icon: Boxes },
  { to: '/evolution', label: '进化实验室', icon: BrainCircuit },
  { to: '/perception', label: '用户与感知', icon: ScanFace },
  { to: '/governance', label: '安全治理', icon: ShieldCheck },
]

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function showBackgroundTasks() {
  agentChat.runningTasks.forEach(task => { agentChat.openAgent(task.agent) })
  agentChat.restoreAll()
}

watch(sidebarCollapsed, value => {
  window.localStorage.setItem('evoagent-sidebar-collapsed', String(value))
})

onMounted(async () => {
  await store.waitForBackend()
  await userStore.bootstrap()
  window.setInterval(() => { void store.checkBackend() }, 15000)
})
</script>

<template>
  <div v-if="!userStore.ready" class="auth-loading"><Sparkles :size="28" /><strong>EvoAgent</strong><span>正在载入本地用户空间…</span></div>
  <AuthGate v-else-if="!userStore.user" />
  <div v-else-if="detached" class="detached-page"><RouterView /></div>
  <div v-else class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><Sparkles :size="21" /></div>
        <div class="brand-copy"><strong>EvoAgent</strong><span>智能体协作平台</span></div>
      </div>
      <div class="side-section-label">工作台</div>
      <nav>
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-link"
          :title="sidebarCollapsed ? item.label : undefined"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
          <ChevronRight class="nav-arrow" :size="15" />
        </RouterLink>
      </nav>
      <div class="sidebar-spacer" />
      <div class="security-note">
        <LockKeyhole :size="18" />
        <div class="security-copy"><strong>本地安全模式</strong><span>工作区隔离 · 全程审计</span></div>
      </div>
      <div class="version">EvoAgent v0.3.19</div>
    </aside>

    <section class="main-shell">
      <header class="topbar">
        <div class="topbar-title">
          <button
            class="shell-toggle"
            :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
            :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="toggleSidebar"
          >
            <PanelLeftOpen v-if="sidebarCollapsed" :size="18" />
            <PanelLeftClose v-else :size="18" />
          </button>
          <span>{{ pageTitle }}</span>
        </div>
        <div class="topbar-actions">
          <button
            v-if="agentChat.runningTasks.length"
            class="background-task-pill"
            title="任务会在关闭对话或切换页面后继续执行"
            @click="showBackgroundTasks"
          >
            <LoaderCircle :size="14" />
            后台执行 {{ agentChat.runningTasks.length }}
          </button>
          <div class="topbar-status">
            <span class="health-dot" :class="{ offline: !store.backendOnline }" />
            {{ store.backendOnline ? '本地服务正常' : '正在连接本地服务' }}
            <Activity :size="16" />
          </div>
          <RouterLink to="/perception" class="topbar-user" title="用户与感知">
            <span :style="{ background:userStore.user.avatar_color }"><UserRound :size="13" /></span>
            <strong>{{ userStore.user.display_name }}</strong>
          </RouterLink>
        </div>
      </header>
      <main class="content"><RouterView /></main>
    </section>

  </div>
  <transition name="toast">
    <div v-if="store.toast" class="toast-message" :class="store.toast.type">{{ store.toast.message }}</div>
  </transition>
  <div v-if="store.loadingCount" class="global-progress"><span /></div>
  <AgentChatOverlay v-if="userStore.user" />
</template>

<style scoped>
.detached-page{min-height:100vh;background:#f3f8fd;padding:22px;overflow:auto}
.auth-loading{position:fixed;inset:0;display:grid;place-content:center;justify-items:center;gap:8px;color:#1c557f;background:#eef5fa}.auth-loading svg{animation:loading-pulse 1.2s infinite}.auth-loading strong{font-size:17px}.auth-loading span{color:#7c92a5;font-size:9px}.topbar-user{height:32px;padding:0 10px 0 5px;display:flex;align-items:center;gap:7px;border:1px solid #d2e0ea;border-radius:99px;color:#31536e;background:#fff;text-decoration:none}.topbar-user>span{width:23px;height:23px;display:grid;place-items:center;border-radius:50%;color:#fff}.topbar-user strong{max-width:90px;overflow:hidden;font-size:9px;text-overflow:ellipsis;white-space:nowrap}@keyframes loading-pulse{50%{opacity:.35;transform:scale(.92)}}
</style>
