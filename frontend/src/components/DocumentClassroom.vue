<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import {
  Bot, CircleStop, Eraser, Expand, Highlighter, ListChecks, MessageCircleQuestion,
  Mic2, Minimize2, Pause, Play, Send, Volume2,
} from 'lucide-vue-next'
import { api, type Entity } from '../services/api'

const props = defineProps<{ artifact: Entity; agentName: string; conversationId: string }>()
const emit = defineEmits<{ ask: [question: string] }>()
const enlarged = ref(false), drawing = ref(false), speaking = ref(false), paused = ref(false)
const activeSection = ref(-1), question = ref(''), penColor = ref('#e33f4f')
const preparing = ref(false), autoBoard = ref(true), planMode = ref('')
const teachingPlan = ref<Entity[]>([]), boardLines = ref<string[]>([]), writingLine = ref('')
const fallbackReason = ref(''), modelEndpoint = ref(''), selectionMode = ref(false)
const selectedSections = ref<number[]>([]), voiceOptions = ref<SpeechSynthesisVoice[]>([])
const selectedVoice = ref('cloud:claire'), teachingStyle = ref<'natural'|'lively'|'rigorous'>('natural')
const cloudTtsAvailable = ref(false)
const surface = ref<HTMLElement | null>(null), canvas = ref<HTMLCanvasElement | null>(null)
const autoCanvas = ref<HTMLCanvasElement | null>(null), sectionEls = ref<HTMLElement[]>([])
let utterance: SpeechSynthesisUtterance | null = null
let currentAudio: HTMLAudioElement | null = null
let currentAudioUrl = ''
let drawingNow = false
let lessonToken = 0

const sections = computed(() => String(props.artifact?.content || '').split(/\n{2,}/).filter(Boolean))
const selectedCount = computed(() => selectedSections.value.length)

function renderMarkdown(source: string) {
  const formulas: string[] = []
  let prepared = source.replace(/\$\$([\s\S]+?)\$\$/g, (_all, formula) => {
    const index = formulas.push(katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false })) - 1
    return `EVOFORMULA${index}END`
  })
  prepared = prepared.replace(/\$([^$\n]+?)\$/g, (_all, formula) => {
    const index = formulas.push(katex.renderToString(formula.trim(), { displayMode: false, throwOnError: false })) - 1
    return `EVOFORMULA${index}END`
  })
  let html = String(marked.parse(prepared, { gfm: true, breaks: true }))
  html = html.replace(/EVOFORMULA(\d+)END/g, (_all, index) => formulas[Number(index)] || '')
  return DOMPurify.sanitize(html)
}

function plainText(markdown: string) {
  const holder = document.createElement('div')
  holder.innerHTML = renderMarkdown(markdown)
  return holder.textContent?.replace(/\s+/g, ' ').trim() || ''
}

function lesson(index: number) {
  return teachingPlan.value.find(item => item.section_index === index)
}

function delay(ms: number) { return new Promise(resolve => window.setTimeout(resolve, ms)) }

async function ensureTeachingPlan() {
  preparing.value = true
  try {
    const result = await api.post<Entity>(`/conversations/${props.conversationId}/teaching-plan`, {
      artifact_id: props.artifact.id,
      section_indices: selectedSections.value,
    })
    teachingPlan.value = result.sections || []
    planMode.value = result.mode || 'fallback'
    fallbackReason.value = result.fallback_reason || ''
    modelEndpoint.value = result.model_endpoint || ''
    cloudTtsAvailable.value = Boolean(result.cloud_tts_available)
    if (!cloudTtsAvailable.value && selectedVoice.value.startsWith('cloud:')) selectedVoice.value = voiceOptions.value[0]?.name || ''
  } finally { preparing.value = false }
}

