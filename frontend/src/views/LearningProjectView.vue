<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft, Bot, BookOpenCheck, CheckCircle2, ClipboardCheck, ExternalLink,
  GitBranch, HeartHandshake, ListChecks, LoaderCircle, MessageCircleQuestion, NotebookPen, Play,
  Plus, RefreshCw, Route as RouteIcon, Save, Send, Target, Trash2, TriangleAlert, UserRound,
} from 'lucide-vue-next'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'
import LearningPathGraph from '../components/LearningPathGraph.vue'

const route = useRoute(), router = useRouter(), store = useAppStore()
const projectId = computed(() => String(route.params.projectId))
const section = computed(() => String(route.params.section || 'overview'))
const loading = ref(true), busy = ref('')
const data = ref<Entity>({ project: {}, nodes: [], tasks: [], turns: [], questions: [], attempts: [], mistakes: [], memories: [], assessments: [] })
const pack = ref<Entity>({ agents: [], workflows: [], knowledge_bases: [] })
const selectedNodeId = ref(''), tutorMode = ref('socratic'), tutorMessage = ref(''), chatBox = ref<HTMLElement | null>(null)
const answers = ref<Record<string, any>>({}), feedback = ref<Record<string, Entity>>({})
const memoryCategory = ref('note'), memoryText = ref('')
const workflowOutput = ref('')
const diagnostic = ref<Entity>({ dimensions: {}, gaps: [], recommended_actions: [] })
const personalizedPath = ref<Entity>({ nodes: [], edges: [], stages: [] })
const personalSpace = ref<Entity>({ direction_profile: {}, memory_summary: {} })
const preferences = reactive<Entity>({ explanation_depth:'step_by_step', mentor_style:'socratic', session_minutes:45, resource_format:'mixed' })
const companionForm = reactive({ minutes:45, mood:'normal', goal:'' })
const companionSession = ref<Entity | null>(null)

const tabs = [
  ['overview', '总览', Target], ['plan', '学习计划', ListChecks], ['path', '智能学习路径', RouteIcon],
  ['tutor', '知识问答与讲解', MessageCircleQuestion], ['companion', '学习陪伴', HeartHandshake],
  ['practice', '练习题库', BookOpenCheck], ['mistakes', '错题复习', TriangleAlert],
  ['memory', '笔记记忆', NotebookPen], ['profile', '个性化空间', UserRound], ['assessment', '学习评测', ClipboardCheck],
] as const
const moduleMap: Record<string,string> = { overview: 'planning', plan: 'planning', path: 'planning', tutor: 'tutor', companion:'tutor', practice: 'practice', mistakes: 'mistake', memory: 'tutor', profile:'planning', assessment: 'assessment' }
const currentModule = computed(() => moduleMap[section.value] || 'planning')
const currentAgentId = computed({
  get: () => data.value.project?.agent_bindings?.[currentModule.value] || '',
  set: value => { data.value.project.agent_bindings = { ...(data.value.project.agent_bindings || {}), [currentModule.value]: value }; void saveBindings() },
})
const currentWorkflowKey = computed(() => section.value === 'mistakes' ? 'mistake_review' : 'learning_loop')
const currentWorkflowId = computed({
  get: () => data.value.project?.workflow_bindings?.[currentWorkflowKey.value] || '',
  set: value => { data.value.project.workflow_bindings = { ...(data.value.project.workflow_bindings || {}), [currentWorkflowKey.value]: value }; void saveBindings() },
})
const currentAgent = computed(() => pack.value.agents?.find((item: Entity) => item.id === currentAgentId.value))
const nodesByDomain = computed<[string, Entity[]][]>(() => Object.entries((data.value.nodes || []).reduce((groups: Record<string,Entity[]>, node: Entity) => { (groups[node.domain] ||= []).push(node); return groups }, {})) as [string, Entity[]][])
const todayTasks = computed(() => (data.value.tasks || []).filter((item: Entity) => item.status !== 'completed').slice(0, 6))
const latestAssessment = computed(() => data.value.assessments?.[0])
const directionProfile = computed(() => data.value.project?.settings?.direction_profile || {})
const nodeName = (id: string) => data.value.nodes?.find((item: Entity) => item.id === id)?.title || '通用学习'
const questionForMistake = (mistake: Entity) => {
  const attempt = data.value.attempts?.find((item: Entity) => item.id === mistake.attempt_id)
  return data.value.questions?.find((item: Entity) => item.id === attempt?.question_id)
}
const moduleLabel = (value: string) => ({ learn: '学习', practice: '练习', review: '复习', assessment: '评测', project: '实践' } as Record<string,string>)[value] || value
const statusLabel = (value: string) => ({ pending: '待开始', in_progress: '进行中', completed: '已完成', skipped: '已跳过', not_started: '未开始', learning: '学习中', mastered: '已掌握', open: '待订正', reviewing: '复习中' } as Record<string,string>)[value] || value

