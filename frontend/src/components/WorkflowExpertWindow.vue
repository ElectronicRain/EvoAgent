<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Bot, Check, CircleHelp, GitBranch, LoaderCircle, RotateCw, Send, Sparkles,
  WandSparkles, X,
} from 'lucide-vue-next'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

type ClarificationOption = { value: string; label: string; description?: string }
type ClarificationQuestion = {
  id: string
  label: string
  question: string
  type: 'single_choice' | 'number' | 'text'
  required: boolean
  default?: string | number
  options?: ClarificationOption[]
  placeholder?: string
  min?: number
  max?: number
  suffix?: string
}

const props = defineProps<{
  open: boolean
  sessionKey: string
  definition: Entity
  workflowName: string
  workflowDescription: string
}>()
const emit = defineEmits<{
  close: []
  apply: [proposal: Entity]
}>()

const store = useAppStore()
const STORAGE_KEY = 'evoagent-workflow-expert-sessions-v2'
const input = ref('')
const thinking = ref(false)
const materializing = ref(false)
const proposal = ref<Entity | null>(null)
const messages = ref<Entity[]>([])
const clarification = ref<Entity | null>(null)
const clarificationAnswers = ref<Record<string, any>>({})
const pendingObjective = ref('')
const hasClarifiedObjective = ref(false)
let restoring = false
const proposedDefinition = computed(() => proposal.value?.definition || props.definition)
const isBlankCanvas = computed(() => {
  const meaningful = (props.definition?.nodes || []).filter(
    (node: Entity) => !['input', 'output'].includes(String(node.type || '')),
  )
  return meaningful.length === 0 && !(props.definition?.edges || []).length
})
const clarificationComplete = computed(() => {
  const questions = (clarification.value?.questions || []) as ClarificationQuestion[]
  return questions.every(question => {
    const value = clarificationAnswers.value[question.id]
    if (question.required && (value === undefined || value === null || String(value).trim() === '')) return false
    if (question.type !== 'number' || value === '' || value === undefined || value === null) return true
    const number = Number(value)
    return Number.isFinite(number)
      && (question.min === undefined || number >= question.min)
      && (question.max === undefined || number <= question.max)
  })
})

function greeting() {
  return {
    role: 'assistant',
    content: '这是当前画板的独立编排会话。先告诉我这条工作流要完成的任务；如果目标、范围或交付标准不明确，我会先向你确认，再创建可执行的 Agent、知识库、分支、变量和产出链路。',
  }
}

function readSessions(): Record<string, Entity> {
  try { return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}') }
  catch { return {} }
}

function persistSession(key = props.sessionKey) {
  if (restoring || !key) return
  const sessions = readSessions()
  sessions[key] = {
    input: input.value,
    proposal: proposal.value,
    messages: messages.value,
    clarification: clarification.value,
    clarificationAnswers: clarificationAnswers.value,
    pendingObjective: pendingObjective.value,
    hasClarifiedObjective: hasClarifiedObjective.value,
    updatedAt: Date.now(),
  }
  const newest = Object.entries(sessions)
    .sort((left, right) => Number(right[1]?.updatedAt || 0) - Number(left[1]?.updatedAt || 0))
    .slice(0, 30)
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(newest)))
}

function restoreSession(key: string) {
  restoring = true
  const saved = readSessions()[key]
  input.value = saved?.input || ''
  proposal.value = saved?.proposal || null
  messages.value = saved?.messages?.length ? saved.messages : [greeting()]
  clarification.value = saved?.clarification || null
  clarificationAnswers.value = saved?.clarificationAnswers || {}
  pendingObjective.value = saved?.pendingObjective || ''
  hasClarifiedObjective.value = Boolean(saved?.hasClarifiedObjective)
  restoring = false
}

function resetSession() {
  const sessions = readSessions()
  delete sessions[props.sessionKey]
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
  input.value = ''
  proposal.value = null
  messages.value = [greeting()]
  clarification.value = null
  clarificationAnswers.value = {}
  pendingObjective.value = ''
  hasClarifiedObjective.value = false
  persistSession()
}

