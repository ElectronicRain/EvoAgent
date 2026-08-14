<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Entity } from '../services/api'

const props = defineProps<{ path: Entity }>()
const emit = defineEmits<{ select: [id: string] }>()
const selectedId = ref('')
watch(() => props.path?.current_node_id, value => { selectedId.value ||= String(value || '') }, { immediate: true })
const stateLabel: Record<string,string> = { mastered:'已掌握', current:'当前重点', ready:'可开始', locked:'待解锁' }
const stateColor: Record<string,string> = { mastered:'#16805f', current:'#1769c2', ready:'#5e7c90', locked:'#a4afb6' }
const layout = computed(() => {
  const nodes = props.path?.nodes || []
  const stages = props.path?.stages || []
  const positioned: Entity[] = []
  stages.forEach((stage: Entity, column: number) => {
    const stageNodes = stage.node_ids.map((id: string) => nodes.find((item: Entity) => item.id === id)).filter(Boolean)
    stageNodes.forEach((node: Entity, row: number) => positioned.push({ ...node, x: 115 + column * 260, y: 100 + row * 112 }))
  })
  const map = Object.fromEntries(positioned.map(item => [item.id, item]))
  return { nodes: positioned, map, width: Math.max(780, 230 + stages.length * 260), height: Math.max(340, 175 + Math.max(1, ...stages.map((stage: Entity) => stage.node_ids.length)) * 112) }
})
const selected = computed(() => props.path?.nodes?.find((item: Entity) => item.id === selectedId.value))
function choose(id:string){ selectedId.value=id; emit('select',id) }
</script>

<template>
  <div class="path-graph">
    <div class="legend"><span v-for="(label,key) in stateLabel" :key="key"><i :style="{background:stateColor[key]}"/>{{ label }}</span></div>
    <div class="canvas">
      <svg :viewBox="`0 0 ${layout.width} ${layout.height}`" role="img" :aria-label="path.title">
        <defs><marker id="path-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#91a8b7"/></marker></defs>
        <g v-for="(stage,index) in path.stages || []" :key="stage.name">
          <text :x="115+index*260" y="30" text-anchor="middle" class="stage-title">{{ stage.name }}</text>
          <line :x1="245+index*260" y1="42" :x2="245+index*260" :y2="layout.height-32" stroke="#edf1f4" />
        </g>
        <g v-for="edge in path.edges || []" :key="`${edge.source}-${edge.target}`">
          <path v-if="layout.map[edge.source] && layout.map[edge.target]" :d="`M ${layout.map[edge.source].x+92} ${layout.map[edge.source].y} C ${layout.map[edge.source].x+130} ${layout.map[edge.source].y}, ${layout.map[edge.target].x-130} ${layout.map[edge.target].y}, ${layout.map[edge.target].x-92} ${layout.map[edge.target].y}`" fill="none" stroke="#91a8b7" stroke-width="1.5" marker-end="url(#path-arrow)"/>
        </g>
        <g v-for="node in layout.nodes" :key="node.id" class="node" role="button" tabindex="0" @click="choose(node.id)" @keydown.enter="choose(node.id)">
          <rect :x="node.x-92" :y="node.y-36" width="184" height="72" rx="8" :fill="selectedId===node.id?'#eef7fd':'#ffffff'" :stroke="stateColor[node.state]" :stroke-width="selectedId===node.id?3:1.5"/>
          <circle :cx="node.x-72" :cy="node.y-16" r="5" :fill="stateColor[node.state]"/>
          <text :x="node.x-60" :y="node.y-12" class="node-title">{{ String(node.label).slice(0,17) }}</text>
          <text :x="node.x-72" :y="node.y+13" class="node-meta">{{ stateLabel[node.state] }} · 掌握度 {{ node.mastery }}%</text>
        </g>
      </svg>
    </div>
    <article v-if="selected" class="node-detail">
      <div><strong>{{ selected.label }}</strong><span>{{ selected.domain }} · {{ stateLabel[selected.state] }} · {{ selected.mastery }}%</span></div>
      <p>{{ selected.description }}</p>
      <small>{{ selected.recommended_action }} · 来源：{{ selected.resources?.[0]?.source || '计算机学科学科包' }}</small>
      <button type="button" @click="emit('select',selected.id)">进入针对性学习</button>
    </article>
  </div>
</template>

<style scoped>
.path-graph{padding:12px}.legend{display:flex;gap:14px;flex-wrap:wrap;color:#5d7484;font-size:8px}.legend span{display:flex;align-items:center;gap:5px}.legend i{width:8px;height:8px;border-radius:50%}.canvas{margin-top:9px;overflow:auto;border:1px solid #dce5eb;border-radius:8px;background:#fff}.canvas svg{min-width:760px;display:block}.stage-title{fill:#34576e;font:600 11px Arial,'Microsoft YaHei'}.node{cursor:pointer}.node:focus{outline:none}.node-title{fill:#294a62;font:600 10px Arial,'Microsoft YaHei'}.node-meta{fill:#718593;font:8px Arial,'Microsoft YaHei'}.node-detail{margin-top:9px;padding:11px;display:grid;grid-template-columns:minmax(130px,.6fr) minmax(220px,1fr) minmax(220px,1fr) auto;align-items:center;gap:10px;border:1px solid #dce5eb;border-radius:8px;background:#fbfcfd}.node-detail strong,.node-detail span{display:block}.node-detail strong{color:#294a62;font-size:10px}.node-detail span,.node-detail small{margin-top:3px;color:#768a98;font-size:8px}.node-detail p{margin:0;color:#4f687a;font-size:8px;line-height:1.5}.node-detail button{padding:7px 10px;border:1px solid #87b6d3;border-radius:6px;color:#1769c2;background:#fff;font-size:8px}@media(max-width:900px){.node-detail{grid-template-columns:1fr}.node-detail button{justify-self:start}}
</style>
