<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  Activity, Bot, Boxes, BrainCircuit, ChevronRight, Database, GitBranch,
  LayoutDashboard, LockKeyhole, Menu, ShieldCheck, Sparkles,
} from 'lucide-vue-next'
import { useAppStore } from './stores/app'

const route = useRoute()
const store = useAppStore()
const pageTitle = computed(() => String(route.meta.title || 'EvoAgent'))
const nav = [
  { to: '/', label: '运行总览', icon: LayoutDashboard },
  { to: '/agents', label: 'Agent 工厂', icon: Bot },
  { to: '/workflows', label: '协作工作流', icon: GitBranch },
  { to: '/knowledge', label: '学科知识库', icon: Database },
  { to: '/extensions', label: '扩展与模型', icon: Boxes },
  { to: '/evolution', label: '进化实验室', icon: BrainCircuit },
  { to: '/governance', label: '安全治理', icon: ShieldCheck },
]

onMounted(() => {
  void store.waitForBackend()
  window.setInterval(() => { void store.checkBackend() }, 15000)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><Sparkles :size="21" /></div>
        <div><strong>EvoAgent</strong><span>智能体协作平台</span></div>
      </div>
      <div class="side-section-label">工作台</div>
      <nav>
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to" class="nav-link">
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
          <ChevronRight class="nav-arrow" :size="15" />
        </RouterLink>
      </nav>
      <div class="sidebar-spacer" />
      <div class="security-note">
        <LockKeyhole :size="18" />
        <div><strong>本地安全模式</strong><span>工作区隔离 · 全程审计</span></div>
      </div>
      <div class="version">EvoAgent v0.1.0</div>
    </aside>

    <section class="main-shell">
      <header class="topbar">
        <div class="topbar-title"><Menu :size="19" /><span>{{ pageTitle }}</span></div>
        <div class="topbar-status">
          <span class="health-dot" :class="{ offline: !store.backendOnline }" />
          {{ store.backendOnline ? '本地服务正常' : '正在连接本地服务' }}
          <Activity :size="16" />
        </div>
      </header>
      <main class="content"><RouterView /></main>
    </section>

    <transition name="toast">
      <div v-if="store.toast" class="toast-message" :class="store.toast.type">{{ store.toast.message }}</div>
    </transition>
    <div v-if="store.loadingCount" class="global-progress"><span /></div>
  </div>
</template>