watch(() => props.sessionKey, (next, previous) => {
  if (previous) persistSession(previous)
  restoreSession(next)
}, { immediate: true })
watch(
  [input, proposal, messages, clarification, clarificationAnswers, pendingObjective, hasClarifiedObjective],
  () => persistSession(),
  { deep: true },
)
watch(() => props.open, open => { if (open) restoreSession(props.sessionKey); else persistSession() })

async function generateProposal(objective: string) {
  thinking.value = true
  try {
    const result = await api.post<Entity>('/workflow-expert/chat', {
      message: objective,
      history: messages.value.slice(-20).map(item => ({
        role: item.role,
        content: item.content,
      })),
      current_definition: proposedDefinition.value,
      current_agent_drafts: proposal.value?.agent_drafts || [],
      workflow_name: proposal.value?.name || props.workflowName,
      workflow_description: proposal.value?.description || props.workflowDescription,
    })
    result.objective = objective
    proposal.value = result
    messages.value.push({
      role: 'assistant',
      content: result.reply,
      changes: result.change_summary || [],
      snapshot: result.resource_snapshot || {},
    })
  } catch (error: any) {
    const message = error.message || '编排服务暂时不可用'
    store.notify(message, 'error')
    messages.value.push({ role: 'assistant', content: `编排失败：${message}` })
  } finally {
    thinking.value = false
  }
}

async function askExpert() {
  const content = input.value.trim()
  if (!content || thinking.value) return
  messages.value.push({ role: 'user', content })
  input.value = ''
  pendingObjective.value = content
  if (isBlankCanvas.value && !proposal.value && !hasClarifiedObjective.value) {
    thinking.value = true
    try {
      const result = await api.post<Entity>('/workflow-clarification', {
        task: content,
        workflow_name: props.workflowName,
        workflow_description: props.workflowDescription,
        definition: props.definition,
        phase: 'orchestration',
      })
      if (result.required && result.questions?.length) {
        clarification.value = result
        clarificationAnswers.value = Object.fromEntries(
          result.questions.map((question: ClarificationQuestion) => [question.id, question.default ?? '']),
        )
        messages.value.push({
          role: 'assistant',
          content: `${result.summary} 请先确认下面这些会改变工作流结构与执行方式的要求。`,
          intent: result.intent,
        })
        return
      }
      hasClarifiedObjective.value = true
      await generateProposal(result.resolved_task || content)
    } catch (error: any) {
      const message = error.message || '需求分析失败'
      store.notify(message, 'error')
      messages.value.push({ role: 'assistant', content: `需求分析失败：${message}` })
    } finally {
      thinking.value = false
    }
    return
  }
  await generateProposal(content)
}

async function confirmClarification() {
  if (!clarificationComplete.value || thinking.value) return
  thinking.value = true
  try {
    const result = await api.post<Entity>('/workflow-clarification', {
      task: pendingObjective.value,
      workflow_name: props.workflowName,
      workflow_description: props.workflowDescription,
      definition: props.definition,
      answers: clarificationAnswers.value,
      confirmed: true,
      phase: 'orchestration',
    })
    const requirementSummary = (result.requirements || [])
      .map((item: Entity) => `${item.label}：${item.value}`).join('；')
    messages.value.push({ role: 'user', content: `确认编排要求：${requirementSummary}` })
    clarification.value = null
    clarificationAnswers.value = {}
    hasClarifiedObjective.value = true
    await generateProposal(result.resolved_task || pendingObjective.value)
  } catch (error: any) {
    store.notify(error.message || '需求确认失败', 'error')
  } finally {
    thinking.value = false
  }
}

