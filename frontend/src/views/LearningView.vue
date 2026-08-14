<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { BookOpenCheck, Clock3, GraduationCap, Plus, Search, Target } from 'lucide-vue-next'
import FloatingPanel from '../components/FloatingPanel.vue'
import PageHeader from '../components/PageHeader.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()
const projects = ref<Entity[]>([])
const pack = ref<Entity | null>(null)
const search = ref('')
const showCreate = ref(false)
const creating = ref(false)
const form = reactive({
  name: '', project_type: 'course', description: '', target: '',
  current_level: 'beginner', target_level: 'proficient', weekly_hours: 6,
  deadline: '', track: '计算机基础',
})
const visibleProjects = computed(() => projects.value.filter(item =>
  !search.value || `${item.name}${item.target}${item.settings?.track || ''}`.toLowerCase().includes(search.value.toLowerCase()),
))

function resetForm() {
  Object.assign(form, { name: '', project_type: 'course', description: '', target: '', current_level: 'beginner', target_level: 'proficient', weekly_hours: 6, deadline: '', track: '计算机基础' })
  showCreate.value = true
}
async function load() {
  try {
    const [items, subjectPack] = await Promise.all([
      api.get<Entity[]>('/learning-projects'),
      api.get<Entity>('/learning-subject-packs/computer-science'),
    ])
    projects.value = items
    pack.value = subjectPack
  } catch (error: any) { store.notify(error.message || '学习空间载入失败', 'error') }
}
async function createProject() {
  if (form.name.trim().length < 2 || creating.value) return
  creating.value = true
  try {
    const project = await api.post<Entity>('/learning-projects', {
      ...form,
      name: form.name.trim(),
      target: form.target.trim(),
      description: form.description.trim(),
      deadline: form.deadline ? new Date(form.deadline).toISOString() : null,
    })
    showCreate.value = false
    store.notify('学习方向已创建，计算机学科包和通用 Agent 已自动绑定')
    await router.push(`/learning/${project.id}/overview`)
  } catch (error: any) { store.notify(error.message || '创建学习方向失败', 'error') }
  finally { creating.value = false }
}
function typeLabel(value: string) { return ({ course: '课程学习', exam: '考试备考', skill: '技能提升', topic: '专题学习', project: '项目实践' } as Record<string,string>)[value] || value }
onMounted(load)
</script>

<template>
  <div class="learning-home">
    <PageHeader title="学习空间" description="围绕学习目标组织计划、知识脉络、辅导、练习、错题与量化评测。">
      <template #actions><button class="btn btn-primary" @click="resetForm"><Plus :size="15" />新建学习方向</button></template>
    </PageHeader>

    <section class="pack-summary">
      <div><GraduationCap :size="24" /><span><strong>计算机科学学科包</strong><small>创建方向后自动绑定，可在项目内调整 Agent、工作流和知识库。</small></span></div>
      <dl><div><dt>知识库</dt><dd>{{ pack?.knowledge_bases?.length || 0 }}</dd></div><div><dt>学习 Agent</dt><dd>{{ pack?.agents?.length || 0 }}</dd></div><div><dt>工作流</dt><dd>{{ pack?.workflows?.length || 0 }}</dd></div></dl>
    </section>

    <div class="learning-toolbar"><Search :size="15" /><input v-model="search" placeholder="搜索学习方向或目标"></div>

    <section v-if="visibleProjects.length" class="learning-grid">
      <article v-for="project in visibleProjects" :key="project.id" class="learning-card" @click="router.push(`/learning/${project.id}/overview`)">
        <header><span>{{ typeLabel(project.project_type) }}</span><b>{{ project.settings?.track || '计算机基础' }}</b></header>
        <h3>{{ project.name }}</h3>
        <p>{{ project.target || project.description || '尚未填写学习目标' }}</p>
        <div class="progress-line"><i :style="{ width: `${project.progress || 0}%` }" /></div>
        <div class="learning-stats"><span><BookOpenCheck :size="13" />{{ project.counts?.tasks || 0 }} 项任务</span><span><Target :size="13" />掌握度 {{ project.mastery || 0 }}%</span><span><Clock3 :size="13" />每周 {{ project.weekly_hours }} 小时</span></div>
        <footer><span>进度 {{ project.progress || 0 }}%</span><time>{{ new Date(project.updated_at).toLocaleDateString() }}</time></footer>
      </article>
    </section>
    <section v-else class="learning-empty"><GraduationCap :size="42" /><h3>建立第一个学习方向</h3><p>系统会自动提供计算机知识路径、辅导 Agent、练习和评测闭环。</p><button class="btn btn-primary" @click="resetForm">新建学习方向</button></section>

    <FloatingPanel v-model="showCreate" title="新建学习方向" description="只需填写目标和时间条件，创建后仍可修改。" size="large">
      <div class="form-grid">
        <div class="field full"><label>学习方向名称 *</label><input v-model="form.name" class="input" maxlength="160" autofocus placeholder="例如：计算机基础系统学习"></div>
        <div class="field"><label>学习类型</label><select v-model="form.project_type" class="select"><option value="course">课程学习</option><option value="exam">考试备考</option><option value="skill">技能提升</option><option value="topic">专题学习</option><option value="project">项目实践</option></select></div>
        <div class="field"><label>计算机方向</label><select v-model="form.track" class="select"><option>计算机基础</option><option>程序设计</option><option>算法与数据结构</option><option>计算机组成与体系结构</option><option>操作系统</option><option>计算机网络</option><option>数据库系统</option><option>软件工程</option><option>程序设计语言与编译</option><option>并行与分布式计算</option><option>网络安全</option><option>人工智能</option><option>大模型、RAG 与智能体</option><option>计算机视觉</option><option>计算机图形学与人机交互</option><option>数据科学</option><option>Web 全栈开发</option></select></div>
        <div class="field full"><label>期望达到的目标</label><textarea v-model="form.target" class="textarea" placeholder="说明希望能独立完成什么任务，或达到什么可量化结果"></textarea></div>
        <div class="field full"><label>已有基础与补充说明</label><textarea v-model="form.description" class="textarea" placeholder="可填写已学课程、薄弱环节和偏好的学习方式"></textarea></div>
        <div class="field"><label>当前水平</label><select v-model="form.current_level" class="select"><option value="beginner">入门</option><option value="foundation">有基础</option><option value="intermediate">中级</option><option value="advanced">高级</option></select></div>
        <div class="field"><label>目标水平</label><select v-model="form.target_level" class="select"><option value="foundation">基础掌握</option><option value="intermediate">中级应用</option><option value="proficient">熟练应用</option><option value="advanced">高级综合</option></select></div>
        <div class="field"><label>每周可用时间（小时）</label><input v-model.number="form.weekly_hours" type="number" min="1" max="80" class="input"></div>
        <div class="field"><label>期望完成日期</label><input v-model="form.deadline" type="date" class="input"></div>
      </div>
      <template #footer><button class="btn" @click="showCreate=false">取消</button><button class="btn btn-primary" :disabled="form.name.trim().length<2 || creating" @click="createProject">{{ creating ? '正在创建…' : '创建并进入' }}</button></template>
    </FloatingPanel>
  </div>
