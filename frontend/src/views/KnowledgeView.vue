<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Database, FileText, Plus, Search, Upload } from 'lucide-vue-next'
import PageHeader from '../components/PageHeader.vue'
import { api, type Entity } from '../services/api'
import { useAppStore } from '../stores/app'

const store=useAppStore(), bases=ref<Entity[]>([]), selected=ref<Entity|null>(null), documents=ref<Entity[]>([]), results=ref<Entity[]>([])
const search=ref('知识可信与来源追溯'), createBase=ref(false), addText=ref(false)
const baseForm=reactive({name:'',discipline:'',description:''}), docForm=reactive({title:'',source:'用户录入',content:''})
async function load(){store.loading(true);try{bases.value=await api.get('/knowledge-bases');selected.value ||= bases.value[0]||null;if(selected.value)documents.value=await api.get(`/knowledge-bases/${selected.value.id}/documents`)}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
async function choose(item:Entity){selected.value=item;documents.value=await api.get(`/knowledge-bases/${item.id}/documents`)}
async function saveBase(){store.loading(true);try{const item:Entity=await api.post('/knowledge-bases',baseForm);store.notify('知识库已创建');createBase.value=false;await load();selected.value=item}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
async function saveText(){if(!selected.value)return;store.loading(true);try{await api.post(`/knowledge-bases/${selected.value.id}/documents/text`,docForm);store.notify('资料已切分并建立索引');addText.value=false;await choose(selected.value)}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
async function upload(event:Event){if(!selected.value)return;const file=(event.target as HTMLInputElement).files?.[0];if(!file)return;store.loading(true);try{await api.upload(`/knowledge-bases/${selected.value.id}/documents/upload`,file);store.notify('文档导入完成');await choose(selected.value)}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false);(event.target as HTMLInputElement).value=''}}
async function doSearch(){store.loading(true);try{results.value=await api.post('/knowledge/search',{query:search.value,knowledge_base_ids:selected.value?[selected.value.id]:[],top_k:8})}catch(e:any){store.notify(e.message,'error')}finally{store.loading(false)}}
onMounted(load)
</script>

<template>
  <PageHeader eyebrow="TRUSTED KNOWLEDGE" title="学科知识库" description="导入权威教材、论文、标准与课程资料，为 Agent 提供可追溯的知识依据。"><button class="btn btn-primary" @click="createBase=true"><Plus :size="15" />新建知识库</button></PageHeader>
  <div class="split">
    <section class="card">
      <div class="card-header"><h2>知识库</h2><span style="font-size:11px;color:#60758b">{{ bases.length }} 个</span></div>
      <div class="card-body grid grid-2">
        <button v-for="item in bases" :key="item.id" class="list-item" style="background:white;text-align:left" @click="choose(item)"><div style="display:flex;gap:11px"><div class="metric-icon"><Database :size="18" /></div><div><strong>{{ item.name }}</strong><p>{{ item.discipline }} · {{ item.document_count }} 份资料</p></div></div></button>
      </div>
    </section>
    <aside class="card">
      <div class="card-header"><h3>可信知识原则</h3></div><div class="card-body"><div class="notice">资料需记录名称、来源与片段位置。Agent 回答时自动检索已绑定的知识库，并把引用信息注入上下文。</div><ul style="font-size:11px;color:#526b83;line-height:2"><li>优先权威和可核查资料</li><li>禁止编造文献与数据</li><li>AI 输出保留人工复核入口</li></ul></div>
    </aside>
  </div>

  <section v-if="createBase" class="card" style="margin-top:20px"><div class="card-header"><h2>新建学科知识库</h2><button class="btn btn-sm" @click="createBase=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>名称</label><input v-model="baseForm.name" class="input"></div><div class="field"><label>学科</label><input v-model="baseForm.discipline" class="input" placeholder="例如：教育学"></div><div class="field full"><label>说明</label><input v-model="baseForm.description" class="input"></div><div class="field full"><button class="btn btn-primary" @click="saveBase">创建知识库</button></div></div></section>

  <section v-if="selected" class="card" style="margin-top:20px">
    <div class="card-header"><h2>{{ selected.name }} · 文档</h2><div style="display:flex;gap:8px"><button class="btn btn-sm" @click="addText=true"><FileText :size="14" />粘贴文本</button><label class="btn btn-sm"><Upload :size="14" />上传文件<input type="file" accept=".pdf,.docx,.txt,.md,.csv" hidden @change="upload"></label></div></div>
    <div class="table-wrap"><table><thead><tr><th>资料名称</th><th>来源</th><th>类型</th><th>字符数</th></tr></thead><tbody><tr v-for="item in documents" :key="item.id"><td>{{ item.title }}</td><td>{{ item.source }}</td><td>{{ item.mime_type }}</td><td>{{ item.char_count }}</td></tr></tbody></table><div v-if="!documents.length" class="empty"><FileText :size="28" /><br>还没有资料</div></div>
  </section>
  <section v-if="addText" class="card" style="margin-top:20px"><div class="card-header"><h2>录入可信资料</h2><button class="btn btn-sm" @click="addText=false">取消</button></div><div class="card-body form-grid"><div class="field"><label>标题</label><input v-model="docForm.title" class="input"></div><div class="field"><label>来源</label><input v-model="docForm.source" class="input"></div><div class="field full"><label>正文</label><textarea v-model="docForm.content" class="textarea" style="min-height:220px" /></div><div class="field full"><button class="btn btn-primary" @click="saveText">切分并索引</button></div></div></section>

  <section class="card" style="margin-top:20px"><div class="card-header"><h2>引用检索测试</h2><Search :size="18" color="#1769c2" /></div><div class="card-body"><div style="display:flex;gap:9px"><input v-model="search" class="input" @keyup.enter="doSearch"><button class="btn btn-primary" @click="doSearch"><Search :size="14" />检索</button></div><div class="list-stack" style="margin-top:14px"><div v-for="item in results" :key="item.id" class="list-item"><div><strong>{{ item.title }}</strong><p style="font-size:11px;color:#385570">{{ item.content }}</p><p>引用：{{ item.citation }}</p></div></div><div v-if="results.length===0" class="empty">输入问题以验证检索和引用效果</div></div></div></section>
</template>