async function startTeaching() {
  if (!selectedSections.value.length) {
    fallbackReason.value = '请先至少选择一个需要讲解的部分'
    selectionMode.value = true
    return
  }
  stopSpeaking()
  try { await ensureTeachingPlan() }
  catch (error: any) { fallbackReason.value = error?.message || '教学脚本生成失败'; return }
  boardLines.value = []
  writingLine.value = ''
  clearAutoCanvas()
  await resizeCanvas()
  speakSection([...selectedSections.value].sort((a,b)=>a-b)[0])
}

function speakSection(index = 0) {
  if (!('speechSynthesis' in window) || !sections.value.length) return
  window.speechSynthesis.cancel()
  activeSection.value = Math.min(index, sections.value.length - 1)
  const currentLesson = lesson(activeSection.value)
  speaking.value = true
  paused.value = false
  const token = ++lessonToken
  void presentSection(activeSection.value, token)
  void speakNaturally(currentLesson?.narration || plainText(sections.value[activeSection.value]), activeSection.value, token)
}

function voiceStyle(sentence: string) {
  const base = teachingStyle.value === 'lively'
    ? { rate:.98, pitch:1.12 }
    : teachingStyle.value === 'rigorous'
      ? { rate:.82, pitch:.94 }
      : { rate:.9, pitch:1.02 }
  if (/[？?]$/.test(sentence)) return { rate:base.rate*.92, pitch:base.pitch+.1, pause:360 }
  if (/[！!]$/.test(sentence)) return { rate:base.rate*1.03, pitch:base.pitch+.07, pause:260 }
  if (/公式|推导|注意|关键|易错/.test(sentence)) return { rate:base.rate*.88, pitch:base.pitch, pause:380 }
  return { ...base, pause:220 }
}

function speakSentence(sentence: string, token: number) {
  return new Promise<void>(resolve => {
    if (token !== lessonToken || !speaking.value) return resolve()
    utterance = new SpeechSynthesisUtterance(sentence)
    const style = voiceStyle(sentence)
    utterance.lang = 'zh-CN'
    utterance.rate = style.rate
    utterance.pitch = style.pitch
    utterance.volume = 1
    utterance.voice = voiceOptions.value.find(item => item.name === selectedVoice.value) || null
    utterance.onend = () => resolve()
    utterance.onerror = () => resolve()
    window.speechSynthesis.speak(utterance)
  })
}

async function speakNaturally(narration: string, sectionIndex: number, token: number) {
  if (cloudTtsAvailable.value && selectedVoice.value.startsWith('cloud:')) {
    try {
      await speakCloud(narration, token)
      if (token === lessonToken && speaking.value) advanceSection(sectionIndex, token)
      return
    } catch (error: any) {
      fallbackReason.value = `云端真人语音暂不可用，已切换系统音色：${error?.message || '未知错误'}`
    }
  }
  const sentences = narration.match(/[^。！？!?；;]+[。！？!?；;]?/g)?.map(item=>item.trim()).filter(Boolean) || [narration]
  for (const sentence of sentences) {
    if (token !== lessonToken || !speaking.value) return
    await speakSentence(sentence, token)
    if (token !== lessonToken || !speaking.value) return
    await delay(voiceStyle(sentence).pause)
  }
  advanceSection(sectionIndex, token)
}

function advanceSection(sectionIndex: number, token: number) {
  const ordered = [...selectedSections.value].sort((a,b)=>a-b)
  const position = ordered.indexOf(sectionIndex)
  const next = ordered[position+1]
  if (next !== undefined && speaking.value && token === lessonToken) speakSection(next)
  else if (token === lessonToken) stopSpeaking()
}

async function speakCloud(narration: string, token: number) {
  const blob = await api.blob(`/conversations/${props.conversationId}/classroom-speech`, {
    input: narration,
    voice: selectedVoice.value.replace('cloud:', ''),
    style: teachingStyle.value,
  })
  if (token !== lessonToken || !speaking.value) return
  if (currentAudioUrl) URL.revokeObjectURL(currentAudioUrl)
  currentAudioUrl = URL.createObjectURL(blob)
  currentAudio = new Audio(currentAudioUrl)
  await new Promise<void>((resolve, reject) => {
    if (!currentAudio) return resolve()
    currentAudio.onended = () => resolve()
    currentAudio.onerror = () => reject(new Error('音频播放失败'))
    currentAudio.play().catch(reject)
  })
}

