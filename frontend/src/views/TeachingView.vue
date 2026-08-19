<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getDocument, GlobalWorkerOptions, Util, type PDFDocumentProxy } from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  ArrowLeft, Bot, ChevronLeft, ChevronRight, Circle, CircleStop, Eraser, Expand,
  FileUp, Highlighter, LoaderCircle, Maximize2, MessageCircleQuestion, Minus, MousePointer2,
  Pause, PenLine, Play, Plus, RotateCcw, RotateCw, Send, Type, Undo2,
} from 'lucide-vue-next'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

GlobalWorkerOptions.workerSrc = pdfWorker

type Tool = 'pointer' | 'pen' | 'highlighter' | 'circle' | 'eraser' | 'text'
type Point = { x: number; y: number }
type Box = { x: number; y: number; width: number; height: number }

const router = useRouter(), store = useAppStore()
const documents = ref<Entity[]>([]), agents = ref<Entity[]>([])
const session = ref<Entity | null>(null), selectedDocumentId = ref(''), selectedAgentId = ref('')
const busy = ref(''), question = ref(''), fileInput = ref<HTMLInputElement | null>(null)
const chatBox = ref<HTMLElement | null>(null), stage = ref<HTMLElement | null>(null)
const pageCanvas = ref<HTMLCanvasElement | null>(null), annotationSvg = ref<SVGSVGElement | null>(null)
// PDF.js proxies use native private fields and must never be wrapped by Vue's deep proxy.
const pdf = shallowRef<PDFDocumentProxy | null>(null), page = ref(1), zoom = ref(1)
const split = ref(Number(window.localStorage.getItem('teaching-split') || 64))
const draggingSplit = ref(false), activeTool = ref<Tool>('pointer')
const annotations = ref<Entity[]>([]), redoStack = ref<Entity[]>([]), drawingId = ref('')
const textDraft = ref(''), renderToken = ref(0), requestToken = ref(0)
const teacherFocusBox = ref<Entity | null>(null)
const teacherWriting = ref('')
let resizeObserver: ResizeObserver | null = null
let saveTimer = 0
const textGeometryCache = new Map<number, Box[]>()
const reflowedTeacherPages = new Set<number>()

const currentDocument = computed(() => session.value?.document || documents.value.find(item => item.id === selectedDocumentId.value))
const currentPageData = computed(() => currentDocument.value?.sections?.[Math.max(0, page.value - 1)] || {})
const pageCount = computed(() => Number(currentDocument.value?.page_count || pdf.value?.numPages || 1))
const pageAnnotations = computed(() => annotations.value.filter(item => Number(item.page) === page.value))
const canTeach = computed(() => Boolean(session.value && !['stopped', 'completed'].includes(session.value.status)))
const progress = computed(() => Math.round(100 * Math.max(0, page.value - 1) / Math.max(1, pageCount.value)))
const isFallbackSlides = computed(() => currentDocument.value?.metadata?.source_kind === 'pptx' && !currentDocument.value?.has_rendered_file)
const activeAssistantTurn = computed(() => [...(session.value?.turns || [])].reverse().find(
  (turn: Entity) => turn.role === 'assistant' && Number(turn.page) === page.value && turn.metadata?.kind !== 'greeting',
))
const teacherFocusText = computed(() => activeAssistantTurn.value?.commands?.find(
  (command: Entity) => command.type === 'focus_text' && Number(command.page) === page.value,
)?.text || '')
const latestAssistantPage = computed(() => Number([...(session.value?.turns || [])].reverse().find(
  (turn: Entity) => turn.role === 'assistant' && turn.metadata?.kind !== 'greeting',
)?.page || 0))

