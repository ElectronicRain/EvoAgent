<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Bot, ChevronsUp, Layers3, LoaderCircle, X } from 'lucide-vue-next'
import AgentChatWindow from './AgentChatWindow.vue'
import { useAgentChatStore, type AgentChatWindow as ChatWindowState } from '../stores/agentChat'

const chat = useAgentChatStore()
const route = useRoute()
const minimized = computed(() => chat.minimizedWindows)

function runningFor(item: ChatWindowState) {
  return chat.runningTasks.some(task => task.agent.id === item.agent.id)
}

watch(() => route.fullPath, (_next, previous) => {
  if (previous && chat.expandedWindows.length) chat.minimizeAll()
})
</script>

<template>
  <AgentChatWindow
    v-for="item in chat.windows"
    :key="item.id"
    :window-id="item.id"
  />

  <Teleport to="body">
    <Transition name="stack-dock">
      <section
        v-if="minimized.length"
        class="agent-stack-dock"
        role="button"
        tabindex="0"
        aria-label="展开全部已最小化的 Agent 对话"
        @click="chat.restoreAll()"
        @keydown.enter.prevent="chat.restoreAll()"
      >
        <header>
          <span class="stack-mark"><Layers3 :size="18" /></span>
          <span><strong>{{ minimized.length }} 个 Agent 对话</strong><small>单击展开全部窗口</small></span>
          <ChevronsUp :size="17" />
        </header>
        <div class="agent-stack-list">
          <article
            v-for="(item, index) in minimized"
            :key="item.id"
            class="agent-stack-card"
            :style="{ '--stack-index': index }"
          >
            <span class="stack-avatar">
              <Bot :size="16" />
              <i v-if="runningFor(item)" />
            </span>
            <span class="stack-copy">
              <strong>{{ item.agent.name }}</strong>
              <small>{{ runningFor(item) ? '任务正在后台执行' : '对话已最小化' }}</small>
            </span>
            <LoaderCircle v-if="runningFor(item)" class="stack-running" :size="14" />
            <button
              title="关闭这个对话，运行中的任务仍会继续"
              @click.stop="chat.closeWindow(item.id)"
            >
              <X :size="13" />
            </button>
          </article>
        </div>
      </section>
    </Transition>
  </Teleport>
</template>

<style scoped>
.agent-stack-dock{position:fixed;right:22px;bottom:22px;z-index:1400;width:292px;padding:9px;border:1px solid #88b8db;border-radius:16px;background:rgba(244,250,255,.97);box-shadow:0 20px 52px rgba(12,52,84,.3);backdrop-filter:blur(14px);cursor:pointer;user-select:none}
.agent-stack-dock>header{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:9px;padding:4px 5px 9px;color:#215479}
.agent-stack-dock>header>span:nth-child(2){display:flex;flex-direction:column;gap:1px}
.agent-stack-dock>header strong{font-size:11px}
.agent-stack-dock>header small{font-size:8px;color:#7690a5}
.stack-mark{display:grid;width:31px;height:31px;place-items:center;border-radius:9px;color:#fff;background:linear-gradient(135deg,#1769c2,#25a5bc)}
.agent-stack-list{max-height:min(420px,60vh);padding:0 2px 2px;overflow:auto}
.agent-stack-card{position:relative;display:flex;align-items:center;gap:9px;min-height:57px;margin-top:-4px;padding:8px 7px 8px 8px;border:1px solid #c8deec;border-radius:11px;background:#fff;box-shadow:0 7px 18px rgba(30,85,125,.1);transform:translateX(calc(var(--stack-index) * -1px))}
.agent-stack-card:first-child{margin-top:0}
.stack-avatar{position:relative;display:grid;width:34px;height:34px;flex:none;place-items:center;border-radius:9px;color:#fff;background:linear-gradient(135deg,#1769c2,#25a5bc)}
.stack-avatar i{position:absolute;right:-2px;bottom:-2px;width:9px;height:9px;border:2px solid #fff;border-radius:50%;background:#20b774}
.stack-copy{display:flex;min-width:0;flex:1;flex-direction:column;gap:2px}
.stack-copy strong{overflow:hidden;color:#214b6d;font-size:10px;text-overflow:ellipsis;white-space:nowrap}
.stack-copy small{color:#7891a5;font-size:8px}
.agent-stack-card button{display:grid;width:24px;height:24px;flex:none;place-items:center;border:0;border-radius:7px;color:#8096a8;background:transparent;cursor:pointer}
.agent-stack-card button:hover{color:#b44444;background:#fff1f1}
.stack-running{flex:none;color:#1786bd;animation:stack-spin 1.2s linear infinite}
.stack-dock-enter-active,.stack-dock-leave-active{transition:opacity .2s ease,transform .2s ease}
.stack-dock-enter-from,.stack-dock-leave-to{opacity:0;transform:translateY(12px) scale(.96)}
@keyframes stack-spin{to{transform:rotate(360deg)}}
@media(max-width:720px){.agent-stack-dock{right:12px;bottom:12px;width:min(292px,calc(100vw - 24px))}}
</style>