async function load() {
  loading.value = true
  try {
    const [workspace, subjectPack, diagnosis, path, space] = await Promise.all([
      api.get<Entity>(`/learning-projects/${projectId.value}/workspace`),
      api.get<Entity>('/learning-subject-packs/computer-science'),
      api.get<Entity>(`/learning-projects/${projectId.value}/diagnostic`),
      api.get<Entity>(`/learning-projects/${projectId.value}/personalized-path`),
      api.get<Entity>(`/learning-projects/${projectId.value}/personal-space`),
    ])
    data.value = workspace
    pack.value = subjectPack
    diagnostic.value = diagnosis
    personalizedPath.value = path
    personalSpace.value = space
    Object.assign(preferences, space.preferences || {})
    selectedNodeId.value ||= workspace.nodes?.[0]?.id || ''
  } catch (error: any) {
    store.notify(error.message || '学习项目载入失败', 'error')
    if ([403, 404].includes(Number(error.status))) await router.replace('/learning')
  }
  finally { loading.value = false }
}
async function saveBindings() {
  try {
    await api.put(`/learning-projects/${projectId.value}/bindings`, { agents: data.value.project.agent_bindings || {}, workflows: data.value.project.workflow_bindings || {} })
    store.notify('模块配置已保存')
  } catch (error: any) { store.notify(error.message || '配置保存失败', 'error') }
}
async function generatePlan(regenerate = false) {
  busy.value = 'plan'
  try { await api.post(`/learning-projects/${projectId.value}/plan/generate`, { regenerate, focus: [] }); await load(); store.notify(regenerate ? '学习计划已重新生成' : '学习计划已生成') }
  catch (error: any) { store.notify(error.message || '计划生成失败', 'error') }
  finally { busy.value = '' }
}
async function replanPath() {
  if (busy.value) return
  busy.value='path'
  try {
    const result=await api.post<Entity>(`/learning-projects/${projectId.value}/personalized-path/replan`,{regenerate_plan:true,focus:diagnostic.value.gaps?.slice(0,3).map((item:Entity)=>item.code)||[]})
    personalizedPath.value=result.path;diagnostic.value=result.path.diagnostic;data.value.tasks=result.tasks
    store.notify('已按最新诊断重排学习路径和任务')
  } catch(error:any){store.notify(error.message||'路径重规划失败','error')} finally{busy.value=''}
}
function openPathNode(id:string){selectedNodeId.value=id;void router.push(`/learning/${projectId.value}/tutor`)}
async function startCompanion(){
  busy.value='companion'
  try{companionSession.value=await api.post<Entity>(`/learning-projects/${projectId.value}/companion/session`,companionForm)}
  catch(error:any){store.notify(error.message||'陪伴会话生成失败','error')}finally{busy.value=''}
}
async function savePreferences(){
  try{await api.patch(`/learning-projects/${projectId.value}`,{settings:{learning_preferences:{...preferences}}});personalSpace.value.preferences={...preferences};store.notify('个性化偏好已保存')}
  catch(error:any){store.notify(error.message||'偏好保存失败','error')}
}
async function rebuildDirection() {
  const accepted = window.confirm('将根据当前方向名称、描述、目标、水平与截止时间重新生成知识路径、计划、题库、辅导上下文和评测。已有作答与进度会清空，学习笔记会保留。是否继续？')
  if (!accepted || busy.value) return
  busy.value = 'direction'
  try {
    await api.post(`/learning-projects/${projectId.value}/direction/regenerate`, { keep_memories: true })
    selectedNodeId.value = ''
    answers.value = {}; feedback.value = {}; workflowOutput.value = ''
    await load()
    store.notify('当前方向的专属内容已全部重建')
  } catch (error: any) { store.notify(error.message || '方向内容重建失败', 'error') }
  finally { busy.value = '' }
}
async function updateTask(task: Entity, status: string) {
  try { const updated = await api.patch<Entity>(`/learning-projects/${projectId.value}/tasks/${task.id}`, { status }); Object.assign(task, updated); data.value.project.progress = Math.round(100 * data.value.tasks.filter((item: Entity) => item.status === 'completed').length / Math.max(1, data.value.tasks.length)) }
  catch (error: any) { store.notify(error.message || '任务更新失败', 'error') }
}
async function sendTutor() {
  if (!tutorMessage.value.trim() || busy.value) return
  const message = tutorMessage.value.trim(); tutorMessage.value = ''; busy.value = 'tutor'
  data.value.turns.push({ id: `local-${Date.now()}`, role: 'user', content: message, mode: tutorMode.value, knowledge_node_id: selectedNodeId.value })
  await nextTick(); chatBox.value?.scrollTo({ top: chatBox.value.scrollHeight })
  try {
    const reply = await api.post<Entity>(`/learning-projects/${projectId.value}/tutor`, { message, mode: tutorMode.value, knowledge_node_id: selectedNodeId.value || null, agent_id: currentAgentId.value || null })
    data.value.turns.push(reply)
    await nextTick(); chatBox.value?.scrollTo({ top: chatBox.value.scrollHeight, behavior: 'smooth' })
  } catch (error: any) { store.notify(error.message || '辅导 Agent 暂时无法回答', 'error') }
  finally { busy.value = '' }
}
async function submitAnswer(question: Entity) {
  busy.value = `question-${question.id}`
  try {
    const result = await api.post<Entity>(`/learning-projects/${projectId.value}/attempts`, { question_id: question.id, answer: answers.value[question.id] ?? '' })
    feedback.value[question.id] = result
    data.value.attempts.push(result.attempt)
    if (result.mistake) data.value.mistakes.push(result.mistake)
    const workspace = await api.get<Entity>(`/learning-projects/${projectId.value}/workspace`)
    data.value.nodes = workspace.nodes
  } catch (error: any) { store.notify(error.message || '提交失败', 'error') }
  finally { busy.value = '' }
}
async function reviewMistake(item: Entity) {
  try { const updated = await api.patch<Entity>(`/learning-projects/${projectId.value}/mistakes/${item.id}`, { status: item.status === 'open' ? 'reviewing' : item.status, correction: item.correction, reviewed: true }); Object.assign(item, updated); store.notify('本次复习已记录，并安排下一次间隔复习') }
  catch (error: any) { store.notify(error.message || '错题记录更新失败', 'error') }
}
async function addMemory() {
  if (memoryText.value.trim().length < 2) return
  try { const item = await api.post<Entity>(`/learning-projects/${projectId.value}/memories`, { category: memoryCategory.value, content: memoryText.value.trim(), source_type: 'user', confidence: 1 }); data.value.memories.push(item); memoryText.value = '' }
  catch (error: any) { store.notify(error.message || '笔记保存失败', 'error') }
}
async function deleteMemory(item: Entity) {
  try { await api.delete(`/learning-projects/${projectId.value}/memories/${item.id}`); data.value.memories = data.value.memories.filter((value: Entity) => value.id !== item.id) }
  catch (error: any) { store.notify(error.message || '笔记删除失败', 'error') }
}
async function assess() {
  busy.value = 'assessment'
  try { const item = await api.post<Entity>(`/learning-projects/${projectId.value}/assessments`, { period: 'current' }); data.value.assessments.unshift(item); store.notify('量化学习评测已更新') }
  catch (error: any) { store.notify(error.message || '评测生成失败', 'error') }
  finally { busy.value = '' }
}
async function runWorkflow() {
  if (!currentWorkflowId.value || busy.value) return
  busy.value = 'workflow'; workflowOutput.value = '工作流正在执行，请稍候…'
  try {
    const run = await api.post<Entity>(`/learning-projects/${projectId.value}/workflow/run?module=${currentWorkflowKey.value}`, { input: { task: `围绕“${data.value.project.name}”执行${tabs.find(item => item[0] === section.value)?.[1] || '学习'}任务` } })
    workflowOutput.value = run.status === 'completed' ? JSON.stringify(JSON.parse(run.output_json || '{}'), null, 2) : (run.error || `运行状态：${run.status}`)
  } catch (error: any) { workflowOutput.value = error.message || '工作流执行失败' }
  finally { busy.value = '' }
}
watch(projectId, load)
onMounted(load)
</script>

