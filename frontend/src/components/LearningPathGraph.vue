<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Entity } from '../services/api'

const props = defineProps<{ path: Entity }>()
const emit = defineEmits<{ select: [id: string] }>()
const selectedId = ref('')
watch(() => props.path?.current_node_id, value => { selectedId.value = String(value || '') }, { immediate: true })
const stateLabel: Record<string,string> = { mastered:'已掌握', current:'当前重点', ready:'可开始', locked:'待解锁' }
const stateColor: Record<string,string> = { mastered:'#16805f', current:'#1769c2', ready:'#5e7c90', locked:'#a4afb6' }
const layout = computed(() => {
  const nodes = props.path?.nodes || []
  const stages = props.path?.stages || []
  const positioned: Entity[] = []
  stages.forEach((stage: Entity, column: number) => {
    const stageNodes = stage.node_ids.map((id: string) => nodes.find((item: Entity) => item.id === id)).filter(Boolean)
    stageNodes.forEach((node: Entity, row: number) => positioned.push({ ...node, x: 125 + column * 290, y: 100 + row * 98 }))
  })
  const map = Object.fromEntries(positioned.map(item => [item.id, item]))
  return { nodes: positioned, map, width: Math.max(780, 250 + stages.length * 290), height: Math.max(340, 165 + Math.max(1, ...stages.map((stage: Entity) => stage.node_ids.length)) * 98) }
})
const selected = computed(() => props.path?.nodes?.find((item: Entity) => item.id === selectedId.value))
function choose(id:string){ selectedId.value=id; emit('select',id) }
</script>

<template>
  <div class="path-graph">
    <section class="goal-trace"><div><span>当前目标</span><strong>{{ path.goal }}</strong></div><div><span>自适应深度</span><strong>{{ path.active_depth }}/{{ path.target_depth }}</strong><small>{{ path.adaptive_summary }}</small></div><div><span>下一达标证据</span><strong>{{ path.next_checkpoint }}</strong></div></section>
    <div class="legend"><span v-for="(label,key) in stateLabel" :key="key"><i :style="{background:stateColor[key]}"/>{{ label }}</span></div>
    <div class="canvas">
      <svg :viewBox="`0 0 ${layout.width} ${layout.height}`" role="img" :aria-label="path.title">
        <defs><marker id="path-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#91a8b7"/></marker></defs>
        <g v-for="(stage,index) in path.stages || []" :key="stage.name">
          <text :x="125+index*290" y="30" text-anchor="middle" class="stage-title">{{ stage.name }}</text>
          <line :x1="270+index*290" y1="42" :x2="270+index*290" :y2="layout.height-32" stroke="#edf1f4" />
        </g>
        <g v-for="edge in path.edges || []" :key="`${edge.source}-${edge.target}`">
          <path v-if="layout.map[edge.source] && layout.map[edge.target]" :d="`M ${layout.map[edge.source].x+104} ${layout.map[edge.source].y} C ${layout.map[edge.source].x+138} ${layout.map[edge.source].y}, ${layout.map[edge.target].x-138} ${layout.map[edge.target].y}, ${layout.map[edge.target].x-104} ${layout.map[edge.target].y}`" fill="none" stroke="#91a8b7" stroke-width="1.5" marker-end="url(#path-arrow)"/>
        </g>
        <g v-for="node in layout.nodes" :key="node.id" class="node" role="button" tabindex="0" @click="choose(node.id)" @keydown.enter="choose(node.id)">
          <rect :x="node.x-104" :y="node.y-36" width="208" height="72" rx="8" :fill="selectedId===node.id?'#eef7fd':'#ffffff'" :stroke="stateColor[node.state]" :stroke-width="selectedId===node.id?3:1.5"/>
          <circle :cx="node.x-84" :cy="node.y-16" r="5" :fill="stateColor[node.state]"/>
          <text :x="node.x-72" :y="node.y-12" class="node-title">{{ String(node.label).slice(0,20) }}</text>
          <text :x="node.x-84" :y="node.y+13" class="node-meta">第{{ node.depth_level }}层 · 目标匹配 {{ node.goal_alignment }}% · 掌握 {{ node.mastery }}%</text>
        </g>
      </svg>
    </div>
    <article v-if="selected" class="node-detail">
      <div><strong>{{ selected.label }}</strong><span>{{ selected.depth_label }} · {{ stateLabel[selected.state] }} · 目标匹配 {{ selected.goal_alignment }}%</span></div>
      <p>{{ selected.description }}<br><b>动态调整依据：</b>{{ selected.adaptation_reason }}</p>
      <small>达标证据：{{ selected.evidence_requirement }}<br>来源：{{ selected.resources?.[0]?.source || '计算机科学学科包' }}</small>
      <button type="button" @click="emit('select',selected.id)">进入针对性学习</button>
    </article>
  </div>
</template>

<style scoped>
.path-graph{padding:12px}.goal-trace{margin-bottom:10px;display:grid;grid-template-columns:1.25fr .8fr 1fr;gap:8px}.goal-trace>div{padding:9px 11px;border:1px solid #dce5eb;border-radius:7px;background:#fafcfd;display:grid;gap:3px}.goal-trace span{color:#718795;font-size:7px}.goal-trace strong{color:#315b77;font-size:9px;line-height:1.45}.goal-trace small{color:#718795;font-size:7px;line-height:1.4}.legend{display:flex;gap:14px;flex-wrap:wrap;color:#5d7484;font-size:8px}.legend span{display:flex;align-items:center;gap:5px}.legend i{width:8px;height:8px;border-radius:50%}.canvas{margin-top:9px;max-height:640px;overflow:auto;border:1px solid #dce5eb;border-radius:8px;background:#fff}.canvas svg{min-width:760px;display:block}.stage-title{fill:#34576e;font:600 11px Arial,'Microsoft YaHei'}.node{cursor:pointer}.node:focus{outline:none}.node-title{fill:#294a62;font:600 9px Arial,'Microsoft YaHei'}.node-meta{fill:#718593;font:7px Arial,'Microsoft YaHei'}.node-detail{margin-top:9px;padding:11px;display:grid;grid-template-columns:minmax(160px,.65fr) minmax(260px,1.2fr) minmax(230px,1fr) auto;align-items:center;gap:10px;border:1px solid #dce5eb;border-radius:8px;background:#fbfcfd}.node-detail strong,.node-detail span{display:block}.node-detail strong{color:#294a62;font-size:10px}.node-detail span,.node-detail small{margin-top:3px;color:#768a98;font-size:8px}.node-detail p{margin:0;color:#4f687a;font-size:8px;line-height:1.55}.node-detail p b{color:#315b77}.node-detail button{padding:7px 10px;border:1px solid #87b6d3;border-radius:6px;color:#1769c2;background:#fff;font-size:8px}@media(max-width:900px){.goal-trace,.node-detail{grid-template-columns:1fr}.node-detail button{justify-self:start}}
</style>
