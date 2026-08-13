<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { BookOpen, FlaskConical, Lightbulb, Plus, Search, Users, FileCode2, ScanSearch, UserPlus } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import FloatingPanel from '../components/FloatingPanel.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()
const projects = ref<Entity[]>([])
const search = ref('')
const showCreate = ref(false)
const showJoin = ref(false), joinCode = ref('')
const creating = ref(false)
const form = reactive({ name: '', discipline: '计算机科学', description: '', research_question: '', expected_outcome: '学术论文', citation_style: 'GB/T 7714', language: 'zh-CN' })
const canCreate = computed(() => form.name.trim().length >= 2 && form.discipline.trim().length > 0)

function openCreate() {
  Object.assign(form, { name: '', discipline: '计算机科学', description: '', research_question: '', expected_outcome: '学术论文', citation_style: 'GB/T 7714', language: 'zh-CN' })
  showCreate.value = true
}

async function load() { projects.value = await api.get('/research-projects') }
async function createProject() {
  if (!canCreate.value || creating.value) {
    if (!canCreate.value) store.notify('请填写至少 2 个字的科研方向名称和所属学科', 'error')
    return
  }
  creating.value = true
  try {
    const project = await api.post<Entity>('/research-projects', {
      ...form,
      name: form.name.trim(),
      discipline: form.discipline.trim(),
      description: form.description.trim(),
      research_question: form.research_question.trim(),
      expected_outcome: form.expected_outcome.trim() || '学术论文',
    })
    showCreate.value = false
    projects.value.unshift(project)
    store.notify('新科研方向已创建')
    await router.push(`/research/${project.id}`)
  } catch (error: any) { store.notify(error.message || '创建科研方向失败', 'error') }
  finally { creating.value = false }
}
async function joinProject(){try{const project=await api.post<Entity>('/research-projects/join',{code:joinCode.value.trim()});showJoin.value=false;joinCode.value='';await router.push(`/research/${project.id}`)}catch(error:any){store.notify(error.message||'加入项目失败','error')}}
function visible(project: Entity) { return !search.value || `${project.name}${project.discipline}${project.description}`.toLowerCase().includes(search.value.toLowerCase()) }
function stageLabel(stage: string) { return ({ literature: '文献研究', idea: 'Idea 探索', experiment: '实验', writing: '论文写作', review: '模拟审稿' } as Record<string,string>)[stage] || stage }
onMounted(load)
</script>

<template>
  <div class="research-home">
    <PageHeader eyebrow="RESEARCH APPLICATION" title="科研空间" description="以科研项目为主线，串联文献、Idea、记忆、实验、LaTeX 协作写作与模拟审稿。">
      <template #actions><button class="btn" @click="showJoin=true"><UserPlus :size="15"/>使用邀请码加入</button><button class="btn btn-primary" @click="openCreate"><Plus :size="15" />新建科研方向</button></template>
    </PageHeader>
    <section class="research-hero">
      <div><span>科研应用层</span><h2>从问题到论文，全过程可协作、可追溯</h2><p>项目成员共享经过确认的文献证据、研究决策和实验资产；论文采用 LaTeX 源码、版本防冲突和行级批注。</p></div>
      <div class="research-hero-flow"><BookOpen/><i/><Lightbulb/><i/><FlaskConical/><i/><FileCode2/><i/><ScanSearch/></div>
    </section>
    <div class="research-toolbar"><Search :size="15"/><input v-model="search" placeholder="搜索课题、学科或研究方向"></div>
    <section v-if="projects.length" class="project-grid">
      <article v-for="project in projects.filter(visible)" :key="project.id" class="project-card" @click="router.push(`/research/${project.id}`)">
        <header><span>{{ project.discipline }}</span><b>{{ stageLabel(project.stage) }}</b></header>
        <h3>{{ project.name }}</h3><p>{{ project.research_question || project.description || '尚未填写研究问题' }}</p>
        <div class="project-metrics"><span><BookOpen :size="13"/>{{ project.counts?.literature || 0 }} 文献</span><span><Lightbulb :size="13"/>{{ project.counts?.ideas || 0 }} Idea</span><span><FileCode2 :size="13"/>{{ project.counts?.manuscripts || 0 }} 稿件</span></div>
        <footer><span><Users :size="13"/>{{ project.role }}</span><time>{{ new Date(project.updated_at).toLocaleDateString() }}</time></footer>
      </article>
    </section>
    <section v-else class="research-empty"><FlaskConical :size="44"/><h3>从第一个科研方向开始</h3><p>创建方向后，系统会为文献、Idea、实验和论文提供统一上下文。</p><button class="btn btn-primary" @click="openCreate">创建科研方向</button></section>
    <FloatingPanel v-model="showCreate" title="新建科研方向" eyebrow="NEW RESEARCH DIRECTION" description="一个方向对应独立的文献、Idea、实验、论文和协作记录，创建后仍可继续修改。" size="large">
      <div class="form-grid"><div class="field full"><label>科研方向名称 <b class="required">*</b></label><input v-model="form.name" class="input" maxlength="160" autofocus placeholder="例如：大语言模型辅助程序设计教育研究" @keydown.ctrl.enter="createProject"><small>至少 2 个字，用于区分不同研究方向</small></div><div class="field"><label>所属学科 <b class="required">*</b></label><input v-model="form.discipline" class="input" placeholder="例如：计算机科学"></div><div class="field"><label>预期成果</label><input v-model="form.expected_outcome" class="input" placeholder="论文、数据集或原型系统"></div><div class="field full"><label>核心研究问题</label><textarea v-model="form.research_question" class="textarea" placeholder="希望回答什么可验证的科学问题？"></textarea></div><div class="field full"><label>背景与范围</label><textarea v-model="form.description" class="textarea" placeholder="说明研究对象、边界和已知约束"></textarea></div><div class="field"><label>引用格式</label><select v-model="form.citation_style" class="select"><option>GB/T 7714</option><option>APA</option><option>IEEE</option><option>Chicago</option></select></div><div class="field"><label>写作语言</label><select v-model="form.language" class="select"><option value="zh-CN">中文</option><option value="en-US">英文</option><option value="bilingual">双语</option></select></div></div>
      <template #footer><span class="create-hint">Ctrl + Enter 快速创建</span><button class="btn" :disabled="creating" @click="showCreate=false">取消</button><button class="btn btn-primary" :disabled="!canCreate || creating" @click="createProject">{{ creating ? '正在创建…' : '创建并进入' }}</button></template>
    </FloatingPanel>
    <FloatingPanel v-model="showJoin" title="加入科研协作" eyebrow="JOIN COLLABORATION" description="输入项目负责人分享的邀请码；加入后将按邀请角色获得写作、批注或只读权限。"><div class="field"><label>协作邀请码</label><input v-model="joinCode" class="input" placeholder="EVO-..." @keydown.enter="joinProject"></div><template #footer><button class="btn" @click="showJoin=false">取消</button><button class="btn btn-primary" :disabled="!joinCode.trim()" @click="joinProject">加入项目</button></template></FloatingPanel>
  </div>
