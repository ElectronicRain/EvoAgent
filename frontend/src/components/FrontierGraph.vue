<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Entity } from '../services/api'
const props=defineProps<{ snapshot:Entity }>()
const selectedId=ref('')
const topics=computed(()=>props.snapshot?.nodes?.filter((item:Entity)=>item.type==='topic')||[])
const papers=computed(()=>props.snapshot?.nodes?.filter((item:Entity)=>item.type==='paper')||[])
const paperTopics=computed(()=>{
  const result:Record<string,string[]>={}
  for(const edge of props.snapshot?.edges||[]) (result[edge.target]||=[]).push(String(edge.source).replace('topic:',''))
  return result
})
const selected=computed(()=>[...topics.value,...papers.value].find((item:Entity)=>item.id===selectedId.value))
</script>
<template>
  <section class="frontier-graph">
    <header><div><strong>参考文献—主题关联图</strong><small>主题由题名与标签共现形成；点击节点查看证据，不表示因果或完整引文网络。</small></div></header>
    <div class="topic-row"><button v-for="topic in topics" :key="topic.id" :class="{active:selectedId===topic.id}" @click="selectedId=topic.id"><b>{{ topic.label }}</b><span>热度 {{ topic.heat }} · 增长 {{ topic.growth }}</span></button></div>
    <div class="paper-list"><button v-for="paper in papers" :key="paper.id" :class="{active:selectedId===paper.id}" @click="selectedId=paper.id"><span>{{ paper.year || '年份待核验' }}</span><strong>{{ paper.label }}</strong><small>{{ paperTopics[paper.id]?.join(' / ') || '尚无稳定主题连接' }} · 可信度 {{ paper.credibility }}</small></button></div>
    <article v-if="selected"><strong>{{ selected.label }}</strong><p v-if="selected.type==='topic'">项目题录出现 {{ selected.count }} 次，相对热度 {{ selected.heat }}，时间窗增长比 {{ selected.growth }}。</p><p v-else>{{ selected.authors || '作者待核验' }} · {{ selected.source || '来源待核验' }}</p><a v-if="selected.url" :href="selected.url" target="_blank" rel="noreferrer">打开来源</a></article>
  </section>
</template>
<style scoped>
.frontier-graph{border:1px solid #d8e3eb;border-radius:10px;background:#fff}.frontier-graph>header{padding:12px 14px;border-bottom:1px solid #e5ecf1}.frontier-graph strong,.frontier-graph small{display:block}.frontier-graph strong{color:#294a62;font-size:10px}.frontier-graph small{margin-top:3px;color:#7a8e9c;font-size:8px}.topic-row{padding:12px;display:flex;gap:7px;overflow:auto;border-bottom:1px solid #e7edf1}.topic-row button{min-width:125px;padding:9px;border:1px solid #b9cfdd;border-radius:7px;text-align:left;background:#fff}.topic-row button.active,.paper-list button.active{border-color:#1769c2;background:#eef7fd}.topic-row b,.topic-row span{display:block}.topic-row b{color:#315972;font-size:9px}.topic-row span{margin-top:4px;color:#728795;font-size:7px}.paper-list{max-height:390px;padding:10px;display:grid;grid-template-columns:1fr 1fr;gap:7px;overflow:auto}.paper-list button{padding:9px;display:grid;grid-template-columns:45px 1fr;gap:3px 7px;border:1px solid #dce5eb;border-radius:7px;text-align:left;background:#fff}.paper-list span{grid-row:1/3;color:#1769c2;font-size:8px}.paper-list strong{font-size:8px}.paper-list small{font-size:7px}.frontier-graph>article{margin:0 10px 10px;padding:10px;border-left:3px solid #1769c2;background:#f7fafc}.frontier-graph>article p{margin:5px 0;color:#577083;font-size:8px}.frontier-graph>article a{color:#1769c2;font-size:8px}@media(max-width:850px){.paper-list{grid-template-columns:1fr}}
</style>