function togglePause() {
  if (!speaking.value) return speakSection(Math.max(activeSection.value, 0))
  if (currentAudio && !currentAudio.ended) {
    if (paused.value) void currentAudio.play()
    else currentAudio.pause()
  } else if (paused.value) window.speechSynthesis.resume()
  else window.speechSynthesis.pause()
  paused.value = !paused.value
}

function stopSpeaking() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  currentAudio?.pause(); currentAudio = null
  if (currentAudioUrl) { URL.revokeObjectURL(currentAudioUrl); currentAudioUrl = '' }
  speaking.value = false
  paused.value = false
  activeSection.value = -1
  lessonToken += 1
}

async function resizeCanvas() {
  await nextTick()
  if (!surface.value || !canvas.value) return
  const width = Math.max(surface.value.scrollWidth, surface.value.clientWidth)
  const height = Math.max(surface.value.scrollHeight, surface.value.clientHeight)
  canvas.value.width = width
  canvas.value.height = height
  if (autoCanvas.value) { autoCanvas.value.width = width; autoCanvas.value.height = height }
}

function setSectionRef(element: any, index: number) { if (element) sectionEls.value[index] = element as HTMLElement }

function canvasRectFor(element: Element | Range) {
  if (!surface.value) return null
  const rect = element.getBoundingClientRect(), parent = surface.value.getBoundingClientRect()
  return {
    x: rect.left - parent.left + surface.value.scrollLeft,
    y: rect.top - parent.top + surface.value.scrollTop,
    width: rect.width,
    height: rect.height,
  }
}

function findPhrase(container: HTMLElement, phrase: string) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) {
    const node = walker.currentNode as Text, start = (node.textContent || '').indexOf(phrase)
    if (start >= 0) { const range = document.createRange(); range.setStart(node, start); range.setEnd(node, start + phrase.length); return range }
  }
  return null
}

function inkContext() {
  const ctx = autoCanvas.value?.getContext('2d')
  if (!ctx) return null
  ctx.strokeStyle = '#e33f4f'; ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.globalAlpha = .9
  return ctx
}

async function animateCircle(rect: {x:number;y:number;width:number;height:number}, token: number) {
  const ctx = inkContext(); if (!ctx) return
  const cx=rect.x+rect.width/2, cy=rect.y+rect.height/2, rx=Math.max(18,rect.width/2+8), ry=Math.max(12,rect.height/2+5)
  ctx.beginPath()
  for (let i=0;i<=44 && token===lessonToken;i++) { const a=Math.PI*2*i/44; const x=cx+rx*Math.cos(a), y=cy+ry*Math.sin(a); i ? ctx.lineTo(x,y) : ctx.moveTo(x,y); ctx.stroke(); await delay(9) }
}

async function writeBoard(steps: string[], token: number) {
  for (const step of steps) {
    writingLine.value = ''
    for (const char of step) {
      if (token !== lessonToken || !speaking.value) return
      while (paused.value && token === lessonToken) await delay(80)
      writingLine.value += char
      await delay(/[，。；：]/.test(char) ? 85 : 24)
    }
    boardLines.value.push(writingLine.value)
    writingLine.value = ''
    await delay(180)
  }
}

async function presentSection(index: number, token: number) {
  await nextTick()
  const element = sectionEls.value[index], current = lesson(index)
  if (!element || !surface.value) return
  surface.value.scrollTo({ top: Math.max(0, element.offsetTop - 36), behavior: 'smooth' })
  if (!autoBoard.value) return
  await delay(250)
  for (const phrase of (current?.focus_phrases || []).slice(0, 3)) {
    const range = findPhrase(element, String(phrase)), rect = range ? canvasRectFor(range) : null
    if (rect) await animateCircle(rect, token)
  }
  void writeBoard(current?.board_steps || [], token)
}