async function applyProposal() {
  if (!proposal.value || materializing.value) return
  materializing.value = true
  try {
    const result = await api.post<Entity>('/workflow-expert/materialize', {
      proposal: proposal.value,
    })
    proposal.value = result
    const names = (result.created_agents || []).map((item: Entity) => item.name)
    messages.value.push({
      role: 'assistant',
      content: names.length
        ? `已创建并在线绑定 ${names.length} 个候选 Agent：${names.join('、')}。草案已通过后端可执行性检查，正在应用并保存画板。`
        : '草案已通过后端可执行性检查，正在应用并保存画板。',
    })
    emit('apply', result)
  } catch (error: any) {
    store.notify(error.message || '草案无法应用', 'error')
  } finally {
    materializing.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="workflow-expert">
      <div v-if="open" class="workflow-expert-layer">
        <button class="workflow-expert-backdrop" aria-label="关闭编排专家" @click="emit('close')" />
        <section class="workflow-expert-window">
          <header>
            <span class="expert-avatar"><WandSparkles :size="19" /></span>
            <div>
              <small>WORKFLOW ORCHESTRATION COPILOT</small>
              <strong>工作流智能编排专家</strong>
              <span>{{ workflowName || '新画板' }} · 独立会话 · 手动可编辑</span>
            </div>
            <button class="reset-session" title="清空当前画板的编排对话" @click="resetSession"><RotateCw :size="15" /></button>
            <button class="close-session" title="关闭" @click="emit('close')"><X :size="17" /></button>
          </header>

          <div class="expert-body">
            <main class="expert-conversation">
              <div class="expert-capabilities">
                <span><GitBranch :size="12" />条件分支</span>
                <span><Bot :size="12" />Agent 协作</span>
                <span><Sparkles :size="12" />变量与循环</span>
                <span><Check :size="12" />可执行校验</span>
              </div>
              <article v-for="(message,index) in messages" :key="index" :class="message.role">
                <strong>{{ message.role === 'user' ? '你' : '编排专家' }}</strong>
                <p>{{ message.content }}</p>
                <ul v-if="message.changes?.length">
                  <li v-for="item in message.changes" :key="item">{{ item }}</li>
                </ul>
                <small v-if="message.snapshot?.model_endpoint">
                  {{ message.snapshot.model_endpoint }} · {{ message.snapshot.agent_count }} Agent ·
                  {{ message.snapshot.knowledge_base_count }} 知识库
                </small>
              </article>
              <section v-if="clarification" class="expert-clarification">
                <header>
                  <CircleHelp :size="17" />
                  <div>
                    <strong>先明确任务，再生成画板</strong>
                    <small>{{ clarification.task_type_label }} · {{ clarification.questions?.length || 0 }} 项关键决策</small>
                  </div>
                </header>
                <div v-for="(question,index) in clarification.questions || []" :key="question.id" class="expert-question">
                  <label><b>{{ index + 1 }}</b><span><strong>{{ question.label }}</strong>{{ question.question }}</span></label>
                  <div v-if="question.type === 'single_choice'" class="expert-options">
                    <button
                      v-for="option in question.options || []"
                      :key="option.value"
                      :class="{ active: clarificationAnswers[question.id] === option.value }"
                      @click="clarificationAnswers[question.id] = option.value"
                    >
                      <Check v-if="clarificationAnswers[question.id] === option.value" :size="11" />
                      <span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span>
                    </button>
                  </div>
                  <label v-else-if="question.type === 'number'" class="expert-number">
                    <input v-model.number="clarificationAnswers[question.id]" type="number" :min="question.min" :max="question.max" />
                    <b>{{ question.suffix }}</b><small>{{ question.min }}–{{ question.max }}</small>
                  </label>
                  <textarea
                    v-else
                    v-model="clarificationAnswers[question.id]"
                    :placeholder="question.placeholder"
                  />
                </div>
                <button class="confirm-intent" :disabled="!clarificationComplete || thinking" @click="confirmClarification">
                  <LoaderCircle v-if="thinking" :size="13" class="spin" /><Check v-else :size="13" />
                  确认需求并生成工作流
                </button>
              </section>
              <article v-if="thinking" class="assistant thinking">
                <strong>编排专家</strong>
                <p><LoaderCircle :size="13" />正在分析目标、资源和可执行路径…</p>
              </article>
            </main>

            <aside class="expert-preview">
              <header><span>当前草案</span><b>{{ proposedDefinition.nodes?.length || 0 }} 节点</b></header>
              <div class="expert-node-list">
                <div v-for="node in proposedDefinition.nodes || []" :key="node.id">
                  <span>{{ String(node.type || '').toUpperCase() }}</span>
                  <strong>{{ node.label }}</strong>
                </div>
              </div>
              <div class="expert-preview-stats">
                <span>{{ proposedDefinition.edges?.length || 0 }} 连线</span>
                <span>{{ proposedDefinition.variables?.length || 0 }} 变量</span>
                <span v-if="proposal?.agent_drafts?.length" class="new-agent-stat">
                  将创建 {{ proposal.agent_drafts.length }} 个 Agent
                </span>
                <span v-if="(proposedDefinition.nodes || []).some((node: Entity) => node.type === 'condition')">
                  {{ (proposedDefinition.nodes || []).filter((node: Entity) => node.type === 'condition').length }} 个条件分支
                </span>
                <span v-if="proposedDefinition.execution?.loop_enabled">
                  循环 {{ proposedDefinition.execution.loop_count }} 次
                </span>
                <span v-if="proposal?.validation?.executable" class="validated-stat">已通过执行校验</span>
              </div>
              <div v-if="proposal?.agent_drafts?.length" class="expert-agent-drafts">
                <small>新 Agent 完整配置</small>
                <div v-for="draft in proposal.agent_drafts" :key="draft.key">
                  <Bot :size="13" />
                  <span><strong>{{ draft.name }}</strong>{{ draft.description }}</span>
                </div>
              </div>
              <button :disabled="!proposal || thinking || materializing" @click="applyProposal">
                <LoaderCircle v-if="materializing" :size="14" class="spin" />
                <Check v-else :size="14" />
                {{ materializing ? '正在创建并校验…' : '校验、应用并保存画板' }}
              </button>
            </aside>
          </div>

          <footer>
            <textarea
              v-model="input"
              :disabled="Boolean(clarification)"
              placeholder="例如：让调查 Agent 先检索教育知识库，再由审核 Agent 复核；循环 3 轮，每轮生成文档…"
              @keydown.ctrl.enter.prevent="askExpert"
            />
            <div>
              <span>{{ clarification ? '请先完成上方需求确认' : 'Ctrl + Enter 发送 · 可继续要求“增加分支”“换 Agent”“循环 5 次”' }}</span>
              <button :disabled="thinking || Boolean(clarification) || !input.trim()" @click="askExpert">
                <LoaderCircle v-if="thinking" :size="14" /><Send v-else :size="14" />
                {{ thinking ? '编排中' : '发送' }}
              </button>
            </div>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.workflow-expert-layer{position:fixed;inset:0;z-index:5200;display:grid;place-items:center;padding:22px}.workflow-expert-backdrop{position:absolute;inset:0;border:0;background:rgba(7,28,46,.5);backdrop-filter:blur(6px)}.workflow-expert-window{position:relative;width:min(1180px,calc(100vw - 44px));height:min(790px,calc(100vh - 44px));display:grid;grid-template-rows:68px minmax(0,1fr) 132px;overflow:hidden;border:1px solid #9ec5df;border-radius:17px;background:#fff;box-shadow:0 30px 90px rgba(8,35,57,.38)}.workflow-expert-window>header{padding:0 17px;display:flex;align-items:center;gap:11px;border-bottom:1px solid #d8e6ef;background:linear-gradient(120deg,#f8fcff,#eaf7fc)}.expert-avatar{width:40px;height:40px;display:grid;place-items:center;border-radius:12px;color:#fff;background:linear-gradient(135deg,#1769c2,#1aa295);box-shadow:0 7px 18px rgba(23,105,194,.22)}.workflow-expert-window>header>div{display:grid;grid-template-columns:auto auto;align-items:end;gap:2px 8px}.workflow-expert-window>header small{grid-column:1/-1;color:#6b899f;font-size:8px;letter-spacing:1.2px}.workflow-expert-window>header strong{color:#173f60;font-size:15px}.workflow-expert-window>header span{color:#7890a2;font-size:9px}.workflow-expert-window>header>button{width:32px;height:32px;margin-left:auto;border:1px solid #cbdde8;border-radius:8px;display:grid;place-items:center;color:#6a8397;background:#fff}.expert-body{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 300px}.expert-conversation{min-height:0;padding:16px;display:flex;flex-direction:column;gap:11px;overflow:auto;background:#f6f9fb}.expert-capabilities{display:flex;flex-wrap:wrap;gap:6px}.expert-capabilities span{padding:5px 8px;display:flex;align-items:center;gap:4px;border:1px solid #c9dfe9;border-radius:99px;color:#53778d;background:#fff;font-size:8px}.expert-conversation article{max-width:82%;padding:10px 12px;border:1px solid #d5e3ec;border-radius:4px 12px 12px;background:#fff;color:#31536c;box-shadow:0 4px 12px rgba(23,61,88,.05)}.expert-conversation article.user{align-self:flex-end;border-color:#1769c2;border-radius:12px 4px 12px 12px;color:#fff;background:#1769c2}.expert-conversation article strong{display:block;margin-bottom:5px;font-size:9px}.expert-conversation article p{margin:0;font-size:11px;line-height:1.65;white-space:pre-wrap}.expert-conversation article ul{margin:8px 0 0;padding-left:18px;font-size:9px;line-height:1.6}.expert-conversation article small{display:block;margin-top:8px;color:#7792a5;font-size:8px}.expert-conversation article.user small{color:#d6ebff}.expert-conversation article.thinking p{display:flex;align-items:center;gap:6px}.expert-conversation article.thinking svg{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.expert-preview{min-height:0;padding:13px;display:flex;flex-direction:column;border-left:1px solid #d7e4ec;background:linear-gradient(180deg,#f8fbfd,#eef5f8)}.expert-preview>header{display:flex;align-items:center;justify-content:space-between;color:#315a75;font-size:10px}.expert-preview>header b{padding:3px 6px;border-radius:99px;color:#1769c2;background:#e2f1fb;font-size:8px}.expert-node-list{min-height:0;margin:10px 0;display:flex;flex-direction:column;gap:5px;overflow:auto}.expert-node-list>div{padding:7px 8px;display:grid;grid-template-columns:62px minmax(0,1fr);gap:7px;border:1px solid #d6e3eb;border-radius:7px;background:#fff}.expert-node-list span{color:#7892a4;font-size:7px;letter-spacing:.5px}.expert-node-list strong{overflow:hidden;color:#315873;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.expert-preview-stats{display:flex;flex-wrap:wrap;gap:5px}.expert-preview-stats span{padding:4px 6px;border-radius:5px;color:#57758a;background:#e5edf2;font-size:8px}.expert-preview>button{height:34px;margin-top:10px;border:0;border-radius:8px;display:flex;align-items:center;justify-content:center;gap:6px;color:#fff;background:linear-gradient(135deg,#1769c2,#168c83);font-size:9px;font-weight:750;box-shadow:0 6px 15px rgba(23,105,194,.2)}.expert-preview>button:disabled{opacity:.45}.workflow-expert-window>footer{padding:12px 16px;border-top:1px solid #d7e5ed;background:#fff}.workflow-expert-window>footer textarea{width:100%;height:64px;box-sizing:border-box;padding:9px 10px;border:1px solid #c7d9e5;border-radius:9px;resize:none;color:#294e67;font:10px/1.55 inherit;outline:none}.workflow-expert-window>footer textarea:focus{border-color:#5d9dca;box-shadow:0 0 0 3px rgba(52,137,197,.11)}.workflow-expert-window>footer>div{margin-top:7px;display:flex;align-items:center;justify-content:space-between;gap:8px}.workflow-expert-window>footer span{color:#8093a1;font-size:8px}.workflow-expert-window>footer button{height:30px;padding:0 11px;border:0;border-radius:7px;display:flex;align-items:center;gap:5px;color:#fff;background:#1769c2;font-size:9px;font-weight:700}.workflow-expert-enter-active,.workflow-expert-leave-active{transition:.18s ease}.workflow-expert-enter-from,.workflow-expert-leave-to{opacity:0}.workflow-expert-enter-from .workflow-expert-window,.workflow-expert-leave-to .workflow-expert-window{transform:translateY(12px) scale(.985)}@media(max-width:760px){.workflow-expert-layer{padding:0}.workflow-expert-window{width:100vw;height:100vh;border:0;border-radius:0}.expert-body{grid-template-columns:1fr}.expert-preview{display:none}}
.expert-preview-stats .new-agent-stat{color:#08796f;background:#d8f4ee}.expert-agent-drafts{max-height:132px;margin-top:8px;padding:8px;overflow:auto;border:1px solid #c8dedf;border-radius:8px;background:#f5fbfa}.expert-agent-drafts>small{display:block;margin-bottom:6px;color:#16877d;font-size:8px;font-weight:800}.expert-agent-drafts>div{display:grid;grid-template-columns:16px minmax(0,1fr);gap:5px;padding:5px 0;color:#31736e}.expert-agent-drafts>div+div{border-top:1px solid #d9ebe8}.expert-agent-drafts span{min-width:0;display:grid;gap:2px;color:#718b8a;font-size:7px}.expert-agent-drafts strong{overflow:hidden;color:#245d59;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.spin{animation:spin 1s linear infinite}
.workflow-expert-window>header>.reset-session{margin-left:auto}.workflow-expert-window>header>.close-session{margin-left:0}.expert-clarification{width:min(640px,94%);align-self:center;padding:14px;border:1px solid #a8d4e6;border-radius:14px;background:linear-gradient(145deg,#fff,#f2faff);box-shadow:0 10px 30px rgba(20,91,133,.1)}.expert-clarification>header{display:flex;align-items:center;gap:9px;padding-bottom:10px;border-bottom:1px solid #dbeaf1;color:#147da6}.expert-clarification>header>div{display:grid;gap:2px}.expert-clarification>header strong{font-size:12px;color:#17445f}.expert-clarification>header small{font-size:8px;color:#6e899a}.expert-question{display:grid;gap:8px;padding:11px 0;border-bottom:1px dashed #dbe7ed}.expert-question>label:first-child{display:flex;align-items:flex-start;gap:8px;color:#4c6575;font-size:9px}.expert-question>label:first-child>b{width:19px;height:19px;display:grid;place-items:center;flex:0 0 19px;border-radius:6px;color:#fff;background:#1683b8;font-size:8px}.expert-question>label:first-child>span{display:grid;gap:2px}.expert-question>label:first-child strong{font-size:10px;color:#254d66}.expert-options{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.expert-options button{min-height:48px;padding:7px;border:1px solid #d4e3eb;border-radius:8px;display:flex;align-items:flex-start;gap:5px;text-align:left;color:#456477;background:#fff}.expert-options button.active{border-color:#1683b8;background:#eaf7fd;box-shadow:0 0 0 2px rgba(22,131,184,.08)}.expert-options button>svg{flex:0 0 auto;margin-top:1px;color:#0b8f81}.expert-options button>span{display:grid;gap:2px}.expert-options button strong{font-size:9px}.expert-options button small{font-size:7px;line-height:1.4;color:#79909f}.expert-question>textarea{min-height:54px;padding:8px;border:1px solid #cedfe8;border-radius:8px;resize:vertical;font:9px/1.5 inherit;color:#31566c}.expert-number{display:flex;align-items:center;gap:7px}.expert-number input{width:110px;padding:7px 8px;border:1px solid #cedfe8;border-radius:8px}.expert-number b{font-size:9px;color:#365e75}.expert-number small{font-size:8px;color:#8aa0ae}.confirm-intent{width:100%;height:34px;margin-top:11px;border:0;border-radius:8px;display:flex;align-items:center;justify-content:center;gap:6px;color:#fff;background:linear-gradient(135deg,#147dc0,#0b9587);font-size:9px;font-weight:800}.confirm-intent:disabled{opacity:.45}.expert-preview-stats .validated-stat{color:#08796f;background:#d8f4ee}.workflow-expert-window>footer textarea:disabled{color:#8ba0ad;background:#f2f5f7}@media(max-width:760px){.expert-options{grid-template-columns:1fr}.expert-clarification{width:auto}}
</style>
