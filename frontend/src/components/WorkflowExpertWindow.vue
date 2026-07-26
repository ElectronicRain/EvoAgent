<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Bot, Check, GitBranch, LoaderCircle, Send, Sparkles, WandSparkles, X,
} from 'lucide-vue-next'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const props = defineProps<{
  open: boolean
  definition: Entity
  workflowName: string
  workflowDescription: string
}>()
const emit = defineEmits<{
  close: []
  apply: [proposal: Entity]
}>()

const store = useAppStore()
const input = ref('')
const thinking = ref(false)
const materializing = ref(false)
const proposal = ref<Entity | null>(null)
const messages = ref<Entity[]>([])
const proposedDefinition = computed(() => proposal.value?.definition || props.definition)

watch(() => props.open, value => {
  if (value) {
    proposal.value = null
    if (!messages.value.length) {
      messages.value = [{
        role: 'assistant',
        content: '告诉我你希望工作流完成什么任务。我会选择真实可用的 Agent 与知识库，编排节点、变量、条件分支、循环和产出文档；之后你可以继续对话修改，也可以应用后在画布上手动调整。',
      }]
    }
  }
})

async function askExpert() {
  const content = input.value.trim()
  if (!content || thinking.value) return
  messages.value.push({ role: 'user', content })
  input.value = ''
  thinking.value = true
  try {
    const result = await api.post<Entity>('/workflow-expert/chat', {
      message: content,
      history: messages.value.slice(0, -1).slice(-20).map(item => ({
        role: item.role,
        content: item.content,
      })),
      current_definition: proposedDefinition.value,
      current_agent_drafts: proposal.value?.agent_drafts || [],
      workflow_name: proposal.value?.name || props.workflowName,
      workflow_description: proposal.value?.description || props.workflowDescription,
    })
    proposal.value = result
    messages.value.push({
      role: 'assistant',
      content: result.reply,
      changes: result.change_summary || [],
      snapshot: result.resource_snapshot || {},
    })
  } catch (error: any) {
    store.notify(error.message, 'error')
    messages.value.push({ role: 'assistant', content: `编排失败：${error.message}` })
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
    emit('apply', result)
    const names = (result.created_agents || []).map((item: Entity) => item.name)
    if (names.length) {
      messages.value.push({
        role: 'assistant',
        content: `已创建 ${names.length} 个候选 Agent：${names.join('、')}。完整配置已保存到 Agent 工厂，并已绑定到工作流节点。`,
      })
    }
    store.notify(names.length
      ? `已创建 ${names.length} 个候选 Agent，并应用完整分支到画布`
      : '专家草案已应用到画布，所有节点和参数仍可手动调整')
  } catch (error: any) {
    store.notify(error.message, 'error')
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
              <span>对话生成 · 动态调整 · 保持手动可编辑</span>
            </div>
            <button title="关闭" @click="emit('close')"><X :size="17" /></button>
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
                {{ materializing ? '正在创建 Agent 与分支…' : '创建 Agent 并应用到画布' }}
              </button>
            </aside>
          </div>

          <footer>
            <textarea
              v-model="input"
              placeholder="例如：让调查 Agent 先检索教育知识库，再由审核 Agent 复核；循环 3 轮，每轮生成文档…"
              @keydown.ctrl.enter.prevent="askExpert"
            />
            <div>
              <span>Ctrl + Enter 发送 · 可继续要求“增加分支”“换 Agent”“循环 5 次”</span>
              <button :disabled="thinking || !input.trim()" @click="askExpert">
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
</style>