function point(event: PointerEvent) {
  const rect = canvas.value!.getBoundingClientRect()
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}

function drawStart(event: PointerEvent) {
  if (!drawing.value || !canvas.value) return
  drawingNow = true
  canvas.value.setPointerCapture(event.pointerId)
  const ctx = canvas.value.getContext('2d')!
  const p = point(event)
  ctx.beginPath(); ctx.moveTo(p.x, p.y)
}

function drawMove(event: PointerEvent) {
  if (!drawingNow || !canvas.value) return
  const ctx = canvas.value.getContext('2d')!
  const p = point(event)
  ctx.lineTo(p.x, p.y)
  ctx.strokeStyle = penColor.value
  ctx.lineWidth = penColor.value === '#ffd43b' ? 12 : 3
  ctx.globalAlpha = penColor.value === '#ffd43b' ? 0.38 : 0.92
  ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.stroke()
}

function drawEnd() { drawingNow = false }
function clearAutoCanvas() { autoCanvas.value?.getContext('2d')?.clearRect(0, 0, autoCanvas.value.width, autoCanvas.value.height) }
function clearCanvas() {
  canvas.value?.getContext('2d')?.clearRect(0, 0, canvas.value.width, canvas.value.height)
  clearAutoCanvas(); boardLines.value = []; writingLine.value = ''
}

function documentClick(event: MouseEvent) {
  const formula = (event.target as HTMLElement).closest('.katex')
  if (formula && !drawing.value) {
    const value = formula.getAttribute('aria-label') || formula.textContent || '当前公式'
    emit('ask', `请像老师板书一样，逐符号、逐步骤讲解这个公式：${value}`)
  }
}

function ask() {
  const value = question.value.trim()
  if (!value) return
  emit('ask', `请结合当前生成的研究文档，像课堂老师一样回答：${value}`)
  question.value = ''
}

function loadVoices() {
  const available = window.speechSynthesis?.getVoices?.() || []
  voiceOptions.value = available.filter(item =>
    /^zh/i.test(item.lang) || /Chinese|Xiaoxiao|Yunxi|晓晓|云希/i.test(item.name),
  )
  if (!voiceOptions.value.length) voiceOptions.value = available
  if (!selectedVoice.value && voiceOptions.value.length) {
    selectedVoice.value = voiceOptions.value.find(item =>
      /Natural|Xiaoxiao|晓晓|Yunxi|云希/i.test(item.name),
    )?.name || voiceOptions.value[0].name
  }
}

function toggleSelected(index: number) {
  selectedSections.value = selectedSections.value.includes(index)
    ? selectedSections.value.filter(item => item !== index)
    : [...selectedSections.value, index].sort((a,b)=>a-b)
}
function selectAll() { selectedSections.value = sections.value.map((_item,index)=>index) }
function selectNone() { selectedSections.value = [] }

watch(enlarged, resizeCanvas)
watch(() => props.artifact?.id, () => {
  stopSpeaking(); teachingPlan.value=[]; planMode.value=''; fallbackReason.value=''; selectAll(); clearCanvas(); void resizeCanvas()
})
watch(sections, () => { if (!selectedSections.value.length) selectAll() }, { immediate:true })
onMounted(() => {
  loadVoices()
  if ('speechSynthesis' in window) window.speechSynthesis.onvoiceschanged = loadVoices
})
onBeforeUnmount(() => {
  stopSpeaking()
  if ('speechSynthesis' in window) window.speechSynthesis.onvoiceschanged = null
})
</script>