</template>

<style scoped>
.learning-home{display:grid;gap:16px}.pack-summary{padding:16px 18px;display:flex;align-items:center;justify-content:space-between;gap:20px;border:1px solid #d6e2eb;border-radius:11px;background:#fff}.pack-summary>div{display:flex;align-items:center;gap:11px;color:#1769c2}.pack-summary span{display:grid;gap:3px}.pack-summary strong{color:#24465f;font-size:13px}.pack-summary small{color:#718696;font-size:10px}.pack-summary dl{margin:0;display:flex;gap:8px}.pack-summary dl>div{min-width:78px;padding:8px 12px;border-left:1px solid #e1e9ef}.pack-summary dt{color:#7b8d9a;font-size:9px}.pack-summary dd{margin:4px 0 0;color:#244b69;font-size:16px;font-weight:700}.learning-toolbar{width:360px;height:38px;padding:0 12px;display:flex;align-items:center;gap:8px;border:1px solid var(--border);border-radius:8px;background:#fff}.learning-toolbar input{width:100%;border:0;outline:0}.learning-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}.learning-card{padding:17px;display:grid;gap:10px;border:1px solid #d6e2eb;border-radius:11px;background:#fff;cursor:pointer}.learning-card:hover{border-color:#7bafd2;box-shadow:0 7px 18px rgba(35,76,105,.08)}.learning-card header,.learning-card footer{display:flex;align-items:center;justify-content:space-between}.learning-card header span,.learning-card header b{font-size:9px;font-weight:600}.learning-card header span{color:#1769c2}.learning-card header b{color:#5d7485}.learning-card h3{margin:0;color:#213f57;font-size:14px}.learning-card p{height:43px;margin:0;overflow:hidden;color:#718493;font-size:10px;line-height:1.55}.progress-line{height:6px;overflow:hidden;border-radius:4px;background:#edf2f5}.progress-line i{display:block;height:100%;background:#2a78bd}.learning-stats{display:flex;flex-wrap:wrap;gap:10px}.learning-stats span{display:flex;align-items:center;gap:4px;color:#5f788b;font-size:9px}.learning-card footer{padding-top:8px;border-top:1px solid #e6edf2;color:#8495a1;font-size:9px}.learning-empty{min-height:330px;display:grid;place-items:center;align-content:center;gap:8px;color:#7d9aab}.learning-empty h3,.learning-empty p{margin:0}.learning-empty h3{color:#385d76}.learning-empty p{font-size:10px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field{display:grid;gap:5px}.field label{color:#526d80;font-size:9px}.field.full{grid-column:1/-1}.textarea{min-height:78px;resize:vertical}@media(max-width:1100px){.learning-grid{grid-template-columns:1fr 1fr}.pack-summary{align-items:flex-start}.pack-summary dl{flex-wrap:wrap}}@media(max-width:760px){.learning-grid{grid-template-columns:1fr}.pack-summary{display:grid}.learning-toolbar{width:100%}.form-grid{grid-template-columns:1fr}.field.full{grid-column:auto}}
</style>