function annotationPoints(item: Entity): string {
  return (item.payload?.points || []).map((point: Point) => `${point.x * 1000},${point.y * 1000}`).join(' ')
}
function circleShape(item: Entity) {
  const start = item.payload?.start || { x: 0, y: 0 }, end = item.payload?.end || start
  return {
    cx: (start.x + end.x) * 500,
    cy: (start.y + end.y) * 500,
    rx: Math.abs(end.x - start.x) * 500,
    ry: Math.abs(end.y - start.y) * 500,
  }
}
function handwritingLines(item: Entity): string[] {
  const text = String(item.payload?.text || '').trim()
  const lineLength = item.kind === 'formula' ? 18 : 11
  return text.match(new RegExp(`.{1,${lineLength}}`, 'g'))?.slice(0, 3) || ['']
}
function handwritingShape(item: Entity) {
  const lines = handwritingLines(item)
  const longest = Math.max(...lines.map(line => line.length), 1)
  const charWidth = item.kind === 'formula' ? 19 : 25
  return {
    x: Number(item.payload?.x || .05) * 1000,
    y: Number(item.payload?.y || .08) * 1000,
    width: Math.min(430, Math.max(135, longest * charWidth + 22)),
    height: lines.length * 38 + 18,
    lines,
  }
}
function normalizedPoint(event: PointerEvent): Point {
  const rect = annotationSvg.value!.getBoundingClientRect()
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  }
}
function beginDraw(event: PointerEvent) {
  if (!session.value || !annotationSvg.value || !['pen', 'highlighter', 'circle'].includes(activeTool.value)) return
  annotationSvg.value.setPointerCapture(event.pointerId)
  const id = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`
  const point = normalizedPoint(event)
  const item: Entity = {
    id, page: page.value, author: 'student', kind: activeTool.value,
    payload: activeTool.value === 'circle' ? { start: point, end: point, color: '#1769c2' } : {
      points: [point], color: activeTool.value === 'highlighter' ? '#ffd54a' : '#263b4d',
    },
  }
  annotations.value.push(item); drawingId.value = id; redoStack.value = []
}
function moveDraw(event: PointerEvent) {
  if (!drawingId.value) return
  const item = annotations.value.find(value => value.id === drawingId.value)
  if (!item) return
  const point = normalizedPoint(event)
  if (item.kind === 'circle') item.payload.end = point
  else item.payload.points.push(point)
}
function endDraw() { if (drawingId.value) { drawingId.value = ''; scheduleSave() } }
function eraseAnnotation(id: string) {
  if (activeTool.value !== 'eraser') return
  const index = annotations.value.findIndex(item => item.id === id)
  if (index >= 0) { redoStack.value = []; annotations.value.splice(index, 1); scheduleSave() }
}
function addText() {
  const value = textDraft.value.trim()
  if (!value || !session.value) return
  annotations.value.push({
    id: `local-${Date.now()}`, page: page.value, author: 'student', kind: 'text',
    payload: { x: .08, y: .12 + (pageAnnotations.value.filter(item => item.kind === 'text').length % 8) * .075, text: value, color: '#263b4d' },
  })
  textDraft.value = ''; redoStack.value = []; scheduleSave()
}
function undo() {
  const index = annotations.value.map(item => item.page).lastIndexOf(page.value)
  if (index < 0) return
  redoStack.value.push(annotations.value[index]); annotations.value.splice(index, 1); scheduleSave()
}
function redo() {
  const item = redoStack.value.pop(); if (!item) return
  annotations.value.push(item); scheduleSave()
}
function scheduleSave() {
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => void saveAnnotations(), 500)
}
async function saveAnnotations() {
  if (!session.value) return
  try {
    await api.put(`/teaching/sessions/${session.value.id}/annotations`, {
      annotations: annotations.value.map(({ page, author, kind, payload }) => ({ page, author, kind, payload })),
    })
  } catch (error: any) { store.notify(error.message || '批注保存失败', 'error') }
}

async function loadBase() {
  busy.value = 'loading'
  try {
    const [docs, agentItems, sessions] = await Promise.all([
      api.get<Entity[]>('/teaching/documents'),
      api.get<Entity[]>('/agents'),
      api.get<Entity[]>('/teaching/sessions'),
    ])
    documents.value = docs
    agents.value = agentItems.filter(item => item.status === 'active')
    selectedAgentId.value = agents.value.find(item => item.slug === 'learning-socratic-tutor')?.id || agents.value[0]?.id || ''
    const resumable = sessions.find(item => !['completed', 'stopped'].includes(item.status))
    if (resumable) await openSession(resumable)
    else {
      closeDocument()
      selectedDocumentId.value = docs[0]?.id || ''
      if (selectedDocumentId.value) await createSession()
    }
  } catch (error: any) { store.notify(error.message || '教学空间载入失败', 'error') }
  finally { busy.value = '' }
}
async function importFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  busy.value = 'upload'
  try {
    const document = await api.upload<Entity>('/teaching/documents', file)
    documents.value = [document, ...documents.value.filter(item => item.id !== document.id)]
    selectedDocumentId.value = document.id
    await createSession()
    store.notify('课件已导入，教师 Agent 已完成讲解准备')
  } catch (error: any) { store.notify(error.message || '课件导入失败', 'error') }
  finally { busy.value = ''; if (fileInput.value) fileInput.value.value = '' }
}
async function createSession() {
  if (!selectedDocumentId.value) return
  busy.value = 'session'
  try {
    const result = await api.post<Entity>('/teaching/sessions', {
      document_id: selectedDocumentId.value, agent_id: selectedAgentId.value || null,
      pace: 'standard', depth: 'course', duration_minutes: 45, proactive_questions: true,
    })
    await openSession(result)
  } catch (error: any) { store.notify(error.message || '讲解会话创建失败', 'error') }
  finally { busy.value = '' }
}
async function openSession(value: Entity) {
  session.value = value; selectedDocumentId.value = value.document_id
  selectedAgentId.value = value.agent_id || selectedAgentId.value
  annotations.value = value.annotations || []; redoStack.value = []
  page.value = Math.min(Math.max(1, Number(value.current_page || 1)), Number(value.document?.page_count || 1))
  await loadRenderedDocument(); await scrollChat()
}
function closeDocument() {
  pdf.value?.destroy(); pdf.value = null; session.value = null; annotations.value = []; page.value = 1; textGeometryCache.clear(); reflowedTeacherPages.clear()
}
async function loadRenderedDocument() {
  pdf.value?.destroy(); pdf.value = null; textGeometryCache.clear(); reflowedTeacherPages.clear()
  if (!session.value?.document?.has_rendered_file) { await nextTick(); return }
  busy.value = 'document'
  try {
    const blob = await api.getBlob(`/teaching/documents/${session.value.document_id}/file`)
    const bytes = new Uint8Array(await blob.arrayBuffer())
    pdf.value = await getDocument({ data: bytes }).promise
  } catch (error: any) { store.notify(error.message || '课件页面载入失败', 'error') }
  finally {
    busy.value = ''
    // The loading branch removes the canvas from the DOM. Wait until Vue mounts
    // it again before asking PDF.js to paint the first page.
    await nextTick()
    if (pdf.value) await renderPage()
  }
}
async function renderPage() {
  if (!pdf.value || !pageCanvas.value || !stage.value) return
  const token = ++renderToken.value, pdfPage = await pdf.value.getPage(page.value)
  if (token !== renderToken.value) return
  const base = pdfPage.getViewport({ scale: 1 })
  const available = Math.max(320, stage.value.clientWidth - 48)
  const scale = Math.max(.35, available / base.width) * zoom.value
  const viewport = pdfPage.getViewport({ scale })
  const canvas = pageCanvas.value, ratio = window.devicePixelRatio || 1
  canvas.width = Math.floor(viewport.width * ratio); canvas.height = Math.floor(viewport.height * ratio)
  canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`
  await pdfPage.render({ canvasContext: canvas.getContext('2d')!, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] }).promise
  if (token === renderToken.value) {
    await locateTeacherFocus(pdfPage, viewport)
    await reflowTeacherAnnotations()
  }
}
async function locateTextBox(pdfPage: any, viewport: any, text: string): Promise<Entity | null> {
  const focus = String(text || '').replace(/\s+/g, '')
  if (!focus) return null
  const content = await pdfPage.getTextContent()
  const needle = focus.slice(0, Math.min(8, focus.length))
  const item = content.items.find((candidate: any) => {
    const value = String(candidate.str || '').replace(/\s+/g, '')
    return value && (value.includes(needle) || needle.includes(value)) && Math.min(value.length, needle.length) >= 2
  }) as any
  if (!item?.transform) return null
  const transform = Util.transform(viewport.transform, item.transform)
  const fontHeight = Math.max(8, Math.hypot(transform[2], transform[3]))
  const width = Math.max(18, Number(item.width || 0) * viewport.scale)
  return {
    x: Math.max(0, transform[4] / viewport.width * 1000 - 5),
    y: Math.max(0, (transform[5] - fontHeight) / viewport.height * 1000 - 5),
    width: Math.min(1000, width / viewport.width * 1000 + 10),
    height: Math.min(1000, fontHeight / viewport.height * 1000 + 10),
  }
}
async function locateTeacherFocus(pdfPage: any, viewport: any) {
  teacherFocusBox.value = await locateTextBox(pdfPage, viewport, teacherFocusText.value)
}
async function locateCommandTarget(target: string): Promise<Entity | null> {
  if (!pdf.value || !pageCanvas.value) return null
  const pdfPage = await pdf.value.getPage(page.value)
  const base = pdfPage.getViewport({ scale: 1 })
  const scale = Math.max(.1, pageCanvas.value.clientWidth / base.width)
  return locateTextBox(pdfPage, pdfPage.getViewport({ scale }), target)
}
async function pageTextGeometry(): Promise<Box[]> {
  if (textGeometryCache.has(page.value)) return textGeometryCache.get(page.value)!
  if (!pdf.value) return []
  const pdfPage = await pdf.value.getPage(page.value)
  const viewport = pdfPage.getViewport({ scale: 1 })
  const content = await pdfPage.getTextContent()
  const boxes = content.items.flatMap((item: any) => {
    if (!item?.transform || !String(item.str || '').trim()) return []
    const transform = Util.transform(viewport.transform, item.transform)
    const height = Math.max(5, Math.hypot(transform[2], transform[3])) / viewport.height
    return [{
      x: Math.max(0, transform[4] / viewport.width),
      y: Math.max(0, (transform[5] - height * viewport.height) / viewport.height),
      width: Math.min(1, Math.max(.006, Number(item.width || 0) / viewport.width)),
      height: Math.min(.08, height),
    }]
  })
  textGeometryCache.set(page.value, boxes)
  return boxes
}
function overlapArea(first: Box, second: Box): number {
  const width = Math.max(0, Math.min(first.x + first.width, second.x + second.width) - Math.max(first.x, second.x))
  const height = Math.max(0, Math.min(first.y + first.height, second.y + second.height) - Math.max(first.y, second.y))
  return width * height
}
async function findBlankPosition(text: string, kind: 'text'|'formula', preferredX: number, preferredY: number): Promise<Box> {
  const lines = text.match(new RegExp(`.{1,${kind === 'formula' ? 18 : 11}}`, 'g'))?.slice(0, 3) || ['']
  const width = Math.min(.43, Math.max(.135, Math.max(...lines.map(line => line.length)) * (kind === 'formula' ? .019 : .025) + .022))
  const height = lines.length * .038 + .018
  const occupied = await pageTextGeometry()
  occupied.push({ x: 0, y: 0, width: .44, height: .085 })
  for (const item of annotations.value.filter(value => value.page === page.value && ['text', 'formula'].includes(value.kind))) {
    const shape = handwritingShape(item)
    occupied.push({ x: shape.x / 1000, y: shape.y / 1000, width: shape.width / 1000, height: shape.height / 1000 })
  }
  const candidates: Box[] = []
  for (let y = .055; y + height <= .965; y += .025) {
    for (const x of [.045, .18, .34, .5, .66, .8]) {
      if (x + width <= .965) candidates.push({ x, y, width, height })
    }
  }
  const ranked = candidates.map(candidate => ({
    ...candidate,
    overlap: occupied.reduce((sum, box) => sum + overlapArea(candidate, box), 0),
    distance: Math.abs(candidate.x - preferredX) + Math.abs(candidate.y - preferredY),
  })).sort((left, right) => left.overlap - right.overlap || left.distance - right.distance)
  return ranked[0] || { x: .55, y: .08, width, height }
}
async function reflowTeacherAnnotations() {
  if (reflowedTeacherPages.has(page.value)) return
  reflowedTeacherPages.add(page.value)
  const notes = annotations.value.filter(item => item.page === page.value && item.author === 'teacher' && ['text', 'formula'].includes(item.kind))
  if (!notes.length) return
  annotations.value = annotations.value.filter(item => !notes.includes(item))
  for (const item of notes) {
    const text = String(item.payload?.text || '')
    const kind = item.kind === 'formula' ? 'formula' : 'text'
    const position = await findBlankPosition(text, kind, Number(item.payload?.x || .57), Number(item.payload?.y || .16))
    item.payload = { ...item.payload, x: position.x, y: position.y, paper: true }
    annotations.value.push(item)
  }
  scheduleSave()
}
function commandLabel(command: Entity) {
  return ({
    highlight_text: '正在高亮课件重点',
    circle_text: '正在圈出关键概念',
    write_note: '正在写板书草稿',
    write_formula: '正在写公式',
  } as Record<string, string>)[command.type] || '正在标注课件'
}
async function applyTeacherCommands(turn: Entity) {
  const commands = (turn.commands || []).filter((command: Entity) => command.type !== 'focus_text' && Number(command.page) === page.value)
  for (const [index, command] of commands.entries()) {
    const id = `teacher-${turn.id}-${index}`
    if (annotations.value.some(item => item.id === id)) continue
    teacherWriting.value = commandLabel(command)
    await new Promise(resolve => window.setTimeout(resolve, 420))
    let item: Entity | null = null
    if (command.type === 'write_note' || command.type === 'write_formula') {
      const kind = command.type === 'write_formula' ? 'formula' : 'text'
      const text = String(command.text || '').slice(0, kind === 'formula' ? 54 : 33)
      const position = await findBlankPosition(text, kind, Number(command.x || .57), Number(command.y || .16))
      item = {
        id, page: page.value, author: 'teacher', kind,
        payload: {
          x: position.x, y: position.y,
          text, color: command.color || '#1769c2', paper: true,
        },
      }
    } else {
      const box = await locateCommandTarget(String(command.target || ''))
      if (box && command.type === 'highlight_text') {
        const y = (box.y + box.height * .58) / 1000
        item = {
          id, page: page.value, author: 'teacher', kind: 'highlighter',
          payload: { color: '#ffd54a', points: [{ x: box.x / 1000, y }, { x: (box.x + box.width) / 1000, y }] },
        }
      } else if (box && command.type === 'circle_text') {
        item = {
          id, page: page.value, author: 'teacher', kind: 'circle',
          payload: {
            color: command.color || '#1769c2',
            start: { x: box.x / 1000, y: box.y / 1000 },
            end: { x: (box.x + box.width) / 1000, y: (box.y + box.height) / 1000 },
          },
        }
      } else {
        const text = `重点：${String(command.target || '').slice(0, 26)}`
        const position = await findBlankPosition(text, 'text', .57, .16 + index * .08)
        item = {
          id, page: page.value, author: 'teacher', kind: 'text',
          payload: { x: position.x, y: position.y, text, color: '#1769c2', paper: true },
        }
      }
    }
    if (item) { annotations.value.push(item); scheduleSave() }
  }
  teacherWriting.value = ''
}
function setPage(value: number) {
  teacherFocusBox.value = null
  page.value = Math.min(pageCount.value, Math.max(1, value)); if (session.value) void control('seek')
}
function syncToTurn(turn: Entity) {
  if (turn.role !== 'assistant' || !turn.page) return
  setPage(Number(turn.page))
}
async function control(action: 'start'|'pause'|'resume'|'stop'|'seek'|'complete') {
  if (!session.value) return
  const token = action === 'stop' || action === 'pause' ? ++requestToken.value : requestToken.value
  if (action === 'stop' || action === 'pause') busy.value = ''
  try {
    const result = await api.patch<Entity>(`/teaching/sessions/${session.value.id}/control`, { action, page: page.value })
    if (token <= requestToken.value) session.value = { ...session.value, ...result }
    if (action === 'start') await requestTeaching('explain')
    if (action === 'resume') await requestTeaching('continue')
  } catch (error: any) { store.notify(error.message || '讲解控制失败', 'error') }
}
async function requestTeaching(action: 'explain'|'ask'|'continue') {
  if (!session.value || busy.value === 'teaching') return
  const message = question.value.trim()
  if (action === 'ask' && !message) return
  const token = ++requestToken.value
  if (message) session.value.turns.push({ id: `local-${Date.now()}`, role: 'user', content: message, page: page.value })
  question.value = ''; busy.value = 'teaching'; await scrollChat()
  try {
    const turn = await api.post<Entity>(`/teaching/sessions/${session.value.id}/turns`, { message, action, page: page.value })
    if (token !== requestToken.value) return
    session.value.turns.push(turn)
    session.value.status = 'explaining'
    if (Number(turn.page) !== page.value) page.value = Number(turn.page)
    await nextTick()
    await renderPage()
    await applyTeacherCommands(turn)
    await scrollChat()
  } catch (error: any) { if (token === requestToken.value) store.notify(error.message || '教师 Agent 暂时无法回答', 'error') }
  finally { if (token === requestToken.value) busy.value = '' }
}
async function scrollChat() { await nextTick(); chatBox.value?.scrollTo({ top: chatBox.value.scrollHeight, behavior: 'smooth' }) }
function beginSplit(event: PointerEvent) { draggingSplit.value = true; (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId) }
function moveSplit(event: PointerEvent) {
  if (!draggingSplit.value) return
  const main = (event.currentTarget as HTMLElement).parentElement!, rect = main.getBoundingClientRect()
  split.value = Math.min(80, Math.max(45, ((event.clientX - rect.left) / rect.width) * 100))
}
function endSplit() { draggingSplit.value = false; window.localStorage.setItem('teaching-split', String(split.value)) }
async function toggleFullscreen() { if (!document.fullscreenElement) await document.documentElement.requestFullscreen(); else await document.exitFullscreen() }

