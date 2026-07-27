<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import {
  AlertTriangle, Check, ExternalLink, Globe2, LoaderCircle, RefreshCw,
  Search, ShieldCheck, SkipForward,
} from 'lucide-vue-next'
import FloatingPanel from './FloatingPanel.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

type ResearchVisit = Entity & {
  id: string
  url: string
  title: string
  provider: string
  status: string
  nodeLabel?: string
  verificationId?: string
}

const props = defineProps<{
  modelValue: boolean
  visits: ResearchVisit[]
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'verification-completed': [payload: { verificationId: string; approved: boolean }]
}>()
const store = useAppStore()
const activeId = ref('')
const processing = ref(false)

const current = computed(() => props.visits.find(item => item.id === activeId.value) || props.visits[0] || null)
const verificationCount = computed(() => props.visits.filter(item => item.status === 'verification_required').length)
const visitedCount = computed(() => props.visits.filter(item => ['fetched', 'metadata-only', 'search-snippet'].includes(item.status)).length)
const failedCount = computed(() => props.visits.filter(item => item.status.startsWith('failed') || item.status === 'blocked').length)

watch(() => props.visits, visits => {
  if (!visits.some(item => item.id === activeId.value)) activeId.value = visits[0]?.id || ''
}, { deep: true, immediate: true })

function host(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, '') }
  catch { return url }
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    discovered: '已发现', searching: '正在检索', searched: '检索完成', visiting: '正在访问', fetched: '已读取',
    'metadata-only': '仅题录', 'search-snippet': '搜索摘要', verification_required: '等待验证',
    verified: '验证已提交', skipped: '已跳过', blocked: '已拦截',
  }
  return labels[status] || (status.startsWith('failed') ? '访问失败' : status)
}

async function openCurrent() {
  if (!current.value?.url) return
  try {
    if ('__TAURI_INTERNALS__' in window) {
      await invoke('open_research_browser', {
        url: current.value.url,
        title: current.value.title || host(current.value.url),
      })
    } else {
      window.open(current.value.url, '_blank', 'noopener,noreferrer')
    }
  } catch (error: any) {
    store.notify(String(error || '无法打开联网验证窗口'), 'error')
  }
}

async function finishVerification(approved: boolean) {
  const visit = current.value
  if (!visit?.verificationId || processing.value) return
  processing.value = true
  try {
    let cookies: Entity[] = []
    if (approved && '__TAURI_INTERNALS__' in window) {
      cookies = await invoke<Entity[]>('research_browser_cookies', { url: visit.url })
    }
    await api.post('/research-browser/verifications/complete', {
      verification_id: visit.verificationId,
      approved,
      url: visit.url,
      cookies,
    })
    emit('verification-completed', { verificationId: visit.verificationId, approved })
    store.notify(approved ? '验证会话已同步，正在重试学术检索' : '已跳过该站点，将使用其他来源继续')
  } catch (error: any) {
    store.notify(error.message || String(error) || '提交验证结果失败', 'error')
  } finally {
    processing.value = false
  }
}
</script>

<template>
  <FloatingPanel
    :model-value="modelValue"
    title="联网访问中心"
    eyebrow="LIVE RESEARCH BROWSER"
    description="集中查看本次工作流发现、访问和筛选的站点；遇到机器人验证时可在隔离窗口中手动通过。"
    size="large"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="research-browser-center">
      <section class="research-browser-summary">
        <article><Globe2 :size="16" /><span><strong>{{ visits.length }}</strong><small>站点与检索页</small></span></article>
        <article><Check :size="16" /><span><strong>{{ visitedCount }}</strong><small>已读取</small></span></article>
        <article :class="{warning:verificationCount}"><ShieldCheck :size="16" /><span><strong>{{ verificationCount }}</strong><small>等待人工验证</small></span></article>
        <article :class="{danger:failedCount}"><AlertTriangle :size="16" /><span><strong>{{ failedCount }}</strong><small>访问失败</small></span></article>
      </section>

      <div v-if="visits.length" class="research-browser-layout">
        <nav class="research-site-tabs" aria-label="联网访问站点">
          <button
            v-for="visit in visits"
            :key="visit.id"
            :class="[visit.status,{active:current?.id===visit.id}]"
            @click="activeId=visit.id"
          >
            <i><LoaderCircle v-if="['searching','visiting'].includes(visit.status)" :size="12" /><ShieldCheck v-else-if="visit.status==='verification_required'" :size="12" /><Globe2 v-else :size="12" /></i>
            <span><strong>{{ visit.title || host(visit.url) }}</strong><small>{{ host(visit.url) }} · {{ statusLabel(visit.status) }}</small></span>
          </button>
        </nav>

        <section v-if="current" class="research-site-detail">
          <header>
            <div><span>{{ current.provider || '网络来源' }}<b>{{ statusLabel(current.status) }}</b></span><h3>{{ current.title || host(current.url) }}</h3><p>{{ current.url }}</p></div>
            <button class="research-open-button" @click="openCurrent"><ExternalLink :size="14" />在统一访问窗口打开</button>
          </header>

          <div v-if="current.status==='verification_required'" class="research-verification-callout">
            <ShieldCheck :size="22" />
            <div><strong>该检索站点正在等待人工验证</strong><p>先打开站点并按页面提示完成验证，再同步验证会话。EvoAgent 只会使用反爬所需的非登录 Cookie，且不会写入数据库。</p></div>
          </div>
          <div v-else class="research-site-preview">
            <Search :size="28" />
            <strong>站点状态：{{ statusLabel(current.status) }}</strong>
            <p v-if="current.nodeLabel">来自工作流节点“{{ current.nodeLabel }}”</p>
            <p>外部学术站点常禁止 iframe 嵌入，因此统一访问窗口使用桌面 WebView 直接打开，便于登录、翻页或完成验证。</p>
          </div>
        </section>
      </div>
      <div v-else class="research-browser-empty"><Globe2 :size="34" /><strong>尚无联网访问记录</strong><span>启动包含资料检索的 Agent 节点后，站点会实时出现在这里。</span></div>
    </div>
    <template #footer>
      <span class="research-browser-privacy"><ShieldCheck :size="13" />验证会话仅存活于当前客户端运行期间</span>
      <button v-if="current?.status==='verification_required'" class="btn" :disabled="processing" @click="finishVerification(false)"><SkipForward :size="13" />跳过并使用备用源</button>
      <button v-if="current?.status==='verification_required'" class="btn btn-primary" :disabled="processing" @click="finishVerification(true)"><RefreshCw :size="13" />{{ processing ? '正在同步…' : '我已通过验证，继续' }}</button>
      <button v-else class="btn" @click="emit('update:modelValue',false)">关闭</button>
    </template>
  </FloatingPanel>