<template>
  <section class="classroom" :class="{ enlarged }">
    <header class="classroom-toolbar">
      <div><Bot :size="15" /><strong>AI 文档讲解课堂</strong><small>{{ agentName }}</small></div>
      <div class="toolbar-actions">
        <button :disabled="preparing" title="AI 自动讲解并板书" @click="startTeaching"><Volume2 :size="14" /></button>
        <button title="暂停/继续" @click="togglePause"><Pause v-if="speaking&&!paused" :size="14" /><Play v-else :size="14" /></button>
        <button title="停止讲解" @click="stopSpeaking"><CircleStop :size="14" /></button>
        <button :class="{ active:autoBoard }" title="Agent 自动圈画与板书" @click="autoBoard=!autoBoard"><Bot :size="14" /></button>
        <button :class="{ active:selectionMode }" title="选择讲解范围" @click="selectionMode=!selectionMode"><ListChecks :size="14" /></button>
        <button :class="{ active:drawing }" title="圈画板书" @click="drawing=!drawing;resizeCanvas()"><Highlighter :size="14" /></button>
        <button title="清空板书" @click="clearCanvas"><Eraser :size="14" /></button>
        <button title="放大文档" @click="enlarged=!enlarged"><Minimize2 v-if="enlarged" :size="14" /><Expand v-else :size="14" /></button>
      </div>
    </header>
    <div class="teaching-options">
      <span><ListChecks :size="12" />讲解范围 {{ selectedCount }}/{{ sections.length }}</span>
      <button @click="selectionMode=!selectionMode">{{ selectionMode?'完成选择':'选择部分' }}</button>
      <button @click="selectAll">全选</button><button @click="selectNone">清空</button>
      <label>音色<select v-model="selectedVoice">
        <optgroup label="云端真人音色（需兼容接口）">
          <option value="cloud:claire">温柔女声 Claire</option><option value="cloud:bella">热情女声 Bella</option><option value="cloud:diana">活泼女声 Diana</option><option value="cloud:anna">沉稳女声 Anna</option>
          <option value="cloud:alex">沉稳男声 Alex</option><option value="cloud:benjamin">深沉男声 Benjamin</option><option value="cloud:charles">磁性男声 Charles</option><option value="cloud:david">活泼男声 David</option>
        </optgroup>
        <optgroup label="Windows 本地音色"><option v-for="voice in voiceOptions" :key="voice.name" :value="voice.name">{{ voice.name }}</option></optgroup>
      </select></label>
      <label>语气<select v-model="teachingStyle"><option value="natural">自然亲切</option><option value="lively">生动活泼</option><option value="rigorous">沉稳严谨</option></select></label>
    </div>
    <div v-if="drawing" class="pen-row">
      <span>板书颜色</span><button class="pen red" @click="penColor='#e33f4f'" /><button class="pen blue" @click="penColor='#2677d5'" /><button class="pen yellow" @click="penColor='#ffd43b'" />
      <small>Agent 会自动圈重点并板书；你也可以随时接管画笔。</small>
    </div>
    <div class="lesson-stage">
      <div ref="surface" class="document-surface" @click="documentClick">
        <article v-for="(section,index) in sections" :key="index" :ref="el=>setSectionRef(el,index)" class="markdown-section" :class="{ speaking:activeSection===index, selectable:selectionMode, selected:selectedSections.includes(index), unselected:selectionMode&&!selectedSections.includes(index) }" @click="selectionMode&&toggleSelected(index)">
          <button v-if="selectionMode" class="section-check" @click.stop="toggleSelected(index)">{{ selectedSections.includes(index)?'✓':'+' }}</button>
          <div v-html="renderMarkdown(section)" />
        </article>
        <canvas ref="autoCanvas" class="auto-ink" />
        <canvas ref="canvas" :class="{ enabled:drawing }" @pointerdown="drawStart" @pointermove="drawMove" @pointerup="drawEnd" @pointercancel="drawEnd" />
      </div>
      <aside class="auto-board" :class="{ active:speaking&&autoBoard }">
        <header><Bot :size="13" />Agent 实时板书 <small>{{ planMode==='model'?`动态讲解 · ${modelEndpoint}`:'智能降级脚本' }}</small></header>
        <div class="chalk-lines">
          <p v-for="(line,index) in boardLines" :key="index">{{ line }}</p>
          <p v-if="writingLine" class="writing">{{ writingLine }}<i /></p>
          <div v-if="!boardLines.length&&!writingLine" class="board-empty">点击“AI 自动讲解并板书”后，Agent 将在这里逐笔推导公式。<em v-if="fallbackReason">{{ fallbackReason }}</em></div>
        </div>
      </aside>
    </div>
    <footer class="teacher-chat">
      <MessageCircleQuestion :size="15" />
      <input v-model="question" placeholder="对当前文档提问，例如：请板书推导雅可比质量指标" @keydown.enter.prevent="ask">
      <button @click="ask"><Send :size="13" />问 AI 老师</button>
      <span v-if="preparing"><Mic2 :size="12" />Agent 正在生成教学与板书脚本…</span>
      <span v-else-if="speaking"><Mic2 :size="12" />正在讲解并自动板书第 {{ activeSection+1 }} / {{ sections.length }} 段</span>
    </footer>
  </section>