watch(page, () => void renderPage())
watch(zoom, () => void renderPage())
onMounted(async () => {
  await loadBase()
  resizeObserver = new ResizeObserver(() => void renderPage()); if (stage.value) resizeObserver.observe(stage.value)
})
onBeforeUnmount(() => { window.clearTimeout(saveTimer); resizeObserver?.disconnect(); pdf.value?.destroy(); requestToken.value += 1 })
</script>

<template>
  <div class="teaching-space">
    <header class="teaching-topbar">
      <button class="icon-button" title="返回工作台" @click="router.push('/')"><ArrowLeft :size="17" /></button>
      <div class="document-title"><strong>{{ currentDocument?.title || '智能讲解教室' }}</strong><small>独立教学空间 · 课件、进度与批注不关联学习方向</small></div>
      <div v-if="session" class="lesson-progress"><span><i :style="{ width: `${progress}%` }" /></span><b>{{ progress }}%</b><small>第 {{ page }}/{{ pageCount }} 页</small></div>
      <button v-if="session?.status !== 'explaining'" class="control primary" :disabled="busy==='teaching'" @click="control(session?.status==='paused'?'resume':'start')"><Play :size="14" />{{ session?.status==='paused'?'继续':'开始讲解' }}</button>
      <button v-else class="control" @click="control('pause')"><Pause :size="14" />暂停</button>
      <button class="control" :disabled="!session" @click="control('stop')"><CircleStop :size="14" />停止</button>
      <button class="icon-button" :disabled="busy==='upload'" title="导入新课件" @click="fileInput?.click()"><FileUp :size="16" /></button>
      <button class="icon-button" title="全屏" @click="toggleFullscreen"><Maximize2 :size="16" /></button>
    </header>

    <section v-if="!session" class="teaching-empty">
      <FileUp :size="44" /><h2>导入一份 PDF 或 PPTX 课件</h2><p>左侧显示课件并支持圈画板书，右侧由教师 Agent 进行短轮次讲解。</p>
      <div class="empty-controls"><select v-model="selectedAgentId"><option value="">默认教师 Agent</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option></select><button class="import-button" :disabled="busy==='upload'" @click="fileInput?.click()"><LoaderCircle v-if="busy==='upload'" class="spin" :size="15" /><FileUp v-else :size="15" />导入课件</button></div>
      <div v-if="documents.length" class="recent-documents"><span>或继续已有课件</span><button v-for="item in documents.slice(0,5)" :key="item.id" @click="selectedDocumentId=item.id;createSession()">{{ item.title }}<small>{{ item.page_count }} 页</small></button></div>
    </section>

    <main v-else class="teaching-main" :style="{ gridTemplateColumns: `${split}% 8px minmax(280px,1fr)` }">
      <section class="document-pane">
        <header class="document-tools">
          <nav>
            <button v-for="item in ([['pointer',MousePointer2,'指针'],['pen',PenLine,'画笔'],['highlighter',Highlighter,'高亮'],['circle',Circle,'圈画'],['eraser',Eraser,'橡皮']] as any[])" :key="item[0]" :class="{ active:activeTool===item[0] }" :title="item[2]" @click="activeTool=item[0]"><component :is="item[1]" :size="15" /></button>
            <button :class="{ active:activeTool==='text' }" title="文字板书" @click="activeTool='text'"><Type :size="15" /></button>
            <button title="撤销" @click="undo"><Undo2 :size="15" /></button><button title="重做" @click="redo"><RotateCw :size="15" /></button>
          </nav>
          <div v-if="activeTool==='text'" class="text-board"><input v-model="textDraft" placeholder="输入公式或草稿" @keyup.enter="addText"><button @click="addText">写入</button></div>
          <nav class="page-tools"><button :disabled="page<=1" @click="setPage(page-1)"><ChevronLeft :size="15" /></button><label><input :value="page" type="number" min="1" :max="pageCount" @change="setPage(Number(($event.target as HTMLInputElement).value))"> / {{ pageCount }}</label><button :disabled="page>=pageCount" @click="setPage(page+1)"><ChevronRight :size="15" /></button><button @click="zoom=Math.max(.5,zoom-.1)"><Minus :size="14" /></button><span>{{ Math.round(zoom*100) }}%</span><button @click="zoom=Math.min(2.5,zoom+.1)"><Plus :size="14" /></button><button title="恢复适页" @click="zoom=1"><Expand :size="14" /></button></nav>
        </header>
        <div ref="stage" class="document-stage" :class="{ drawing:activeTool!=='pointer' }">
          <div v-if="busy==='document'" class="stage-loading"><LoaderCircle class="spin" :size="24" />正在载入课件…</div>
          <div v-else class="page-wrap" :style="isFallbackSlides ? {} : { width:pageCanvas?.style.width, height:pageCanvas?.style.height }">
            <canvas v-if="!isFallbackSlides" ref="pageCanvas" class="pdf-page" />
            <article v-else class="slide-fallback"><small>幻灯片 {{ page }}</small><h1>{{ currentPageData.title }}</h1><div>{{ currentPageData.text || '此页未提取到可讲解文字，请查看原始课件。' }}</div><footer>{{ currentDocument.metadata?.conversion_note }}</footer></article>
            <div v-if="teacherFocusText" class="teacher-focus-label"><Bot :size="13" /><span><b>教师正在讲解</b>{{ teacherFocusText }}</span></div>
            <div v-if="teacherWriting" class="teacher-writing-status"><PenLine :size="13" />{{ teacherWriting }}</div>
            <svg ref="annotationSvg" class="annotation-layer" viewBox="0 0 1000 1000" preserveAspectRatio="none" @pointerdown="beginDraw" @pointermove="moveDraw" @pointerup="endDraw" @pointercancel="endDraw">
              <rect v-if="teacherFocusBox" class="teacher-focus-box" :x="teacherFocusBox.x" :y="teacherFocusBox.y" :width="teacherFocusBox.width" :height="teacherFocusBox.height" rx="5" />
              <template v-for="item in pageAnnotations" :key="item.id">
                <polyline v-if="item.kind==='pen'||item.kind==='highlighter'" :data-id="item.id" :points="annotationPoints(item)" fill="none" :stroke="item.payload.color" :stroke-width="item.kind==='highlighter'?14:3" :opacity="item.kind==='highlighter' ? .27 : .9" stroke-linecap="round" stroke-linejoin="round" @pointerdown.stop="eraseAnnotation(item.id)" />
                <ellipse v-else-if="item.kind==='circle'" v-bind="circleShape(item)" fill="none" :stroke="item.payload.color||'#1769c2'" stroke-width="2.2" @pointerdown.stop="eraseAnnotation(item.id)" />
                <g v-else-if="(item.kind==='text'||item.kind==='formula')&&item.author==='teacher'" class="teacher-handwriting" @pointerdown.stop="eraseAnnotation(item.id)">
                  <rect :x="handwritingShape(item).x-7" :y="handwritingShape(item).y-7" :width="handwritingShape(item).width" :height="handwritingShape(item).height" rx="8" />
                  <text :x="handwritingShape(item).x+4" :y="handwritingShape(item).y+29" :fill="item.payload.color||'#1769c2'" :class="['handwriting','teacher',{ formula:item.kind==='formula' }]">
                    <tspan v-for="(line,lineIndex) in handwritingShape(item).lines" :key="lineIndex" :x="handwritingShape(item).x+4" :dy="lineIndex===0?0:38">{{ line }}</tspan>
                  </text>
                </g>
                <text v-else-if="item.kind==='text'||item.kind==='formula'" :x="item.payload.x*1000" :y="item.payload.y*1000" :fill="item.payload.color||'#263b4d'" :class="['handwriting',{ formula:item.kind==='formula' }]" @pointerdown.stop="eraseAnnotation(item.id)">{{ item.payload.text }}</text>
              </template>
            </svg>
          </div>
        </div>
      </section>

      <button class="split-handle" :class="{ active:draggingSplit }" title="拖动调整左右宽度" @pointerdown="beginSplit" @pointermove="moveSplit" @pointerup="endSplit" @pointercancel="endSplit"><i /></button>

      <aside class="teacher-pane">
        <header><div class="teacher-avatar"><Bot :size="20" /></div><span><strong>{{ session.agent?.name || '教师 Agent' }}</strong><small><i :class="session.status" />{{ ({ready:'等待开始',explaining:'正在讲解',paused:'已暂停',stopped:'已停止',completed:'已完成'} as any)[session.status] || session.status }} · 当前第 {{ page }} 页</small></span></header>
        <div ref="chatBox" class="teacher-chat">
          <div class="page-sync-banner" :class="{ pending:latestAssistantPage!==page }"><span><b>PDF 第 {{ page }} 页</b>{{ latestAssistantPage===page?'右侧讲解已与当前页面同步':'页面已切换，等待教师讲解当前页' }}</span><button v-if="latestAssistantPage!==page" :disabled="busy==='teaching'" @click="requestTeaching('explain')"><Play :size="11" />讲解当前页</button></div>
          <article v-for="turn in session.turns" :key="turn.id" :class="[turn.role,{ current:turn.role==='assistant'&&Number(turn.page)===page }]" :title="turn.role==='assistant'?'点击同步到对应PDF页':''" @click="syncToTurn(turn)"><b>{{ turn.role==='user'?'我':'教师' }}</b><p>{{ turn.content }}</p><small v-if="turn.role==='assistant'">第 {{ turn.page }} 页<span v-if="turn.metadata?.board_action_count"> · 已同步 {{ turn.metadata.board_action_count }} 项教师板书</span><span v-if="turn.citations?.length"> · {{ turn.citations.length }} 条来源</span></small><details v-if="turn.citations?.length" @click.stop><summary>查看知识来源</summary><div v-for="citation in turn.citations" :key="citation.id||citation.title"><strong>{{ citation.title }}</strong><span>{{ citation.source }}</span></div></details></article>
          <div v-if="busy==='teaching'" class="teacher-thinking"><LoaderCircle class="spin" :size="15" />教师正在组织下一个要点…</div>
        </div>
        <footer>
          <div class="quick-actions"><button :disabled="!canTeach||busy==='teaching'" @click="requestTeaching('continue')"><Play :size="12" />继续讲一点</button><button :disabled="!canTeach" @click="control('pause')"><Pause :size="12" />先停一下</button><button @click="question='请换一个更直观的例子解释。';requestTeaching('ask')"><RotateCcw :size="12" />换个例子</button></div>
          <div class="question-box"><MessageCircleQuestion :size="17" /><textarea v-model="question" rows="2" placeholder="随时提问或打断教师……" @keydown.enter.exact.prevent="requestTeaching('ask')" /><button :disabled="!question.trim()||busy==='teaching'" @click="requestTeaching('ask')"><Send :size="16" /></button></div>
          <small>Enter发送 · 教师每次只讲一个要点 · 批注自动保存</small>
        </footer>
      </aside>
    </main>
    <input ref="fileInput" type="file" accept=".pdf,.pptx" hidden @change="importFile">
  </div>