</template>

<style scoped>
.research-browser-center{display:grid;gap:14px;min-height:520px}.research-browser-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.research-browser-summary article{display:flex;align-items:center;gap:9px;padding:11px 12px;border:1px solid #d4e5ef;border-radius:12px;color:#1975ac;background:linear-gradient(145deg,#f7fbff,#fff)}.research-browser-summary article.warning{color:#ad6b0b;border-color:#f0cf96;background:#fff9ed}.research-browser-summary article.danger{color:#b84a4a;border-color:#edc5c5;background:#fff7f7}.research-browser-summary span{display:grid}.research-browser-summary strong{font-size:17px}.research-browser-summary small{color:#778d9d;font-size:10px}.research-browser-layout{min-height:430px;display:grid;grid-template-columns:280px minmax(0,1fr);gap:12px}.research-site-tabs{max-height:440px;padding-right:4px;display:flex;flex-direction:column;gap:6px;overflow:auto}.research-site-tabs button{width:100%;padding:9px;border:1px solid #dbe6ed;border-radius:10px;display:flex;align-items:flex-start;gap:8px;color:#3f6077;background:#fff;text-align:left;cursor:pointer}.research-site-tabs button:hover,.research-site-tabs button.active{border-color:#74b8df;background:#f1f9ff;box-shadow:0 4px 14px rgba(29,111,161,.09)}.research-site-tabs button.verification_required{border-color:#e7b75f;background:#fff9ed}.research-site-tabs i{display:grid;place-items:center;width:24px;height:24px;flex:0 0 24px;border-radius:7px;color:#177db7;background:#e6f4fc}.research-site-tabs button.verification_required i{color:#a7680b;background:#ffedca}.research-site-tabs button.searching svg,.research-site-tabs button.visiting svg{animation:spin .9s linear infinite}.research-site-tabs span{min-width:0;display:grid;gap:3px}.research-site-tabs strong,.research-site-tabs small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.research-site-tabs strong{font-size:11px}.research-site-tabs small{color:#7c909f;font-size:9px}.research-site-detail{padding:16px;border:1px solid #d6e4ed;border-radius:14px;background:linear-gradient(150deg,#fff,#f8fbfd)}.research-site-detail>header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:15px;border-bottom:1px solid #e1e9ee}.research-site-detail header>div{min-width:0}.research-site-detail header span{display:flex;align-items:center;gap:7px;color:#1681b6;font-size:10px;font-weight:800}.research-site-detail header span b{padding:3px 7px;border-radius:99px;color:#577287;background:#edf3f6;font-size:8px}.research-site-detail h3{margin:7px 0 5px;color:#183c54;font-size:16px}.research-site-detail header p{margin:0;max-width:720px;overflow:hidden;color:#718796;font:9px/1.4 Consolas,monospace;text-overflow:ellipsis;white-space:nowrap}.research-open-button{padding:8px 11px;border:1px solid #91c6e4;border-radius:9px;display:flex;align-items:center;gap:6px;color:#126fa4;background:#eef8ff;font-size:10px;font-weight:800;white-space:nowrap;cursor:pointer}.research-verification-callout{margin-top:16px;padding:18px;display:flex;align-items:flex-start;gap:12px;border:1px solid #eac472;border-radius:13px;color:#a06409;background:linear-gradient(135deg,#fffaf0,#fff3d9)}.research-verification-callout div{display:grid;gap:5px}.research-verification-callout strong{font-size:13px}.research-verification-callout p{margin:0;color:#866b41;font-size:10px;line-height:1.65}.research-site-preview{min-height:280px;display:grid;place-items:center;align-content:center;gap:8px;color:#72a3bf;text-align:center}.research-site-preview strong{color:#355f79;font-size:13px}.research-site-preview p{max-width:570px;margin:0;color:#7b8f9e;font-size:10px;line-height:1.7}.research-browser-empty{min-height:430px;display:grid;place-items:center;align-content:center;gap:8px;color:#82a7bd}.research-browser-empty strong{color:#41677e;font-size:13px}.research-browser-empty span{color:#8093a0;font-size:10px}.research-browser-privacy{margin-right:auto;display:flex;align-items:center;gap:5px;color:#658375;font-size:10px}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:820px){.research-browser-summary{grid-template-columns:1fr 1fr}.research-browser-layout{grid-template-columns:1fr}.research-site-tabs{max-height:160px}.research-site-detail>header{display:grid}.research-open-button{width:max-content}}
</style>
