<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Beaker, BrainCircuit, CheckCircle2, FlaskConical, Plus, TrendingUp, XCircle } from 'lucide-vue-next'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store=useAppStore(),proposals=ref<Entity[]>([]),agents=ref<Entity[]>([]),cases=ref<Entity[]>([]),showForm=ref(false),showCaseForm=ref(false),evaluatingId=ref('')
const form=reactive({agent_id:'',reason:'提升回答的学术可信度、结构清晰度和可追溯性',proposed_prompt:'你是严谨的学科研究专家。回答必须先给结论，再列证据与来源，最后给出风险和待核验项。复杂任务需要分步拆解并明确验收标准。',proposed_tools:null as string[]|null})
const caseForm=reactive({name:'',discipline:'通用',input:'',expected_keywords:'',requires_citation:false})
const evaluationProgress=reactive<Record<string,any>>({})
async function load(){store.loading(true);try{[proposals.value,agents.value,cases.value]=await Promise.all([api.get('/evolution'),api.get('/agents'),api.get('/evaluation-cases')]);form.agent_id ||= agents.value.find(a=>a.status==='active')?.id||''}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
async function create(){store.loading(true);try{await api.post('/evolution',form);store.notify('候选版本已创建，尚未获得生产权限');showForm.value=false;await load()}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
async function createCase(){store.loading(true);try{await api.post('/evaluation-cases',{name:caseForm.name,discipline:caseForm.discipline,input:caseForm.input,expected_keywords:caseForm.expected_keywords.split(/[，,\n]/).map(v=>v.trim()).filter(Boolean),requires_citation:caseForm.requires_citation});store.notify('评测用例已添加');Object.assign(caseForm,{name:'',discipline:'通用',input:'',expected_keywords:'',requires_citation:false});showCaseForm.value=false;await load()}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
async function evaluate(id:string){
  evaluatingId.value=id
  const state:any=evaluationProgress[id]={message:'正在连接评测服务…',completed:0,total:cases.value.length,elapsed:0,cases:[] as any[],error:'',stages:[] as any[],sources:[] as any[],skill:null,originalPrompt:'',optimizedPrompt:'',taskPrompt:'',artifact:null}
  try{
    await api.stream(`/evolution/${id}/evaluate/stream`,{},event=>{
      if(event.type==='error'){state.error=event.message||'评测失败';state.message=state.error;return}
      if(event.type==='evolution_result'){state.message='评测完成，正在刷新报告…';return}
      if(event.type!=='step')return
      const step=event.step||{}
      if(step.type==='stream_connected')state.message='评测通道已连接，准备运行基线版本…'
      else if(step.type==='evaluation_started'){state.total=step.total_cases;state.message=`开始 ${step.total_cases} 个用例的新旧版本对照评测`}
      else if(step.type==='evolution_stage_started'){state.stages.push({stage:step.stage,label:step.label,status:'running'});state.message=step.label}
      else if(step.type==='evolution_research_event'){
        const research=step.event||{}
        if(research.type==='research_planning')state.message=`已规划 ${research.queries?.length||0} 组进化方法检索词`
        else if(research.type==='web_search_started')state.message=`正在检索：${research.query}`
        else if(research.type==='web_page_fetched')state.message=`正在整理方法来源：${research.title}`
      }
      else if(step.type==='evolution_methods_ready'){state.sources=step.sources||[];state.message=`已整理 ${step.count} 条联网方法来源`}
      else if(step.type==='evolution_prompt_optimized'){state.originalPrompt=step.original_prompt;state.optimizedPrompt=step.optimized_prompt;state.taskPrompt=step.task_prompt_template;state.message='目标任务提示词已优化'}
      else if(step.type==='evolution_skill_packaged'){state.skill=step.skill;state.message=`Skill 已封装：${step.skill?.name||''}`}
      else if(step.type==='evaluation_case_started')state.message=`用例 ${step.index}/${step.total_cases}：${step.case}`
      else if(step.type==='evaluation_phase_started')state.message=`${step.case}：正在运行${step.phase==='baseline'?'基线版本':'候选版本'}`
      else if(step.type==='evaluation_case_completed'){state.completed=step.index;state.cases.push(step);state.message=`已完成 ${step.index}/${step.total_cases}：${step.case}`}
      else if(step.type==='evaluation_waiting'){state.elapsed=step.elapsed_seconds;state.message=`Agent 仍在评测中，已等待 ${step.elapsed_seconds} 秒…`}
      else if(step.type==='evolution_artifact_created'){state.artifact=step.artifact;state.message=`进化成果已生成：${step.artifact?.title||''}`}
      else if(step.type==='evaluation_completed')state.message=`评测完成：基线 ${step.baseline_score}，候选 ${step.candidate_score}`
    })
    if(state.error)throw new Error(state.error)
    store.notify('新旧版本对照评测完成')
    await load()
  }catch(e:any){store.notify(e.message,'error')}finally{evaluatingId.value=''}
}
async function decide(id:string,approved:boolean){store.loading(true);try{await api.post(`/evolution/${id}/decide`,{approved,decided_by:'local-user'});store.notify(approved?'候选版本已激活，可随时回溯旧版本':'候选版本已拒绝');await load()}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
const report=(value:string)=>{try{return JSON.parse(value||'{}')}catch{return{}}}
const renderMarkdown=(value:string)=>DOMPurify.sanitize(marked.parse(value||'',{async:false}) as string)
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="CONTROLLED EVOLUTION" title="进化实验室" description="让 Agent 基于真实轨迹提出改进，通过离线评测、人工审批和版本回溯完成受控进化。"><button class="btn btn-primary" @click="showForm=true"><Plus :size="15" />发起进化</button></PageHeader>
  <div class="grid grid-3"><div class="card metric-card"><div class="metric-icon"><BrainCircuit :size="21" /></div><div><strong>{{ proposals.length }}</strong><span>进化提案</span></div></div><div class="card metric-card"><div class="metric-icon"><Beaker :size="21" /></div><div><strong>{{ cases.length }}</strong><span>基准评测用例</span></div></div><div class="card metric-card"><div class="metric-icon"><TrendingUp :size="21" /></div><div><strong>{{ proposals.filter(p=>p.status==='approved').length }}</strong><span>已批准版本</span></div></div></div>
  <div class="notice" style="margin-top:18px">Agent 不能直接覆盖生产版本，也不能自行扩大权限。每次进化都会创建独立候选版本，只有完成基准评测并经用户审批后才能激活。</div>

  <section class="card" style="margin-top:20px"><div class="card-header"><div><h2>基准评测用例</h2><p style="font-size:10px;color:#72869a;margin:5px 0 0">每次对照评测会让基线与候选版本分别运行全部用例</p></div><button class="btn btn-sm" @click="showCaseForm=!showCaseForm"><Plus :size="14" />添加用例</button></div>
    <div v-if="showCaseForm" class="card-body form-grid" style="border-bottom:1px solid #e2ecf5"><div class="field"><label>用例名称</label><input v-model="caseForm.name" class="input" placeholder="例如：引用可追溯性"></div><div class="field"><label>学科</label><input v-model="caseForm.discipline" class="input"></div><div class="field full"><label>测试输入</label><textarea v-model="caseForm.input" class="textarea" placeholder="输入需要 Agent 完成的真实任务" /></div><div class="field full"><label>期望关键词（逗号或换行分隔）</label><input v-model="caseForm.expected_keywords" class="input" placeholder="证据，来源，核验"></div><label class="field full" style="display:flex;flex-direction:row;align-items:center;gap:8px"><input v-model="caseForm.requires_citation" type="checkbox">要求回答包含引用或来源</label><div class="field full"><button class="btn btn-primary" :disabled="!caseForm.name.trim()||!caseForm.input.trim()" @click="createCase">保存评测用例</button></div></div>
    <div class="card-body"><div class="list-stack"><div v-for="c in cases" :key="c.id" class="list-item"><div><strong>{{ c.name }}</strong><p>{{ c.discipline }} · {{ c.input_text }}</p></div><div style="display:flex;gap:6px"><span class="tag">{{ JSON.parse(c.expected_keywords_json||'[]').length }} 关键词</span><span v-if="c.requires_citation" class="tag">需引用</span></div></div><div v-if="!cases.length" class="empty">尚无评测用例，请先添加至少一个用例</div></div></div>
  </section>

  <section v-if="showForm" class="card" style="margin-top:20px"><div class="card-header"><h2>创建受控进化提案</h2><button class="btn btn-sm" @click="showForm=false">取消</button></div><div class="card-body form-grid"><div class="field full"><label>源 Agent</label><select v-model="form.agent_id" class="select"><option v-for="item in agents.filter(a=>a.status==='active')" :key="item.id" :value="item.id">{{ item.name }} v{{ item.version }}</option></select></div><div class="field full"><label>进化原因</label><input v-model="form.reason" class="input"></div><div class="field full"><label>候选系统提示词</label><textarea v-model="form.proposed_prompt" class="textarea" style="min-height:160px" /></div><div class="field full"><button class="btn btn-primary" @click="create"><FlaskConical :size="15" />创建候选版本</button></div></div></section>

  <section class="card" style="margin-top:20px"><div class="card-header"><h2>版本评测与审批</h2><span style="font-size:11px;color:#60758b">旧版本始终保留</span></div><div class="card-body list-stack">
    <article v-for="item in proposals" :key="item.id" class="card" style="box-shadow:none"><div class="card-header"><div><h3>{{ item.reason }}</h3><p style="font-size:10px;color:#72869a;margin:5px 0 0">提案 {{ item.id.slice(0,8) }}</p></div><StatusBadge :status="evaluatingId===item.id?'running':item.status" /></div><div class="card-body"><div class="grid grid-3"><div><span style="font-size:10px;color:#71869b">基线得分</span><div class="score">{{ item.baseline_score.toFixed(1) }}</div></div><div><span style="font-size:10px;color:#71869b">候选得分</span><div class="score">{{ item.candidate_score.toFixed(1) }}</div></div><div><span style="font-size:10px;color:#71869b">变化</span><div class="score" :style="item.candidate_score>=item.baseline_score?'color:#17805b':'color:#b53d3d'">{{ (item.candidate_score-item.baseline_score).toFixed(1) }}</div></div></div>
      <div v-if="evaluationProgress[item.id]&&evaluatingId===item.id" class="notice" style="margin-top:15px"><strong>{{ evaluationProgress[item.id].message }}</strong><div style="height:7px;background:#dbe9f5;border-radius:99px;margin-top:10px;overflow:hidden"><div :style="{width:`${evaluationProgress[item.id].total?Math.max(5,evaluationProgress[item.id].completed/evaluationProgress[item.id].total*100):5}%`,height:'100%',background:'#2878c8',transition:'width .25s'}"></div></div><p style="margin:7px 0 0;font-size:10px">{{ evaluationProgress[item.id].completed }}/{{ evaluationProgress[item.id].total }} 个用例完成；当前流程包含联网方法检索、Skill 封装、提示词优化和新旧版本评测。</p><div v-if="evaluationProgress[item.id].sources.length" style="margin-top:10px"><strong>已整理 {{ evaluationProgress[item.id].sources.length }} 条方法来源</strong><div v-for="source in evaluationProgress[item.id].sources.slice(0,4)" :key="source.url" style="font-size:10px;margin-top:5px"><a :href="source.url" target="_blank" rel="noreferrer">{{ source.title }}</a> · 可信度 {{ source.credibility?.score||0 }}/100</div></div><p v-if="evaluationProgress[item.id].skill" style="margin:8px 0 0">已封装 Skill：{{ evaluationProgress[item.id].skill.name }}</p></div>

      <section v-if="report(item.report_json).skill" style="margin-top:15px;border:1px solid #d5e4f1;border-radius:12px;overflow:hidden"><div style="padding:12px 14px;background:#f3f8fc"><strong>进化过程与成果</strong><p style="font-size:10px;color:#657b90;margin:5px 0 0">联网方法 → Skill 封装 → 目标任务提示词优化 → 对照评测 → Markdown 成果</p></div><div style="padding:14px">
        <div class="grid grid-3"><div class="list-item"><div><strong>联网方法</strong><p>{{ report(item.report_json).research_sources?.length||0 }} 条可追溯来源</p></div></div><div class="list-item"><div><strong>封装 Skill</strong><p>{{ report(item.report_json).skill.name }}</p></div></div><div class="list-item"><div><strong>进化成果</strong><p>{{ report(item.report_json).artifact?.relative_path||'生成中' }}</p></div></div></div>
        <details v-if="report(item.report_json).research_sources?.length" style="margin-top:12px"><summary>查看联网方法来源与可信度</summary><div class="list-stack" style="margin-top:10px"><div v-for="source in report(item.report_json).research_sources" :key="source.url" class="list-item"><div><a :href="source.url" target="_blank" rel="noreferrer"><strong>{{ source.title }}</strong></a><p>{{ source.source }} · {{ source.method_excerpt }}</p></div><span class="tag">可信度 {{ source.credibility?.score||0 }}/100</span></div></div></details>
        <details style="margin-top:12px"><summary>查看目标任务提示词优化前后对比</summary><div class="grid grid-2" style="margin-top:10px"><div><strong>优化前</strong><pre style="white-space:pre-wrap;background:#f7f9fb;padding:10px;border-radius:8px;font-size:10px">{{ report(item.report_json).original_prompt }}</pre></div><div><strong>优化后</strong><pre style="white-space:pre-wrap;background:#edf6ff;padding:10px;border-radius:8px;font-size:10px">{{ report(item.report_json).optimized_prompt }}</pre></div></div><strong>目标任务模板</strong><pre style="white-space:pre-wrap;background:#f4f8fc;padding:10px;border-radius:8px;font-size:10px">{{ report(item.report_json).task_prompt_template }}</pre></details>
        <details style="margin-top:12px"><summary>查看并复制进化 Skill</summary><p style="font-size:10px;color:#657b90">{{ report(item.report_json).skill.source_path }}</p><pre style="white-space:pre-wrap;max-height:360px;overflow:auto;background:#f7f9fb;padding:12px;border-radius:8px;font-size:10px">{{ report(item.report_json).skill.instructions }}</pre></details>
        <details v-if="report(item.report_json).artifact" style="margin-top:12px"><summary>放大查看 Markdown 进化成果</summary><div style="margin-top:10px;max-height:650px;overflow:auto;padding:18px;background:white;border:1px solid #e0e8f0;border-radius:8px" v-html="renderMarkdown(report(item.report_json).artifact.content)" /></details>
      </div></section>

      <div v-if="report(item.report_json).cases" class="table-wrap" style="margin-top:15px"><table><thead><tr><th>用例</th><th>基线</th><th>候选</th><th>变化</th><th>状态</th></tr></thead><tbody><tr v-for="c in report(item.report_json).cases" :key="c.case"><td>{{ c.case }}</td><td>{{ c.baseline }}</td><td>{{ c.candidate }}</td><td>{{ c.delta }}</td><td>{{ c.baseline_status&&c.candidate_status?`${c.baseline_status}/${c.candidate_status}`:'已完成' }}<p v-if="c.baseline_error||c.candidate_error" style="color:#b53d3d;margin:3px 0 0">{{ c.baseline_error||c.candidate_error }}</p></td></tr></tbody></table></div><div style="display:flex;gap:8px;margin-top:15px"><button v-if="item.status==='draft'||item.status==='evaluated'||item.status==='evaluating'" class="btn btn-primary" :disabled="!!evaluatingId||!cases.length" @click="evaluate(item.id)"><Beaker :size="14" />{{ evaluatingId===item.id?'进化运行中…':item.status==='evaluated'?'重新进化评测':'开始进化与评测' }}</button><template v-if="item.status==='evaluated'&&!evaluatingId"><button class="btn btn-primary" @click="decide(item.id,true)"><CheckCircle2 :size="14" />批准并激活</button><button class="btn btn-danger" @click="decide(item.id,false)"><XCircle :size="14" />拒绝候选</button></template></div></div></article>
    <div v-if="!proposals.length" class="empty"><BrainCircuit :size="30" /><br>尚未发起进化提案</div>
  </div></section>
</template>