</template>

<style scoped>
.teaching-space{height:calc(100vh - 60px);min-height:620px;margin:-22px;display:flex;flex-direction:column;overflow:hidden;color:#263f52;background:#edf2f5}.teaching-topbar{height:58px;flex:0 0 58px;padding:0 14px;display:flex;align-items:center;gap:9px;border-bottom:1px solid #cfdae2;background:#fff}.icon-button,.control,.document-tools button{height:32px;padding:0 9px;border:1px solid #d2dde5;border-radius:6px;display:flex;align-items:center;justify-content:center;gap:5px;color:#416076;background:#fff;cursor:pointer}.icon-button{width:34px;padding:0}.icon-button:hover,.control:hover,.document-tools button:hover,.document-tools button.active{color:#1769c2;border-color:#8bb9d9;background:#eef7fd}.project-select{width:176px;height:34px;padding:0 9px;border:1px solid #ccd9e2;border-radius:6px;color:#315169;background:#fff}.document-title{min-width:0;flex:1;display:grid}.document-title strong,.document-title small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.document-title strong{font-size:12px}.document-title small{margin-top:2px;color:#7c8e9a;font-size:8px}.lesson-progress{width:185px;display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:7px}.lesson-progress>span{height:5px;overflow:hidden;border-radius:9px;background:#e4ebef}.lesson-progress i{display:block;height:100%;background:#2678bb}.lesson-progress b{color:#2f658b;font-size:9px}.lesson-progress small{color:#84939d;font-size:8px}.control{white-space:nowrap;font-size:9px}.control.primary{color:#fff;border-color:#1769c2;background:#1769c2}.control:disabled,.document-tools button:disabled{opacity:.42;cursor:not-allowed}.teaching-empty{flex:1;display:grid;place-content:center;justify-items:center;gap:10px;color:#83a0b2;background:#f7f9fa}.teaching-empty h2,.teaching-empty p{margin:0}.teaching-empty h2{color:#375a72;font-size:17px}.teaching-empty p{font-size:10px}.empty-controls{display:flex;gap:8px}.empty-controls select{width:230px;border:1px solid #ccdae4;border-radius:7px;padding:0 9px}.import-button{height:38px;padding:0 15px;border:0;border-radius:7px;display:flex;align-items:center;gap:6px;color:#fff;background:#1769c2;cursor:pointer}.recent-documents{width:470px;margin-top:14px;display:grid;gap:5px}.recent-documents>span{color:#7c909f;font-size:8px;text-align:left}.recent-documents button{height:35px;padding:0 10px;border:1px solid #d5e1e8;border-radius:6px;display:flex;align-items:center;justify-content:space-between;color:#3b5e75;background:#fff}.recent-documents small{color:#8a9aa5}.teaching-main{min-height:0;flex:1;display:grid}.document-pane,.teacher-pane{min-width:0;min-height:0;display:flex;flex-direction:column;background:#fff}.document-tools{height:45px;flex:0 0 45px;padding:0 10px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #d7e0e6}.document-tools nav{display:flex;align-items:center;gap:4px}.document-tools button{width:30px;height:29px;padding:0}.page-tools{margin-left:auto}.page-tools label{display:flex;align-items:center;color:#728897;font-size:8px}.page-tools input{width:38px;border:0;text-align:right;color:#36586f;background:transparent}.page-tools span{width:35px;text-align:center;color:#60788a;font-size:8px}.text-board{min-width:180px;display:flex}.text-board input{min-width:0;flex:1;height:28px;padding:0 7px;border:1px solid #cad9e3;border-radius:5px 0 0 5px;font-family:KaiTi,'STKaiti',cursive}.text-board button{width:42px!important;border-radius:0 5px 5px 0}.document-stage{min-height:0;flex:1;overflow:auto;overscroll-behavior:contain;background:#dfe5e9}.page-wrap{position:relative;min-width:300px;min-height:420px;margin:22px auto;box-shadow:0 5px 22px #253c4d26;background:#fff}.pdf-page{display:block;background:#fff}.annotation-layer{position:absolute;inset:0;width:100%;height:100%;touch-action:none;pointer-events:none}.document-stage.drawing .annotation-layer{pointer-events:auto;cursor:crosshair}.annotation-layer polyline,.annotation-layer ellipse,.annotation-layer text{pointer-events:stroke}.document-stage.drawing .annotation-layer text{pointer-events:all}.handwriting{font:30px KaiTi,'STKaiti','Segoe Print',cursive;paint-order:stroke;stroke:#ffffff70;stroke-width:1px}.slide-fallback{width:min(820px,calc(64vw - 60px));min-height:520px;padding:70px 74px;box-sizing:border-box;display:flex;flex-direction:column;color:#29475b;background:#fff}.slide-fallback>small{color:#1769c2;font-weight:700}.slide-fallback h1{margin:25px 0 32px;font-size:28px}.slide-fallback div{white-space:pre-wrap;font-size:16px;line-height:1.9}.slide-fallback footer{margin-top:auto;padding-top:20px;color:#8998a2;font-size:9px}.stage-loading{height:100%;display:grid;place-content:center;justify-items:center;gap:8px;color:#6f8797;font-size:9px}.split-handle{width:8px;padding:0;border:0;display:grid;place-items:center;background:#d9e1e6;cursor:col-resize}.split-handle i{width:2px;height:42px;border-radius:3px;background:#9cabb5}.split-handle:hover,.split-handle.active{background:#c6d9e6}.split-handle:hover i,.split-handle.active i{background:#1769c2}.teacher-pane{border-left:1px solid #cfd9e0}.teacher-pane>header{height:55px;flex:0 0 55px;padding:0 14px;display:flex;align-items:center;gap:9px;border-bottom:1px solid #d8e1e7}.teacher-avatar{width:32px;height:32px;display:grid;place-items:center;border-radius:50%;color:#1769c2;background:#e8f3fb}.teacher-pane>header span{display:grid}.teacher-pane>header strong{font-size:11px}.teacher-pane>header small{margin-top:3px;color:#7d8f9b;font-size:8px}.teacher-pane>header i{width:6px;height:6px;margin-right:5px;display:inline-block;border-radius:50%;background:#95a4ad}.teacher-pane>header i.explaining{background:#1a966c;box-shadow:0 0 0 3px #1a966c1c}.teacher-pane>header i.paused{background:#d18a22}.teacher-chat{min-height:0;flex:1;padding:14px;overflow-y:auto;scrollbar-gutter:stable;background:#f6f8f9}.teacher-chat article{max-width:88%;margin-bottom:12px;padding:10px 11px;border:1px solid #dce5ea;border-radius:9px;background:#fff}.teacher-chat article.user{margin-left:auto;border-color:#b9d7e9;background:#eaf5fc}.teacher-chat b{color:#557085;font-size:8px}.teacher-chat p{margin:5px 0 0;color:#304d61;font-size:10px;line-height:1.68;white-space:pre-wrap}.teacher-chat small{display:block;margin-top:7px;color:#91a0aa;font-size:7px}.teacher-thinking{display:flex;align-items:center;gap:7px;color:#678093;font-size:8px}.teacher-pane>footer{padding:10px 12px;border-top:1px solid #d5e0e7;background:#fff}.quick-actions{margin-bottom:7px;display:flex;gap:5px}.quick-actions button{height:27px;padding:0 8px;border:1px solid #d4dfe6;border-radius:5px;display:flex;align-items:center;gap:4px;color:#597286;background:#fff;font-size:8px}.quick-actions button:hover{color:#1769c2;border-color:#9ac0db}.quick-actions button:disabled{opacity:.42}.question-box{display:grid;grid-template-columns:auto minmax(0,1fr) 34px;align-items:center;gap:7px;padding:6px 6px 6px 9px;border:1px solid #cbd9e2;border-radius:8px;color:#6c8394}.question-box:focus-within{border-color:#6fa6cd;box-shadow:0 0 0 3px #1769c214}.question-box textarea{min-height:38px;border:0;outline:0;resize:none;color:#2f4c61;font:9px/1.45 inherit}.question-box button{width:32px;height:32px;border:0;border-radius:6px;display:grid;place-items:center;color:#fff;background:#1769c2}.question-box button:disabled{opacity:.4}.teacher-pane>footer>small{display:block;margin-top:5px;color:#96a3ac;font-size:7px}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:980px){.lesson-progress{display:none}.teaching-main{grid-template-columns:minmax(0,1fr)!important}.split-handle{display:none}.teacher-pane{position:absolute;right:0;top:58px;bottom:0;width:360px;box-shadow:-6px 0 25px #263d4e25}.document-title small{display:none}}@media(max-width:700px){.project-select{width:120px}.document-title{display:none}.teacher-pane{width:100%}.control{padding:0 6px}.slide-fallback{width:90vw;padding:35px}}
.teacher-chat details{margin-top:7px;padding-top:6px;border-top:1px solid #e4eaee;color:#648096;font-size:7px}.teacher-chat summary{cursor:pointer}.teacher-chat details div{padding:5px 0;display:grid;gap:2px}.teacher-chat details strong{color:#36596f}.teacher-chat details span{color:#8798a3}
.teacher-focus-label{position:absolute;left:14px;top:14px;z-index:3;max-width:min(72%,460px);padding:7px 10px;display:flex;align-items:flex-start;gap:7px;border:1px solid #7eb4d8;border-radius:7px;color:#1769c2;background:#f4faffeb;box-shadow:0 4px 14px #1c5f8c20;pointer-events:none}.teacher-focus-label span{display:grid;gap:2px;color:#49697f;font-size:8px;line-height:1.4}.teacher-focus-label b{color:#1769c2;font-size:7px}.teacher-focus-box{fill:#5fb8ff28;stroke:#1769c2;stroke-width:1.4;stroke-dasharray:5 4;pointer-events:none;animation:teacher-focus-pulse 1.35s ease-in-out infinite alternate}@keyframes teacher-focus-pulse{to{fill:#5fb8ff44;stroke-width:2}}.page-sync-banner{position:sticky;top:0;z-index:2;margin:-5px 0 10px;padding:7px 8px;border:1px solid #b8d8cb;border-radius:7px;display:flex;align-items:center;gap:8px;color:#247359;background:#edf9f4f2;backdrop-filter:blur(6px)}.page-sync-banner.pending{color:#93621b;border-color:#e5c787;background:#fff9edf2}.page-sync-banner span{min-width:0;flex:1;display:grid;gap:2px;font-size:7px}.page-sync-banner b{font-size:8px}.page-sync-banner button{height:24px;padding:0 7px;border:1px solid currentColor;border-radius:5px;display:flex;align-items:center;gap:3px;color:inherit;background:#fff;font-size:7px;cursor:pointer}.teacher-chat article.assistant{cursor:pointer}.teacher-chat article.assistant:hover{border-color:#91bad5}.teacher-chat article.assistant.current{border-color:#6ca8cf;box-shadow:inset 3px 0 #1769c2;background:#fbfdff}
.teacher-writing-status{position:absolute;right:14px;top:14px;z-index:4;padding:7px 10px;display:flex;align-items:center;gap:6px;border:1px solid #9ec4dc;border-radius:7px;color:#1769c2;background:#fffffff2;box-shadow:0 4px 14px #1c5f8c1c;pointer-events:none;animation:writing-pulse .7s ease-in-out infinite alternate}@keyframes writing-pulse{to{transform:translateY(2px);opacity:.72}}.teacher-handwriting rect{fill:#fffef6;fill-opacity:.94;stroke:#90b9d1;stroke-width:1.2;filter:drop-shadow(0 2px 3px #173e5720)}.handwriting.teacher{filter:drop-shadow(0 1px 0 #fff);font-size:25px}.handwriting.formula{font-family:'Cambria Math','STIX Two Math',KaiTi,'STKaiti',cursive;font-size:28px;font-style:italic}
</style>