</template>

<style scoped>
.classroom { container-type:inline-size; border:1px solid #d5e3ee; border-radius:8px; overflow:hidden; background:#fff; }
.classroom.enlarged { position:fixed; inset:18px; z-index:1000; display:grid; grid-template-rows:auto auto auto minmax(0,1fr) auto; box-shadow:0 20px 70px rgba(10,38,65,.3); }
.classroom-toolbar { min-height:45px; padding:0 10px; display:flex; align-items:center; justify-content:space-between; gap:8px; color:#214d73; background:#edf6fd; border-bottom:1px solid #d5e3ee; }
.classroom-toolbar>div:first-child { display:flex; align-items:center; gap:6px; }
.classroom-toolbar small { color:#7a91a5; font-size:8px; }
.toolbar-actions { display:flex; gap:4px; }
.toolbar-actions button { width:30px; height:29px; border:1px solid #cbdce9; border-radius:5px; display:grid; place-items:center; color:#35688f; background:#fff; }
.toolbar-actions button.active { color:#fff; background:#2677c8; }
.teaching-options{min-height:37px;padding:5px 9px;display:flex;align-items:center;gap:6px;color:#4e6d86;background:#f8fbfe;border-bottom:1px solid #dce7ef;font-size:8px}
.teaching-options>span{display:flex;align-items:center;gap:4px;font-weight:600}.teaching-options button{padding:4px 7px;border:1px solid #cbdce8;border-radius:4px;color:#35688f;background:#fff;font-size:8px}.teaching-options label{margin-left:auto;display:flex;align-items:center;gap:4px}.teaching-options label+label{margin-left:2px}.teaching-options select{max-width:145px;height:24px;border:1px solid #cbdce8;border-radius:4px;color:#345b78;background:#fff;font-size:8px}
.pen-row { padding:7px 10px; display:flex; align-items:center; gap:7px; color:#526f88; background:#fff9e7; font-size:9px; }
.pen-row small { margin-left:auto; }
.pen { width:18px; height:18px; border:2px solid #fff; border-radius:99px; box-shadow:0 0 0 1px #b7c7d5; }.pen.red{background:#e33f4f}.pen.blue{background:#2677d5}.pen.yellow{background:#ffd43b}
.lesson-stage { min-height:0; display:grid; grid-template-columns:minmax(0,1fr) 260px; }
.document-surface { position:relative; max-height:430px; padding:22px 24px 50px; overflow:auto; color:#243e56; background:#fff; }
.enlarged .document-surface { max-height:none; }
.auto-board { min-width:0; max-height:430px; overflow:hidden; color:#e7f4ff; background:linear-gradient(145deg,#123c55,#0b2c41); border-left:1px solid #d5e3ee; box-shadow:inset 0 0 40px rgba(0,0,0,.18); }
.enlarged .auto-board { max-height:none; }
.auto-board header { height:42px;padding:0 10px;display:flex;align-items:center;gap:6px;color:#bfe5ff;border-bottom:1px solid rgba(255,255,255,.15);font-size:10px }.auto-board header small{margin-left:auto;color:#7fb0cd;font-size:7px}
.chalk-lines { height:calc(100% - 42px); padding:15px 14px; overflow:auto; background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px);background-size:100% 28px }
.chalk-lines p { margin:0 0 13px;color:#f1f7e7;font-family:"KaiTi","STKaiti",serif;font-size:15px;line-height:1.55;text-shadow:0 0 2px rgba(255,255,255,.3);white-space:pre-wrap;word-break:break-word }.chalk-lines p:nth-child(3n+2){color:#9bd6ff}.chalk-lines p:nth-child(3n){color:#ffe892}
.chalk-lines .writing i { display:inline-block;width:2px;height:16px;margin-left:2px;vertical-align:-2px;background:#fff;animation:blink .7s infinite }.board-empty{margin-top:30px;color:#7ea2b7;font-size:10px;line-height:1.8;text-align:center}.board-empty em{display:block;margin-top:8px;color:#f0bf7a;font-style:normal;font-size:8px}
@keyframes blink{50%{opacity:0}}
.markdown-section { padding:4px 8px; border-left:3px solid transparent; transition:.2s; }
.markdown-section.speaking { border-left-color:#2382d2; background:#edf7ff; }
.markdown-section.selectable{position:relative;cursor:pointer}.markdown-section.unselected{opacity:.38;filter:grayscale(.5)}.markdown-section.selected{box-shadow:inset 0 0 0 1px #77afe0;background:#f0f8ff}.section-check{position:absolute;right:5px;top:5px;z-index:7;width:22px;height:22px;border:1px solid #6da7d6;border-radius:99px;color:#fff;background:#2580cc;font-size:10px}
.markdown-section :deep(h1){font-size:24px;color:#123f68}.markdown-section :deep(h2){margin-top:18px;font-size:18px;color:#17588c}.markdown-section :deep(h3){font-size:15px;color:#28668f}
.markdown-section :deep(p),.markdown-section :deep(li){font-size:12px;line-height:1.8}.markdown-section :deep(blockquote){margin:10px 0;padding:9px 12px;border-left:4px solid #5a9bd3;background:#f0f7fc;color:#4d6c85}
.markdown-section :deep(table){width:100%;border-collapse:collapse;font-size:11px}.markdown-section :deep(th),.markdown-section :deep(td){padding:7px;border:1px solid #d7e3ec}.markdown-section :deep(th){background:#eaf3fa}
.markdown-section :deep(code){padding:2px 4px;border-radius:3px;background:#edf2f6}.markdown-section :deep(a){color:#0875ce}.markdown-section :deep(.katex){cursor:pointer;color:#173f65}
canvas { position:absolute; inset:0; pointer-events:none; z-index:5; } canvas.auto-ink{z-index:4} canvas.enabled{pointer-events:auto;cursor:crosshair}
.teacher-chat { padding:9px; display:grid; grid-template-columns:auto minmax(180px,1fr) auto; align-items:center; gap:7px; border-top:1px solid #d5e3ee; background:#f6faff; }
.teacher-chat input { height:34px;padding:0 9px;border:1px solid #cbdbe7;border-radius:6px;outline:none;font-size:10px}.teacher-chat button{height:34px;padding:0 10px;border:0;border-radius:6px;display:flex;align-items:center;gap:5px;color:#fff;background:#1769c2;font-size:9px}.teacher-chat span{grid-column:2/-1;display:flex;align-items:center;gap:5px;color:#16805c;font-size:8px}
@container(max-width:680px){.classroom-toolbar{align-items:flex-start;padding:8px;flex-direction:column}.toolbar-actions{width:100%;overflow-x:auto;padding-bottom:2px}.teaching-options{flex-wrap:wrap}.teaching-options label{margin-left:0}.lesson-stage{grid-template-columns:1fr}.auto-board{display:none}.teacher-chat{grid-template-columns:auto minmax(0,1fr)}.teacher-chat button{grid-column:2}.teacher-chat span{grid-column:1/-1}}
</style>