<template>
  <div v-if="loading" class="loading-page"><LoaderCircle :size="25" />正在载入学习空间…</div>
  <div v-else class="learning-project">
    <header class="project-header">
      <button class="back-button" title="返回学习空间" @click="router.push('/learning')"><ArrowLeft :size="17" /></button>
      <div><h1>{{ data.project.name }}</h1><p>{{ data.project.target || '尚未设置学习目标' }}</p><small class="direction-state">专属方向内容 · {{ directionProfile.focus_domains?.join(' / ') || data.project.settings?.track || '计算机科学' }} · 画像 {{ directionProfile.signature || '待重建' }}</small></div>
      <div class="header-metrics"><span><b>{{ data.project.progress || 0 }}%</b>任务进度</span><span><b>{{ data.project.mastery || 0 }}%</b>平均掌握度</span><span><b>{{ data.project.counts?.mistakes || 0 }}</b>错题记录</span></div>
      <button class="btn btn-sm rebuild-button" :disabled="busy==='direction'" @click="rebuildDirection"><RefreshCw :size="13" />{{ busy==='direction' ? '正在重建…' : '重建方向内容' }}</button>
    </header>

    <nav class="section-tabs">
      <button v-for="item in tabs" :key="item[0]" :class="{ active: section===item[0] }" @click="router.push(`/learning/${projectId}/${item[0]}`)"><component :is="item[2]" :size="14" />{{ item[1] }}</button>
    </nav>

    <section class="module-bar">
      <div class="agent-summary"><Bot :size="18" /><span><strong>{{ currentAgent?.name || '请选择模块 Agent' }}</strong><small>{{ currentAgent?.description || '学习模块与 Agent 工厂共用配置' }}</small></span></div>
      <label><span>本模块 Agent</span><select v-model="currentAgentId" class="select"><option value="">不指定</option><option v-for="agent in pack.agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option></select></label>
      <label><span>调用工作流</span><select v-model="currentWorkflowId" class="select"><option value="">不指定</option><option v-for="workflow in pack.workflows" :key="workflow.id" :value="workflow.id">{{ workflow.name }}</option></select></label>
      <button class="btn btn-sm" @click="router.push('/agents')"><ExternalLink :size="13" />Agent 工厂</button>
      <button class="btn btn-sm" @click="router.push('/workflows')"><GitBranch :size="13" />工作流</button>
      <button class="btn btn-primary btn-sm" :disabled="!currentWorkflowId || busy==='workflow'" @click="runWorkflow"><Play :size="13" />执行工作流</button>
      <pre v-if="workflowOutput" class="workflow-output">{{ workflowOutput }}</pre>
    </section>

    <main class="section-content">
      <template v-if="section==='overview'">
        <section class="overview-grid">
          <article class="panel direction-profile"><header><div><h2>当前方向画像</h2><p>由本方向信息生成，并用于约束知识、计划、练习、辅导与评测。</p></div></header><div><p><strong>重点领域</strong><span v-for="item in directionProfile.focus_domains || []" :key="item">{{ item }}</span></p><p><strong>识别关键词</strong><span v-for="item in directionProfile.keywords || []" :key="item">{{ item }}</span></p><p><strong>方向产出</strong><em v-for="item in directionProfile.target_outcomes || []" :key="item">{{ item }}</em></p><small>{{ directionProfile.learning_strategy || '重建方向内容后，将根据方向信息形成专属学习策略。' }}</small></div></article>
          <article class="panel wide"><header><div><h2>接下来要做</h2><p>按计划逐项完成，状态可以直接修改。</p></div><button v-if="!data.tasks.length" class="btn btn-primary btn-sm" @click="generatePlan(false)"><Plus :size="13" />生成计划</button></header><div v-if="todayTasks.length" class="task-list"><div v-for="task in todayTasks" :key="task.id"><button class="task-check" :class="{ done: task.status==='completed' }" @click="updateTask(task,task.status==='completed'?'pending':'completed')"><CheckCircle2 :size="17" /></button><span><strong>{{ task.title }}</strong><small>{{ moduleLabel(task.module) }} · {{ task.duration_minutes }} 分钟 · {{ task.scheduled_for ? new Date(task.scheduled_for).toLocaleDateString() : '未排期' }}</small></span><b>{{ statusLabel(task.status) }}</b></div></div><p v-else class="empty-line">尚未生成学习计划。</p></article>
          <article class="panel"><header><div><h2>薄弱知识</h2><p>优先处理低掌握度节点。</p></div></header><div class="weak-list"><button v-for="node in [...data.nodes].sort((a,b)=>a.mastery-b.mastery).slice(0,6)" :key="node.id" @click="selectedNodeId=node.id;router.push(`/learning/${projectId}/tutor`)"><span><strong>{{ node.title }}</strong><small>{{ node.domain }}</small></span><b>{{ node.mastery }}%</b></button></div></article>
          <article class="panel"><header><div><h2>底层能力</h2><p>当前方向已绑定的公共能力。</p></div></header><ul class="binding-list"><li><BookOpenCheck :size="14" /><span><strong>计算机学科知识库</strong><small>{{ data.project.knowledge_base_ids?.length || 0 }} 个知识库，回答带来源</small></span></li><li><Bot :size="14" /><span><strong>学习 Agent 组</strong><small>{{ Object.values(data.project.agent_bindings || {}).filter(Boolean).length }} 个角色已配置</small></span></li><li><GitBranch :size="14" /><span><strong>学习工作流</strong><small>{{ Object.values(data.project.workflow_bindings || {}).filter(Boolean).length }} 条闭环可调用</small></span></li></ul></article>
        </section>
      </template>

      <template v-else-if="section==='plan'">
        <section class="panel full-panel"><header><div><h2>学习计划</h2><p>根据先修关系、每周 {{ data.project.weekly_hours }} 小时和目标自动排期。</p></div><nav><button class="btn btn-sm" :disabled="busy==='plan'" @click="generatePlan(true)"><RefreshCw :size="13" />重新生成</button><button v-if="!data.tasks.length" class="btn btn-primary btn-sm" @click="generatePlan(false)">生成计划</button></nav></header><div v-if="data.tasks.length" class="plan-list"><article v-for="task in data.tasks" :key="task.id"><button class="task-check" :class="{ done:task.status==='completed' }" @click="updateTask(task,task.status==='completed'?'pending':'completed')"><CheckCircle2 :size="17" /></button><time>{{ task.scheduled_for ? new Date(task.scheduled_for).toLocaleDateString() : '待排期' }}</time><span><strong>{{ task.title }}</strong><small>{{ task.description }}</small></span><em>{{ task.duration_minutes }} 分钟</em><select :value="task.status" class="select compact" @change="updateTask(task,($event.target as HTMLSelectElement).value)"><option value="pending">待开始</option><option value="in_progress">进行中</option><option value="completed">已完成</option><option value="skipped">跳过</option></select></article></div><div v-else class="empty-state"><ListChecks :size="36" /><strong>还没有学习计划</strong><span>点击生成后会按每个知识节点安排学习、练习和复习。</span></div></section>
      </template>

      <template v-else-if="section==='path'">
        <section class="panel full-panel"><header><div><h2>动态个性化学习路径</h2><p>依据先修关系、实时掌握度、练习正确率、任务进度和错题订正情况动态规划。</p></div><button class="btn btn-primary btn-sm" :disabled="busy==='path'" @click="replanPath"><RefreshCw :size="13" />{{ busy==='path'?'正在重排…':'按诊断重排' }}</button></header><div class="diagnostic-strip"><strong>{{ diagnostic.overall_score }}</strong><span><b>{{ diagnostic.level }}阶段</b><small>{{ diagnostic.limitations }}</small></span><article v-for="(value,key) in diagnostic.dimensions" :key="key"><label>{{ ({knowledge_mastery:'知识掌握',practice_accuracy:'练习正确率',task_progress:'任务进度',mistake_correction:'错题订正',learning_engagement:'学习投入'} as Record<string,string>)[String(key)]||key }}</label><b>{{ value }}%</b></article></div><LearningPathGraph :path="personalizedPath" @select="openPathNode"/><div class="path-actions"><article v-for="item in diagnostic.recommended_actions||[]" :key="item"><CheckCircle2 :size="14"/><span>{{ item }}</span></article></div></section>
      </template>

      <template v-else-if="section==='tutor'">
        <section class="tutor-layout">
          <aside class="panel node-sidebar"><header><div><h2>选择知识点</h2><p>问题与记忆将归入该节点。</p></div></header><div><button v-for="node in data.nodes" :key="node.id" :class="{ active:selectedNodeId===node.id }" @click="selectedNodeId=node.id"><span><strong>{{ node.title }}</strong><small>{{ node.domain }}</small></span><b>{{ node.mastery }}%</b></button></div></aside>
          <article class="panel tutor-panel"><header><div><h2>{{ nodeName(selectedNodeId) }}</h2><p>可滚轮浏览对话；Agent 会先理解上下文，再逐步追问。</p></div><select v-model="tutorMode" class="select mode-select"><option value="socratic">苏格拉底追问</option><option value="explain">概念讲解</option><option value="debug">代码调试</option><option value="feynman">费曼复述</option><option value="examiner">口试检查</option></select></header><div ref="chatBox" class="chat-messages"><div v-if="!data.turns.length" class="chat-empty"><MessageCircleQuestion :size="34" /><strong>从一个具体问题开始</strong><span>例如：“为什么哈希表的查找是期望 O(1)？”</span></div><article v-for="turn in data.turns" :key="turn.id" :class="turn.role"><b>{{ turn.role==='user' ? '我' : (turn.metadata?.agent_name || currentAgent?.name || '辅导 Agent') }}</b><p>{{ turn.content }}</p><details v-if="turn.citations?.length"><summary>查看 {{ turn.citations.length }} 条来源</summary><div v-for="citation in turn.citations" :key="citation.id"><strong>{{ citation.title }}</strong><small>{{ citation.source }}</small><p>{{ citation.excerpt }}</p></div></details></article><article v-if="busy==='tutor'" class="assistant waiting"><LoaderCircle :size="15" />Agent 正在结合知识库分析…</article></div><footer><textarea v-model="tutorMessage" class="textarea" placeholder="描述你的理解、卡住的位置或代码现象…" @keydown.ctrl.enter.prevent="sendTutor"/><button class="btn btn-primary" :disabled="!tutorMessage.trim() || busy==='tutor'" @click="sendTutor"><Send :size="14" />发送</button></footer></article>
        </section>
      </template>

      <template v-else-if="section==='companion'">
        <section class="companion-layout">
          <article class="panel companion-config"><header><div><h2>导师 / 学伴陪伴</h2><p>根据当前诊断把一次学习拆成可完成的小闭环。</p></div></header><label>本次目标<textarea v-model="companionForm.goal" class="textarea" placeholder="留空则自动选择当前最薄弱知识点"/></label><label>可用时间<input v-model.number="companionForm.minutes" class="input" type="number" min="10" max="180"/> 分钟</label><label>当前状态<select v-model="companionForm.mood" class="select"><option value="focused">专注</option><option value="normal">正常</option><option value="tired">疲惫</option><option value="stressed">焦虑 / 压力较大</option></select></label><button class="btn btn-primary" :disabled="busy==='companion'" @click="startCompanion"><HeartHandshake :size="14"/>{{ busy==='companion'?'正在规划…':'开始本次陪伴' }}</button><small>陪伴 Agent 会使用当前方向、薄弱点、错题和学习偏好，不会输出与本方向无关的通用任务。</small></article>
          <article class="panel companion-session"><header><div><h2>本次学习闭环</h2><p>理解—练习—复盘，结束后可继续进入知识问答。</p></div></header><template v-if="companionSession"><div class="mentor-message"><HeartHandshake :size="20"/><span><strong>{{ companionSession.message }}</strong><small>目标：{{ companionSession.goal }}</small></span></div><ol><li v-for="step in companionSession.steps" :key="step.title"><b>{{ step.minutes }} 分钟</b><span><strong>{{ step.title }}</strong><small>{{ step.instruction }}</small></span></li></ol><blockquote>{{ companionSession.check_in_question }}</blockquote><button class="btn btn-sm" @click="router.push(`/learning/${projectId}/tutor`)">带着问题进入知识问答</button></template><div v-else class="empty-state"><HeartHandshake :size="36"/><strong>准备一次适合当前状态的学习</strong><span>设置时间和状态后，导师会生成可执行的陪伴步骤。</span></div></article>
        </section>
      </template>

      <template v-else-if="section==='practice'">
        <section class="panel full-panel"><header><div><h2>练习题库</h2><p>提交后立即量化评分；低于 80 分自动进入错题复习。</p></div></header><div class="question-list"><article v-for="(question,index) in data.questions" :key="question.id"><header><span>第 {{ index+1 }} 题 · {{ nodeName(question.knowledge_node_id) }}</span><b>难度 {{ question.difficulty }}/5</b></header><h3>{{ question.prompt }}</h3><div v-if="question.options?.length" class="options"><label v-for="option in question.options" :key="option"><input v-model="answers[question.id]" type="radio" :name="question.id" :value="option">{{ option }}</label></div><textarea v-else v-model="answers[question.id]" class="textarea" placeholder="输入你的答案、推导过程或代码"></textarea><footer><span>来源：{{ question.source_refs?.[0]?.source || '计算机学科包' }}</span><button class="btn btn-primary btn-sm" :disabled="answers[question.id]===undefined || busy===`question-${question.id}`" @click="submitAnswer(question)">提交批改</button></footer><section v-if="feedback[question.id]" class="answer-feedback" :class="{ correct: feedback[question.id].attempt.is_correct }"><strong>{{ feedback[question.id].attempt.score }} / 100</strong><p>{{ feedback[question.id].attempt.feedback }}</p><small v-if="feedback[question.id].mistake">已进入错题复习，并安排间隔复习时间。</small></section></article></div></section>
      </template>

      <template v-else-if="section==='mistakes'">
        <section class="panel full-panel"><header><div><h2>错题复习</h2><p>记录真实错因和正确思路；每次复习后自动安排下一次时间。</p></div></header><div v-if="data.mistakes.length" class="mistake-list"><article v-for="item in data.mistakes" :key="item.id"><header><span><b>{{ nodeName(item.knowledge_node_id) }}</b><small>{{ statusLabel(item.status) }} · 已复习 {{ item.review_count }} 次</small></span><time>下次：{{ item.next_review_at ? new Date(item.next_review_at).toLocaleDateString() : '待安排' }}</time></header><p>{{ questionForMistake(item)?.prompt || '练习题记录' }}</p><div><label>系统诊断<textarea :value="item.cause" class="textarea" readonly /></label><label>我的订正<textarea v-model="item.correction" class="textarea" placeholder="用自己的话写出正确思路和容易混淆的边界" /></label></div><footer><span>连续完成 3 次复习后可标记为已掌握。</span><button class="btn btn-primary btn-sm" @click="reviewMistake(item)"><CheckCircle2 :size="13" />完成本次复习</button></footer></article></div><div v-else class="empty-state"><CheckCircle2 :size="36" /><strong>暂时没有错题</strong><span>练习得分低于 80 分时会自动建立复习记录。</span></div></section>
      </template>

      <template v-else-if="section==='memory'">
        <section class="memory-layout"><article class="panel memory-editor"><header><div><h2>添加学习笔记</h2><p>记录概念、方法、疑问或容易混淆的地方。</p></div></header><label>笔记类型<select v-model="memoryCategory" class="select"><option value="note">普通笔记</option><option value="concept">概念</option><option value="method">方法</option><option value="misconception">易错点</option><option value="question">待解决问题</option><option value="preference">学习偏好</option></select></label><label>内容<textarea v-model="memoryText" class="textarea" placeholder="写下可在后续辅导、练习和评测中复用的信息"></textarea></label><button class="btn btn-primary" :disabled="memoryText.trim().length<2" @click="addMemory"><Plus :size="14" />保存笔记</button></article><article class="panel"><header><div><h2>学习记忆</h2><p>{{ data.memories.length }} 条，可由后续 Agent 复用。</p></div></header><div v-if="data.memories.length" class="memory-list"><article v-for="item in data.memories" :key="item.id"><span><b>{{ item.category }}</b><small>{{ new Date(item.created_at).toLocaleString() }}</small></span><p>{{ item.content }}</p><button title="删除" @click="deleteMemory(item)"><Trash2 :size="14" /></button></article></div><p v-else class="empty-line">尚未记录学习记忆。</p></article></section>
      </template>

      <template v-else-if="section==='profile'">
        <section class="profile-layout">
          <article class="panel profile-summary"><header><div><h2>我的方向画像</h2><p>只反映当前学习方向，每个新方向都独立诊断和更新。</p></div></header><div class="profile-score"><strong>{{ diagnostic.overall_score }}</strong><span><b>{{ diagnostic.level }}阶段</b><small>画像签名 {{ personalSpace.direction_profile?.signature || '待生成' }}</small></span></div><div class="dimension-cards"><article v-for="(value,key) in diagnostic.dimensions" :key="key"><span>{{ key }}</span><b>{{ value }}%</b><i><em :style="{width:`${value}%`}"/></i></article></div><section><h3>当前知识缺口</h3><p v-for="gap in diagnostic.gaps||[]" :key="gap.id"><b>{{ gap.title }}</b><span>{{ gap.domain }} · 掌握度 {{ gap.mastery }}%</span></p></section></article>
          <article class="panel preference-panel"><header><div><h2>个性化偏好</h2><p>后续路径、讲解和陪伴 Agent 将读取这些设置。</p></div></header><label>讲解深度<select v-model="preferences.explanation_depth" class="select"><option value="concise">先结论后依据</option><option value="step_by_step">逐步推导</option><option value="deep">深入理论与边界</option></select></label><label>导师风格<select v-model="preferences.mentor_style" class="select"><option value="socratic">苏格拉底追问</option><option value="direct">直接讲解</option><option value="companion">同伴鼓励</option></select></label><label>默认学习时长<input v-model.number="preferences.session_minutes" type="number" min="10" max="180" class="input"/></label><label>资源形式<select v-model="preferences.resource_format" class="select"><option value="mixed">图文与练习混合</option><option value="reading">优先阅读</option><option value="practice">优先实践</option><option value="visual">优先可视化</option></select></label><button class="btn btn-primary" @click="savePreferences"><Save :size="14"/>保存偏好</button><div class="memory-summary"><b>{{ personalSpace.memory_summary?.total||0 }}</b><span>条学习记忆，其中 {{ personalSpace.memory_summary?.locked||0 }} 条已锁定</span></div></article>
        </section>
      </template>

      <template v-else-if="section==='assessment'">
        <section class="panel full-panel assessment-panel"><header><div><h2>量化学习评测</h2><p>指标均由当前任务、作答、知识节点和错题记录计算，可重复生成。</p></div><button class="btn btn-primary btn-sm" :disabled="busy==='assessment'" @click="assess"><RefreshCw :size="13" />生成最新评测</button></header><template v-if="latestAssessment"><div class="score-summary"><strong>{{ latestAssessment.overall_score }}</strong><span><b>综合学习指数</b><small>满分 100，由五项指标加权</small></span></div><div class="metric-list"><article v-for="(value,key) in latestAssessment.metrics" :key="key"><span>{{ ({task_completion:'任务完成度',practice_accuracy:'练习正确率',knowledge_mastery:'知识掌握度',mistake_correction:'错题订正率',knowledge_coverage:'知识覆盖度'} as Record<string,string>)[key] || key }}</span><template v-if="typeof value==='number' && !['attempt_count','completed_tasks','total_tasks'].includes(String(key))"><b>{{ value }}%</b><i><em :style="{width:`${value}%`} "/></i></template><b v-else>{{ value }}</b></article></div><section class="report-copy"><h3>评测结论</h3><p>{{ latestAssessment.summary }}</p><h3>下一步建议</h3><ol><li v-for="item in latestAssessment.recommendations" :key="item">{{ item }}</li></ol><small>生成时间：{{ new Date(latestAssessment.created_at).toLocaleString() }} · 评测不替代教师或考试机构的正式评价。</small></section></template><div v-else class="empty-state"><ClipboardCheck :size="36" /><strong>尚未生成学习评测</strong><span>完成部分任务或练习后生成，结果会更有参考价值。</span></div></section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.loading-page{min-height:420px;display:grid;place-items:center;align-content:center;gap:8px;color:#698497}.loading-page svg,.waiting svg{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.learning-project{display:grid;gap:12px}.project-header{min-height:76px;padding:13px 16px;display:flex;align-items:center;gap:12px;border:1px solid #d6e2eb;border-radius:11px;background:#fff}.back-button{width:34px;height:34px;border:1px solid #d3e0e9;border-radius:7px;display:grid;place-items:center;color:#315d7c;background:#fff}.project-header>div:nth-child(2){min-width:0;flex:1}.project-header h1{margin:0;color:#203f56;font-size:17px}.project-header p{margin:5px 0 0;overflow:hidden;color:#718593;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.header-metrics{display:flex;gap:8px}.header-metrics span{min-width:78px;padding:8px 10px;border-left:1px solid #e2e9ee;display:grid;color:#7b8d9a;font-size:8px}.header-metrics b{color:#245273;font-size:15px}.section-tabs{padding:4px;display:flex;gap:2px;overflow-x:auto;border:1px solid #d8e3eb;border-radius:9px;background:#fff}.section-tabs button{min-width:max-content;padding:8px 11px;border:0;border-radius:6px;display:flex;align-items:center;gap:5px;color:#60798b;background:transparent;font-size:9px}.section-tabs button.active{color:#1769c2;background:#eaf3fa;font-weight:700}.module-bar{padding:9px;display:grid;grid-template-columns:minmax(210px,1.3fr) minmax(150px,.8fr) minmax(160px,.9fr) auto auto auto;gap:8px;align-items:end;border:1px solid #d7e3eb;border-radius:9px;background:#fff}.agent-summary{display:flex;align-items:center;gap:8px;min-width:0;color:#1769c2}.agent-summary span{min-width:0}.agent-summary strong,.agent-summary small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.agent-summary strong{color:#2d5069;font-size:10px}.agent-summary small{margin-top:2px;color:#7a8e9c;font-size:8px}.module-bar label{display:grid;gap:3px}.module-bar label>span{color:#718595;font-size:7px}.module-bar .select{height:31px;padding:5px 7px;font-size:8px}.workflow-output{grid-column:1/-1;max-height:135px;margin:0;padding:9px;overflow:auto;border:1px solid #dbe5ec;border-radius:7px;color:#446278;background:#f7f9fb;font-size:8px;white-space:pre-wrap}.section-content{min-height:500px}.panel{min-width:0;border:1px solid #d8e3eb;border-radius:10px;background:#fff}.panel>header{min-height:58px;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:10px;border-bottom:1px solid #e5ecf1}.panel>header h2{margin:0;color:#294a62;font-size:12px}.panel>header p{margin:4px 0 0;color:#7a8e9c;font-size:8px}.panel>header nav{display:flex;gap:6px}.overview-grid{display:grid;grid-template-columns:1.45fr .8fr;gap:12px}.overview-grid .wide{grid-row:span 2}.task-list>div{min-height:52px;padding:8px 13px;display:flex;align-items:center;gap:9px;border-bottom:1px solid #edf1f4}.task-list>div:last-child{border:0}.task-check{padding:0;border:0;color:#9ab0be;background:transparent}.task-check.done{color:#15805f}.task-list span{min-width:0;flex:1}.task-list strong,.task-list small{display:block}.task-list strong{color:#37576d;font-size:9px}.task-list small{margin-top:3px;color:#82939f;font-size:7px}.task-list>div>b{color:#59778b;font-size:8px}.empty-line{padding:28px;text-align:center;color:#8799a5;font-size:9px}.weak-list{padding:7px}.weak-list button,.node-sidebar>div>button{width:100%;padding:8px;border:0;border-radius:6px;display:flex;align-items:center;gap:7px;text-align:left;background:#fff}.weak-list button:hover,.node-sidebar>div>button:hover,.node-sidebar>div>button.active{background:#edf5fa}.weak-list span,.node-sidebar span{min-width:0;flex:1}.weak-list strong,.weak-list small,.node-sidebar strong,.node-sidebar small{display:block}.weak-list strong,.node-sidebar strong{color:#395b72;font-size:9px}.weak-list small,.node-sidebar small{margin-top:2px;color:#8797a3;font-size:7px}.weak-list b,.node-sidebar b{color:#1769c2;font-size:8px}.binding-list{padding:8px 14px;margin:0;list-style:none}.binding-list li{padding:8px 0;display:flex;align-items:center;gap:8px;color:#1769c2;border-bottom:1px solid #edf1f4}.binding-list li:last-child{border:0}.binding-list strong,.binding-list small{display:block}.binding-list strong{color:#395b72;font-size:9px}.binding-list small{margin-top:2px;color:#8294a0;font-size:7px}.full-panel{min-height:500px}.plan-list{padding:6px 13px}.plan-list article{min-height:57px;display:grid;grid-template-columns:24px 82px minmax(240px,1fr) 60px 110px;align-items:center;gap:8px;border-bottom:1px solid #e9eff3}.plan-list time,.plan-list em{color:#748a99;font-size:8px;font-style:normal}.plan-list span{min-width:0}.plan-list strong,.plan-list small{display:block}.plan-list strong{color:#34566e;font-size:9px}.plan-list small{margin-top:3px;overflow:hidden;color:#83949f;font-size:7px;text-overflow:ellipsis;white-space:nowrap}.compact{height:29px;padding:4px;font-size:8px}.empty-state{min-height:360px;display:grid;place-items:center;align-content:center;gap:7px;color:#7f9aaa}.empty-state strong{color:#46677d}.empty-state span{font-size:9px}.domain-list{padding:13px;display:grid;gap:15px}.domain-list>section>h3{margin:0 0 7px;color:#315972;font-size:10px}.domain-list>section>div{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.domain-list button{padding:11px;border:1px solid #dde6ec;border-radius:8px;text-align:left;background:#fff}.domain-list button:hover{border-color:#75a9cc;background:#f9fcfe}.domain-list button header,.domain-list button footer{display:flex;align-items:center;justify-content:space-between}.domain-list button strong{color:#34566e;font-size:9px}.domain-list button b{color:#1769c2;font-size:9px}.domain-list button p{height:34px;margin:7px 0;color:#6f8493;font-size:8px;line-height:1.5}.domain-list button small{display:block;color:#8a98a3;font-size:7px}.domain-list button footer{margin-top:8px;padding-top:7px;border-top:1px solid #edf1f4}.domain-list button footer span{color:#168061;font-size:7px}.domain-list button footer em{max-width:150px;overflow:hidden;color:#7e8f9b;font-size:7px;font-style:normal;text-overflow:ellipsis;white-space:nowrap}.tutor-layout{height:calc(100vh - 285px);min-height:510px;display:grid;grid-template-columns:235px minmax(0,1fr);gap:12px}.node-sidebar{min-height:0;display:flex;flex-direction:column}.node-sidebar>div{min-height:0;padding:6px;overflow-y:auto}.tutor-panel{min-height:0;display:flex;flex-direction:column}.mode-select{width:145px}.chat-messages{min-height:0;flex:1;padding:12px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;background:#f7f9fb}.chat-messages>article{max-width:82%;margin-bottom:10px;padding:9px 11px;border:1px solid #dbe5ec;border-radius:9px;background:#fff}.chat-messages>article.user{margin-left:auto;border-color:#b9d7ea;background:#edf7fd}.chat-messages b{color:#315872;font-size:8px}.chat-messages p{margin:5px 0 0;color:#425f73;font-size:9px;line-height:1.65;white-space:pre-wrap}.chat-messages details{margin-top:8px;border-top:1px solid #e2e9ee}.chat-messages summary{padding-top:7px;color:#1769c2;font-size:8px;cursor:pointer}.chat-messages details>div{padding:7px 0;border-bottom:1px dashed #e1e8ed}.chat-messages details strong,.chat-messages details small{display:block}.chat-messages details small{margin-top:2px;color:#788b98;font-size:7px}.chat-messages details p{font-size:7px}.chat-empty{height:100%;display:grid;place-items:center;align-content:center;gap:6px;color:#82a0b2}.chat-empty strong{color:#47697d}.chat-empty span{font-size:8px}.waiting{display:flex;align-items:center;gap:7px;color:#55758b;font-size:8px}.tutor-panel>footer{padding:9px;display:grid;grid-template-columns:1fr auto;gap:8px;border-top:1px solid #dce5ec}.tutor-panel>footer .textarea{min-height:58px;max-height:100px;resize:vertical}.question-list{padding:13px;display:grid;grid-template-columns:1fr 1fr;gap:10px}.question-list>article{padding:12px;border:1px solid #dce5eb;border-radius:8px}.question-list>article>header,.question-list>article>footer{display:flex;align-items:center;justify-content:space-between;color:#748997;font-size:8px}.question-list h3{min-height:38px;margin:9px 0;color:#324f64;font-size:10px;line-height:1.55}.options{display:grid;gap:5px}.options label{padding:7px;display:flex;align-items:center;gap:7px;border:1px solid #e0e7ec;border-radius:6px;color:#526d80;font-size:8px}.options input{accent-color:#1769c2}.question-list>article>footer{margin-top:10px;padding-top:8px;border-top:1px solid #e6edf1}.answer-feedback{margin-top:8px;padding:8px;border-radius:6px;color:#9b4f35;background:#fff1eb}.answer-feedback.correct{color:#1a7659;background:#eaf7f1}.answer-feedback strong{font-size:11px}.answer-feedback p{margin:4px 0;font-size:8px}.answer-feedback small{font-size:7px}.mistake-list{padding:12px;display:grid;gap:9px}.mistake-list>article{padding:12px;border:1px solid #e1ded8;border-radius:8px;background:#fff}.mistake-list header,.mistake-list footer{display:flex;align-items:center;justify-content:space-between}.mistake-list header span{display:grid}.mistake-list header b{color:#5a4b42;font-size:10px}.mistake-list header small,.mistake-list time,.mistake-list footer{color:#8b817b;font-size:7px}.mistake-list>article>p{color:#4d6271;font-size:9px}.mistake-list>article>div{display:grid;grid-template-columns:1fr 1fr;gap:8px}.mistake-list label,.memory-editor label{display:grid;gap:5px;color:#647d8e;font-size:8px}.mistake-list .textarea{min-height:64px}.mistake-list footer{margin-top:8px}.memory-layout{display:grid;grid-template-columns:340px minmax(0,1fr);gap:12px}.memory-editor{padding-bottom:13px}.memory-editor>label,.memory-editor>button{margin:11px 13px 0}.memory-editor .textarea{min-height:150px}.memory-list{padding:8px}.memory-list article{padding:9px;display:grid;grid-template-columns:90px minmax(0,1fr) 25px;align-items:start;gap:8px;border-bottom:1px solid #e9eff3}.memory-list span{display:grid}.memory-list b{color:#1769c2;font-size:8px}.memory-list small{margin-top:3px;color:#8a98a2;font-size:7px}.memory-list p{margin:0;color:#456176;font-size:9px;line-height:1.55;white-space:pre-wrap}.memory-list button{padding:3px;border:0;color:#a35656;background:transparent}.score-summary{padding:22px;display:flex;justify-content:center;align-items:center;gap:14px;border-bottom:1px solid #e4ebf0}.score-summary>strong{font-size:42px;color:#1769c2}.score-summary span{display:grid}.score-summary b{color:#34576e}.score-summary small{margin-top:4px;color:#7e909c;font-size:8px}.metric-list{padding:13px;display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.metric-list article{padding:10px;border:1px solid #e0e8ed;border-radius:7px;display:grid;gap:6px}.metric-list span{color:#718695;font-size:8px}.metric-list b{color:#315b77;font-size:14px}.metric-list i{height:5px;border-radius:4px;background:#edf2f5}.metric-list em{display:block;height:100%;border-radius:4px;background:#2c7ab8}.report-copy{margin:0 13px 13px;padding:13px;border:1px solid #e0e7ec;border-radius:8px;background:#fafbfc}.report-copy h3{margin:0 0 6px;color:#3b5b70;font-size:10px}.report-copy p,.report-copy li{color:#586f80;font-size:9px;line-height:1.6}.report-copy small{color:#85959f;font-size:7px}@media(max-width:1200px){.module-bar{grid-template-columns:1fr 1fr 1fr}.agent-summary{grid-column:1/-1}.domain-list>section>div{grid-template-columns:1fr 1fr}.overview-grid{grid-template-columns:1fr}.overview-grid .wide{grid-row:auto}.metric-list{grid-template-columns:repeat(3,1fr)}}@media(max-width:800px){.header-metrics{display:none}.module-bar{grid-template-columns:1fr}.agent-summary{grid-column:auto}.tutor-layout,.memory-layout{height:auto;grid-template-columns:1fr}.node-sidebar>div{max-height:220px}.tutor-panel{height:600px}.question-list{grid-template-columns:1fr}.domain-list>section>div{grid-template-columns:1fr}.plan-list article{grid-template-columns:24px 70px 1fr}.plan-list em,.plan-list select{display:none}.mistake-list>article>div{grid-template-columns:1fr}.metric-list{grid-template-columns:1fr 1fr}}
.direction-state{display:block;margin-top:4px;color:#54778f;font-size:7px}.rebuild-button{white-space:nowrap}.direction-profile{grid-column:1/-1}.direction-profile>div{padding:11px 14px}.direction-profile p{margin:0 0 7px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}.direction-profile strong{min-width:62px;color:#365a72;font-size:8px}.direction-profile span{padding:4px 7px;border:1px solid #d7e5ee;border-radius:5px;color:#3d6580;background:#f7fafc;font-size:8px}.direction-profile em{color:#526e81;font-size:8px;font-style:normal}.direction-profile>div>small{display:block;padding-top:7px;border-top:1px solid #e8eef2;color:#748895;font-size:8px}
.diagnostic-strip{padding:11px 14px;display:grid;grid-template-columns:70px minmax(220px,1fr) repeat(5,minmax(75px,.45fr));align-items:center;gap:9px;border-bottom:1px solid #e5ecf1;background:#fafcfd}.diagnostic-strip>strong{color:#1769c2;font-size:28px}.diagnostic-strip>span b,.diagnostic-strip>span small{display:block}.diagnostic-strip>span b{color:#34576e;font-size:10px}.diagnostic-strip>span small{margin-top:3px;color:#7a8e9c;font-size:7px;line-height:1.4}.diagnostic-strip article{padding-left:8px;border-left:1px solid #dfe7ec;display:grid;gap:3px}.diagnostic-strip label{color:#768a98;font-size:7px}.diagnostic-strip article b{color:#315b77;font-size:11px}.path-actions{padding:0 12px 12px;display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.path-actions article{padding:9px;display:flex;gap:7px;border:1px solid #dce5eb;border-radius:7px;color:#48677b;font-size:8px}.path-actions svg{flex:0 0 auto;color:#16805f}.companion-layout,.profile-layout{display:grid;grid-template-columns:330px minmax(0,1fr);gap:12px}.companion-config,.preference-panel{padding-bottom:13px}.companion-config>label,.preference-panel>label{margin:10px 13px 0;display:grid;gap:4px;color:#60798a;font-size:8px}.companion-config>button,.preference-panel>button{margin:11px 13px 0}.companion-config>small{margin:10px 13px 0;display:block;color:#7a8d99;font-size:7px;line-height:1.5}.mentor-message{margin:13px;padding:12px;display:flex;gap:9px;color:#1769c2;border-left:3px solid #1769c2;background:#f3f8fc}.mentor-message span{min-width:0}.mentor-message strong,.mentor-message small{display:block}.mentor-message strong{color:#34576e;font-size:10px}.mentor-message small{margin-top:4px;color:#6f8492;font-size:8px}.companion-session ol{margin:13px;padding:0;list-style:none;display:grid;gap:8px}.companion-session li{padding:10px;display:flex;align-items:center;gap:10px;border:1px solid #dce5eb;border-radius:8px}.companion-session li>b{min-width:58px;color:#1769c2;font-size:10px}.companion-session li strong,.companion-session li small{display:block}.companion-session li strong{color:#34576e;font-size:9px}.companion-session li small{margin-top:4px;color:#6d8391;font-size:8px;line-height:1.5}.companion-session blockquote{margin:13px;padding:11px;border-left:3px solid #16805f;color:#45677a;background:#f4faf7;font-size:9px}.companion-session>button{margin:0 13px 13px}.profile-summary{padding-bottom:13px}.profile-score{padding:18px;display:flex;align-items:center;justify-content:center;gap:12px;border-bottom:1px solid #e5ecf1}.profile-score>strong{color:#1769c2;font-size:40px}.profile-score span{display:grid}.profile-score b{color:#34576e;font-size:11px}.profile-score small{margin-top:4px;color:#7b8d99;font-size:7px}.dimension-cards{padding:12px;display:grid;grid-template-columns:repeat(5,1fr);gap:7px}.dimension-cards article{padding:8px;border:1px solid #dfe7ec;border-radius:7px;display:grid;gap:5px}.dimension-cards span{color:#748997;font-size:7px}.dimension-cards b{color:#315b77;font-size:12px}.dimension-cards i{height:4px;background:#edf2f5}.dimension-cards em{height:100%;display:block;background:#1769c2}.profile-summary>section{margin:0 12px;padding:10px;border:1px solid #e0e7ec;border-radius:8px}.profile-summary h3{margin:0 0 7px;color:#3c5d72;font-size:9px}.profile-summary section p{margin:5px 0;display:flex;justify-content:space-between;color:#526e80;font-size:8px}.profile-summary section p span{color:#7c8d98}.memory-summary{margin:12px 13px 0;padding:11px;display:flex;align-items:center;gap:8px;border:1px solid #dce5eb;border-radius:7px;background:#fafcfd}.memory-summary b{color:#1769c2;font-size:20px}.memory-summary span{color:#667e8e;font-size:8px}@media(max-width:1150px){.diagnostic-strip{grid-template-columns:70px 1fr repeat(3,80px)}.diagnostic-strip article:nth-last-child(-n+2){display:none}.path-actions{grid-template-columns:1fr}.dimension-cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:800px){.companion-layout,.profile-layout{grid-template-columns:1fr}.diagnostic-strip{grid-template-columns:60px 1fr}.diagnostic-strip article{display:none}.dimension-cards{grid-template-columns:1fr 1fr}}
</style>
