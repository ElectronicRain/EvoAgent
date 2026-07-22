<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Bot, CircleStop, GitBranch, GripVertical, Maximize2, Minus, MousePointer2,
  Play, Plus, Save, Search, Trash2, Workflow, ZoomIn,
} from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

type CanvasNode = Entity & { position: { x: number; y: number } }
type CanvasEdge = { source: string; target: string }

const store = useAppStore()
const workflows = ref<Entity[]>([]), agents = ref<Entity[]>([]), runs = ref<Entity[]>([])
const currentWorkflow = ref<Entity | null>(null), nodes = ref<CanvasNode[]>([]), edges = ref<CanvasEdge[]>([])
const selectedNodeId = ref(''), selectedEdgeIndex = ref<number | null>(null), connectingFrom = ref(''), pointer = reactive({ x: 0, y: 0 })
const canvas = ref<HTMLElement | null>(null), search = ref(''), task = ref('围绕真实教学科研痛点，形成一份可验证的解决方案。'), output = ref('')
const workflowForm = reactive({ name: '', description: '' })
const nodeWidth = 168, nodeHeight = 82, canvasWidth = 1600, canvasHeight = 900
const zoom = ref(1), workflowRunning = ref(false), workflowRunStatus = ref('idle')
const paletteDrag = reactive({ agent: null as Entity | null, active: false, startX: 0, startY: 0, x: 0, y: 0 })
const selectedNode = computed(() => nodes.value.find(node => node.id === selectedNodeId.value) || null)
const selectedEdge = computed(() => selectedEdgeIndex.value === null ? null : edges.value[selectedEdgeIndex.value] || null)
const visibleAgents = computed(() => agents.value.filter(agent => agent.status === 'active' && agent.name.toLowerCase().includes(search.value.toLowerCase())))
const zoomLabel = computed(() => `${Math.round(zoom.value * 100)}%`)

function parseDefinition(value: string) {
  try { return JSON.parse(value) } catch { return { nodes: [], edges: [] } }
}

function normalizeNodes(definition: Entity): CanvasNode[] {
  const raw = definition.nodes || []
  return raw.map((node: Entity, index: number) => ({
    ...node,
    config: { ...(node.config || {}) },
    position: node.position || { x: 50 + index * 215, y: index % 2 ? 245 : 120 },
  }))
}