</template>

<style scoped>
.required{color:#d64b4b}.field small{color:#8799a7;font-size:9px}.create-hint{margin-right:auto;color:#8497a7;font-size:9px}
.research-home{display:grid;gap:18px}.research-hero{padding:24px 28px;display:flex;align-items:center;justify-content:space-between;gap:24px;border:1px solid #bed7ea;border-radius:16px;color:#fff;background:linear-gradient(120deg,#0a3765,#1769c2 65%,#2c8bb7);box-shadow:0 12px 30px rgba(12,63,111,.18)}.research-hero span{font-size:10px;letter-spacing:.16em;color:#acd7fb}.research-hero h2{margin:7px 0;font-size:22px}.research-hero p{max-width:720px;margin:0;color:#d3e9fa;font-size:12px;line-height:1.7}.research-hero-flow{display:flex;align-items:center;gap:7px}.research-hero-flow svg{width:35px;height:35px;padding:8px;border:1px solid rgba(255,255,255,.26);border-radius:10px;background:rgba(255,255,255,.1)}.research-hero-flow i{width:18px;height:1px;background:#a9d7f5}.research-toolbar{width:360px;height:38px;padding:0 12px;display:flex;align-items:center;gap:8px;border:1px solid var(--border);border-radius:9px;background:#fff}.research-toolbar input{width:100%;border:0;outline:0;color:#294861}.project-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.project-card{padding:18px;display:grid;gap:11px;border:1px solid #d3e2ed;border-radius:13px;background:#fff;box-shadow:var(--shadow);cursor:pointer;transition:.18s}.project-card:hover{border-color:#72add6;transform:translateY(-2px);box-shadow:0 12px 26px rgba(28,77,113,.13)}.project-card header,.project-card footer{display:flex;align-items:center;justify-content:space-between}.project-card header span,.project-card header b{padding:4px 7px;border-radius:99px;font-size:8px}.project-card header span{color:#1769c2;background:#e8f4fc}.project-card header b{color:#15745a;background:#e5f6ef}.project-card h3{margin:0;color:#173e5c;font-size:15px}.project-card p{height:48px;margin:0;overflow:hidden;color:#6b8191;font-size:10px;line-height:1.6}.project-metrics{display:flex;gap:12px}.project-metrics span,.project-card footer span{display:flex;align-items:center;gap:4px;color:#5d7c91;font-size:9px}.project-card footer{padding-top:10px;border-top:1px solid #e2eaf0}.project-card time{color:#8a9aa6;font-size:8px}.research-empty{min-height:360px;display:grid;place-items:center;align-content:center;gap:9px;color:#74a0bb}.research-empty h3,.research-empty p{margin:0}.research-empty h3{color:#345e78}.research-empty p{font-size:11px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field.full{grid-column:1/-1}@media(max-width:1100px){.project-grid{grid-template-columns:1fr 1fr}.research-hero-flow{display:none}}
</style>