async function load() {
  store.loading(true)
  try {
    [workflows.value, agents.value, runs.value] = await Promise.all([api.get('/workflows'), api.get('/agents'), api.get('/workflow-runs')])
    if (currentWorkflow.value) {
      const refreshed = workflows.value.find(item => item.id === currentWorkflow.value?.id)
      if (refreshed) openWorkflow(refreshed)
    } else if (workflows.value.length) openWorkflow(workflows.value[0])
    else newWorkflow()
  } catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

function openWorkflow(workflow: Entity) {
  currentWorkflow.value = workflow
  workflowForm.name = workflow.name
  workflowForm.description = workflow.description
  const definition = parseDefinition(workflow.definition_json)
  nodes.value = normalizeNodes(definition)
  edges.value = (definition.edges || []).map((edge: Entity) => ({ source: edge.source, target: edge.target }))
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
  output.value = ''
}

function newWorkflow() {
  currentWorkflow.value = null
  workflowForm.name = '未命名协作工作流'
  workflowForm.description = ''
  nodes.value = [
    { id: 'input', type: 'input', label: '任务输入', config: {}, position: { x: 50, y: 210 } },
    { id: 'output', type: 'output', label: '结果输出', config: { value: { result: '{{input.task}}' } }, position: { x: 390, y: 210 } },
  ]
  edges.value = []
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
  output.value = ''
}

function addAgentAt(agent: Entity, point: { x: number; y: number }) {
  const id = `agent_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
  nodes.value.push({
    id,
    type: 'agent',
    label: agent.name,
    config: { agent_id: agent.id, input: '{{input.task}}' },
    position: {
      x: Math.max(8, Math.min(canvasWidth - nodeWidth - 8, point.x - nodeWidth / 2)),
      y: Math.max(8, Math.min(canvasHeight - nodeHeight - 8, point.y - nodeHeight / 2)),
    },
  })
  selectedNodeId.value = id
  selectedEdgeIndex.value = null
}

function stopPaletteTracking() {
  window.removeEventListener('pointermove', movePaletteDrag)
  window.removeEventListener('pointerup', finishPaletteDrag)
  window.removeEventListener('pointercancel', cancelPaletteDrag)
}

function startPalettePointer(event: PointerEvent, agent: Entity) {
  if (event.button !== 0) return
  paletteDrag.agent = agent
  paletteDrag.active = false
  paletteDrag.startX = paletteDrag.x = event.clientX
  paletteDrag.startY = paletteDrag.y = event.clientY
  window.addEventListener('pointermove', movePaletteDrag)
  window.addEventListener('pointerup', finishPaletteDrag)
  window.addEventListener('pointercancel', cancelPaletteDrag)
}

function movePaletteDrag(event: PointerEvent) {
  if (!paletteDrag.agent) return
  paletteDrag.x = event.clientX
  paletteDrag.y = event.clientY
  if (Math.hypot(event.clientX - paletteDrag.startX, event.clientY - paletteDrag.startY) > 4) paletteDrag.active = true
}

function finishPaletteDrag(event: PointerEvent) {
  const agent = paletteDrag.agent
  const rect = canvas.value?.getBoundingClientRect()
  if (agent && paletteDrag.active && rect && event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) {
    addAgentAt(agent, canvasPoint(event))
    store.notify(`已将“${agent.name}”加入画板`)
  }
  cancelPaletteDrag()
}

function cancelPaletteDrag() {
  stopPaletteTracking()
  paletteDrag.agent = null
  paletteDrag.active = false
}

function addAgentToCenter(agent: Entity) {
  const target = canvas.value
  if (!target) return
  addAgentAt(agent, {
    x: (target.scrollLeft + target.clientWidth / 2) / zoom.value,
    y: (target.scrollTop + target.clientHeight / 2) / zoom.value,
  })
  store.notify(`已将“${agent.name}”加入画板`)
}

function canvasPoint(event: { clientX: number; clientY: number }) {
  const rect = canvas.value?.getBoundingClientRect()
  return {
    x: Math.max(10, (event.clientX - (rect?.left || 0) + (canvas.value?.scrollLeft || 0)) / zoom.value),
    y: Math.max(10, (event.clientY - (rect?.top || 0) + (canvas.value?.scrollTop || 0)) / zoom.value),
  }
}

function dropAgent(event: DragEvent) {
  const agentId = event.dataTransfer?.getData('application/evoagent-agent')
  const agent = agents.value.find(item => item.id === agentId)
  if (!agent) return
  addAgentAt(agent, canvasPoint(event))
}

function startMove(node: CanvasNode, event: PointerEvent) {
  if ((event.target as HTMLElement).classList.contains('node-port')) return
  selectedNodeId.value = node.id
  selectedEdgeIndex.value = null
  const start = { x: event.clientX, y: event.clientY, nodeX: node.position.x, nodeY: node.position.y }
  const move = (next: PointerEvent) => {
    node.position.x = Math.max(8, Math.min(canvasWidth - nodeWidth - 8, start.nodeX + (next.clientX - start.x) / zoom.value))
    node.position.y = Math.max(8, Math.min(canvasHeight - nodeHeight - 8, start.nodeY + (next.clientY - start.y) / zoom.value))
  }
  const stop = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
}

function outputPoint(nodeId: string) {
  const node = nodes.value.find(item => item.id === nodeId)
  return node ? { x: node.position.x + nodeWidth, y: node.position.y + nodeHeight / 2 } : { x: 0, y: 0 }
}
function inputPoint(nodeId: string) {
  const node = nodes.value.find(item => item.id === nodeId)
  return node ? { x: node.position.x, y: node.position.y + nodeHeight / 2 } : { x: 0, y: 0 }
}
function curve(source: { x: number; y: number }, target: { x: number; y: number }) {
  const offset = Math.max(60, Math.abs(target.x - source.x) * .45)
  return `M ${source.x} ${source.y} C ${source.x + offset} ${source.y}, ${target.x - offset} ${target.y}, ${target.x} ${target.y}`
}
function edgePath(edge: CanvasEdge) { return curve(outputPoint(edge.source), inputPoint(edge.target)) }
function previewPath() { return curve(outputPoint(connectingFrom.value), pointer) }

function startConnection(nodeId: string, event: PointerEvent) {
  event.preventDefault()
  connectingFrom.value = nodeId
  Object.assign(pointer, canvasPoint(event))
}
function moveConnection(event: PointerEvent) {
  if (connectingFrom.value) Object.assign(pointer, canvasPoint(event))
}
function createsCycle(source: string, target: string) {
  const outgoing = new Map<string, string[]>()
  for (const edge of edges.value) outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge.target])
  const stack = [target], visited = new Set<string>()
  while (stack.length) {
    const current = stack.pop()!
    if (current === source) return true
    if (visited.has(current)) continue
    visited.add(current)
    stack.push(...(outgoing.get(current) || []))
  }
  return false
}
function finishConnection(target: string) {
  const source = connectingFrom.value
  connectingFrom.value = ''
  if (!source || source === target || target === 'input' || source === 'output') return
  if (edges.value.some(edge => edge.source === source && edge.target === target)) return
  if (createsCycle(source, target)) return store.notify('连接会形成循环，已阻止', 'error')
  edges.value.push({ source, target })
}
function cancelConnection() { connectingFrom.value = '' }
function selectEdge(index: number) {
  selectedEdgeIndex.value = index
  selectedNodeId.value = ''
}
function removeEdge(index: number) {
  edges.value.splice(index, 1)
  selectedEdgeIndex.value = null
}
function removeNode(nodeId: string) {
  const node = nodes.value.find(item => item.id === nodeId)
  if (!node || ['input', 'output'].includes(node.type)) return
  nodes.value = nodes.value.filter(item => item.id !== node.id)
  edges.value = edges.value.filter(edge => edge.source !== node.id && edge.target !== node.id)
  selectedNodeId.value = ''
  selectedEdgeIndex.value = null
}
function removeSelectedNode() { if (selectedNode.value) removeNode(selectedNode.value.id) }
function deleteSelection() {
  if (selectedEdgeIndex.value !== null) removeEdge(selectedEdgeIndex.value)
  else removeSelectedNode()
}
function selectNode(nodeId: string) {
  selectedNodeId.value = nodeId
  selectedEdgeIndex.value = null
}

async function setZoom(value: number) {
  const target = canvas.value
  const oldZoom = zoom.value
  const center = target ? {
    x: (target.scrollLeft + target.clientWidth / 2) / oldZoom,
    y: (target.scrollTop + target.clientHeight / 2) / oldZoom,
  } : null
  zoom.value = Math.max(.5, Math.min(1.8, Math.round(value * 10) / 10))
  await nextTick()
  if (target && center) {
    target.scrollLeft = center.x * zoom.value - target.clientWidth / 2
    target.scrollTop = center.y * zoom.value - target.clientHeight / 2
  }
}
function zoomBy(delta: number) { void setZoom(zoom.value + delta) }
function fitCanvas() {
  const target = canvas.value
  if (!target) return
  const maxX = Math.max(700, ...nodes.value.map(node => node.position.x + nodeWidth + 80))
  const maxY = Math.max(500, ...nodes.value.map(node => node.position.y + nodeHeight + 80))
  const fitted = Math.min(target.clientWidth / maxX, target.clientHeight / maxY, 1.3)
  void setZoom(fitted).then(() => { target.scrollLeft = 0; target.scrollTop = 0 })
}
function wheelZoom(event: WheelEvent) {
  if (!event.ctrlKey) return
  event.preventDefault()
  zoomBy(event.deltaY < 0 ? .1 : -.1)
}

function buildDefinition() {
  const preparedNodes = nodes.value.map(node => {
    const copy = JSON.parse(JSON.stringify(node))
    if (copy.type === 'agent') {
      const parents = edges.value.filter(edge => edge.target === copy.id).map(edge => edge.source)
      const upstream = parents.map(source => source === 'input' ? '原始任务：{{input.task}}' : `上游 ${nodes.value.find(item => item.id === source)?.label || source}：{{nodes.${source}.output}}`)
      copy.config.input = upstream.join('\n\n') || '{{input.task}}'
    }
    if (copy.type === 'output') {
      const parent = edges.value.find(edge => edge.target === copy.id)?.source
      copy.config.value = { result: parent && parent !== 'input' ? `{{nodes.${parent}.output}}` : '{{input.task}}' }
    }
    return copy
  })
  return { nodes: preparedNodes, edges: edges.value }
}

function validateWorkflow() {
  if (!workflowForm.name.trim()) return '请填写工作流名称'
  if (!nodes.value.some(node => node.type === 'agent')) return '请从左侧拖入至少一个 Agent'
  if (!edges.value.some(edge => edge.source === 'input')) return '任务输入节点尚未连接'
  if (!edges.value.some(edge => edge.target === 'output')) return '结果输出节点尚未连接'
  const isolated = nodes.value.filter(node => node.type === 'agent' && (!edges.value.some(edge => edge.target === node.id) || !edges.value.some(edge => edge.source === node.id)))
  if (isolated.length) return `Agent 节点“${isolated[0].label}”尚未完整连接`
  return ''
}

async function persistWorkflow(showNotice = true) {
  const error = validateWorkflow()
  if (error) { store.notify(error, 'error'); return null }
  const payload = { name: workflowForm.name, description: workflowForm.description, definition: buildDefinition() }
  const saved: Entity = currentWorkflow.value
    ? await api.put(`/workflows/${currentWorkflow.value.id}`, payload)
    : await api.post('/workflows', payload)
  currentWorkflow.value = saved
  workflows.value = await api.get('/workflows')
  if (showNotice) store.notify('可视化工作流已保存')
  return saved
}

async function saveWorkflow() {
  store.loading(true)
  try { await persistWorkflow(true) }
  catch (error: any) { store.notify(error.message, 'error') }
  finally { store.loading(false) }
}

async function runWorkflow() {
  if (workflowRunning.value) return
  workflowRunning.value = true
  workflowRunStatus.value = 'running'
  output.value = '正在保存画板并启动工作流，请稍候…'
  try {
    const saved = await persistWorkflow(false)
    if (!saved) { workflowRunStatus.value = 'idle'; output.value = ''; return }
    output.value = '工作流已提交，正在建立实时运行连接…'
    await nextTick()
    let run: Entity = {}
    let receivedResult = false
    let streamError = ''
    await api.stream(`/workflows/${saved.id}/run/stream`, { input: { task: task.value } }, event => {
      if (event.type === 'workflow_result') { run = event.run; receivedResult = true }
      else if (event.type === 'error') streamError = event.message || '工作流运行流异常'
      else if (event.type === 'step') {
        const step = event.step || {}
        if (step.type === 'stream_connected') output.value = '实时连接已建立，准备执行节点…'
        else if (step.type === 'workflow_run_started') output.value = '工作流已启动，正在计算节点执行顺序…'
        else if (step.type === 'workflow_node_started') output.value = `正在执行：${step.label || step.node_id}…`
        else if (step.type === 'workflow_node_completed') output.value = `已完成：${step.label || step.node_id}（${step.duration_ms || 0} ms）`
        else if (step.type === 'workflow_waiting') output.value = `Agent 仍在执行，已等待 ${step.elapsed_seconds || 0} 秒…`
        else if (step.type === 'workflow_run_failed') output.value = step.error || '工作流执行失败'
      }
    })
    if (streamError) throw new Error(streamError)
    if (!receivedResult) throw new Error('工作流运行结束但未返回结果')
    workflowRunStatus.value = run.status
    if (run.status === 'completed') {
      try { output.value = JSON.stringify(JSON.parse(run.output_json), null, 2) }
      catch { output.value = run.output_json || '工作流已完成' }
    } else output.value = run.error || '工作流执行失败，请查看最近运行记录。'
    store.notify(run.status === 'completed' ? '工作流执行完成' : '工作流失败', run.status === 'completed' ? 'success' : 'error')
    runs.value = await api.get('/workflow-runs')
  } catch (error: any) {
    workflowRunStatus.value = 'failed'
    output.value = error.message || '工作流请求失败'
    store.notify(output.value, 'error')
  } finally { workflowRunning.value = false }
}

function keyboardDelete(event: KeyboardEvent) {
  const target = event.target as HTMLElement
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)) return
  if (event.key === 'Delete' || event.key === 'Backspace') deleteSelection()
}

onMounted(() => { void load(); window.addEventListener('keydown', keyboardDelete) })
onBeforeUnmount(() => { stopPaletteTracking(); window.removeEventListener('keydown', keyboardDelete) })
</script>

<template>
  <PageHeader eyebrow="VISUAL ORCHESTRATION" title="协作工作流画板" description="从 Agent 工厂拖入节点，用鼠标连接端口形成可执行工作流。">
    <div class="page-actions"><button class="btn" @click="newWorkflow"><Plus :size="15" />新建画板</button><button class="btn btn-primary" @click="saveWorkflow"><Save :size="15" />保存工作流</button></div>
  </PageHeader>

  <section class="workflow-studio card">
    <aside class="workflow-palette">
      <div class="studio-pane-title"><Bot :size="16" /><span>Agent 工厂</span><small>{{ visibleAgents.length }}</small></div>
      <div class="palette-search"><Search :size="13" /><input v-model="search" placeholder="筛选 Agent"></div>
      <div class="palette-agents">
        <article v-for="agent in visibleAgents" :key="agent.id" class="palette-agent" title="拖入画板；双击可添加到画布中央" @pointerdown.prevent="startPalettePointer($event,agent)" @dblclick="addAgentToCenter(agent)">
          <GripVertical :size="15" /><div><strong>{{ agent.name }}</strong><span>{{ agent.description || '可编排智能体' }}</span></div>
        </article>
      </div>
      <div class="palette-workflows">
        <label>已保存工作流</label>
        <button v-for="item in workflows" :key="item.id" :class="{ active: currentWorkflow?.id===item.id }" @click="openWorkflow(item)"><Workflow :size="13" /><span>{{ item.name }}</span></button>
      </div>
    </aside>

    <div class="workflow-canvas-shell">
      <div class="canvas-toolbar">
        <div><strong>{{ workflowForm.name }}</strong><span>{{ nodes.length }} 节点 · {{ edges.length }} 连线</span></div>
        <div class="canvas-tools">
          <button title="缩小" @click="zoomBy(-.1)"><Minus :size="13" /></button><span>{{ zoomLabel }}</span><button title="放大" @click="zoomBy(.1)"><ZoomIn :size="13" /></button><button title="适应画布" @click="fitCanvas"><Maximize2 :size="13" /></button>
          <button class="danger" :disabled="!selectedNode && !selectedEdge" title="删除选中的节点或连线" @click="deleteSelection"><Trash2 :size="13" /></button>
        </div>
      </div>
      <div ref="canvas" class="workflow-canvas" @dragover.prevent @drop.prevent="dropAgent" @pointermove="moveConnection" @pointerup="cancelConnection" @wheel="wheelZoom" @click.self="selectedNodeId='';selectedEdgeIndex=null">
        <div class="workflow-canvas-stage" :style="{width:`${canvasWidth*zoom}px`,height:`${canvasHeight*zoom}px`}">
          <div class="workflow-canvas-content" :style="{width:`${canvasWidth}px`,height:`${canvasHeight}px`,transform:`scale(${zoom})`}" @click.self="selectedNodeId='';selectedEdgeIndex=null">
            <svg class="workflow-wires" :width="canvasWidth" :height="canvasHeight">
              <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#4f91cb" /></marker></defs>
              <path v-for="(edge,index) in edges" :key="`${edge.source}-${edge.target}`" :d="edgePath(edge)" class="workflow-wire" :class="{selected:selectedEdgeIndex===index}" marker-end="url(#workflow-arrow)" @click.stop="selectEdge(index)" @dblclick.stop="removeEdge(index)" />
              <path v-if="connectingFrom" :d="previewPath()" class="workflow-wire preview" />
            </svg>
            <article v-for="node in nodes" :key="node.id" class="workflow-node" :class="[node.type,{selected:selectedNodeId===node.id}]" :style="{left:`${node.position.x}px`,top:`${node.position.y}px`}" @pointerdown="startMove(node,$event)" @click.stop="selectNode(node.id)">
              <button v-if="node.type!=='input'" class="node-port input-port" title="输入端口" @pointerup.stop="finishConnection(node.id)" />
              <div class="node-icon"><Bot v-if="node.type==='agent'" :size="17" /><GitBranch v-else-if="node.type==='input'" :size="17" /><CircleStop v-else :size="17" /></div>
              <div class="node-copy"><small>{{ node.type.toUpperCase() }}</small><strong>{{ node.label }}</strong></div>
              <button v-if="node.type==='agent'" class="node-delete" title="删除此 Agent 节点" @pointerdown.stop @click.stop="removeNode(node.id)"><Trash2 :size="11" /></button>
              <button v-if="node.type!=='output'" class="node-port output-port" title="输出端口" @pointerdown.stop="startConnection(node.id,$event)" />
            </article>
          </div>
        </div>
      </div>
    </div>

    <aside class="workflow-inspector">
      <div class="studio-pane-title"><GitBranch :size="16" /><span>属性设置</span></div>
      <div class="inspector-body">
        <div class="field"><label>工作流名称</label><input v-model="workflowForm.name" class="input"></div>
        <div class="field"><label>工作流说明</label><textarea v-model="workflowForm.description" class="textarea inspector-textarea" /></div>
        <template v-if="selectedNode">
          <div class="inspector-divider" />
          <div class="field"><label>节点名称</label><input v-model="selectedNode.label" class="input"></div>
          <div class="field"><label>节点类型</label><input :value="selectedNode.type" class="input" disabled></div>
          <div v-if="selectedNode.type==='agent'" class="field"><label>绑定 Agent</label><select v-model="selectedNode.config.agent_id" class="select"><option v-for="agent in agents.filter(item=>item.status==='active')" :key="agent.id" :value="agent.id">{{ agent.name }}</option></select></div>
          <div v-if="selectedNode.type==='agent'" class="notice">节点输入会根据连线自动包含原始任务和上游 Agent 输出。</div>
          <button v-if="!['input','output'].includes(selectedNode.type)" class="btn btn-danger" @click="removeSelectedNode"><Trash2 :size="14" />删除节点</button>
        </template>
        <template v-else-if="selectedEdge">
          <div class="inspector-divider" />
          <div class="notice">已选择连线：{{ nodes.find(item=>item.id===selectedEdge?.source)?.label }} → {{ nodes.find(item=>item.id===selectedEdge?.target)?.label }}</div>
          <button class="btn btn-danger" @click="deleteSelection"><Trash2 :size="14" />删除连线关系</button>
        </template>
        <div v-else class="empty compact">选中画布节点后配置属性；单击连线可删除。</div>
      </div>
    </aside>
  </section>

  <section class="workflow-run-grid">
    <div class="card"><div class="card-header"><div><h2>运行当前工作流</h2><StatusBadge v-if="workflowRunStatus!=='idle'" :status="workflowRunStatus" /></div><button class="btn btn-primary" :disabled="workflowRunning" @click="runWorkflow"><Play :size="15" />{{ workflowRunning ? '运行中…' : '开始运行' }}</button></div><div class="card-body"><div class="field"><label>任务输入</label><textarea v-model="task" class="textarea" /></div><div v-if="output" class="field" style="margin-top:14px"><label>{{ workflowRunning ? '实时状态' : '最终输出（AI 生成内容）' }}</label><div class="result-box" :class="{running:workflowRunning}">{{ output }}</div></div></div></div>
    <aside class="card"><div class="card-header"><h3>最近运行</h3></div><div class="card-body list-stack"><div v-for="run in runs.slice(0,6)" :key="run.id" class="list-item"><div><strong>{{ run.duration_ms }} ms</strong><p>{{ new Date(run.created_at).toLocaleString('zh-CN') }}</p></div><StatusBadge :status="run.status" /></div><div v-if="!runs.length" class="empty">暂无运行</div></div></aside>
  </section>
  <div v-if="paletteDrag.active && paletteDrag.agent" class="palette-drag-ghost" :style="{left:`${paletteDrag.x+14}px`,top:`${paletteDrag.y+14}px`}"><Bot :size="14" />{{ paletteDrag.agent.name }}</div>
</template>
