<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ figure: Record<string, any> | null }>()
const selectedNodeId = ref('')
const selectedEdgeIndex = ref(-1)
const nodes = computed(() => props.figure?.nodes || [])
const edges = computed(() => props.figure?.edges || [])
const positionedNodes = computed(() => {
  const count = Math.max(nodes.value.length, 1)
  return nodes.value.map((node: any, index: number) => {
    const ring = count > 16 && index >= Math.ceil(count / 2) ? 2 : 1
    const ringNodes = ring === 1 ? Math.min(count, Math.ceil(count / 2)) : count - Math.ceil(count / 2)
    const ringIndex = ring === 1 ? index : index - Math.ceil(count / 2)
    const angle = (Math.PI * 2 * ringIndex / Math.max(ringNodes, 1)) - Math.PI / 2
    const radiusX = ring === 1 ? 235 : 330
    const radiusY = ring === 1 ? 150 : 210
    return {...node, x:400 + Math.cos(angle)*radiusX, y:260 + Math.sin(angle)*radiusY}
  })
})
const positions = computed(() => Object.fromEntries(positionedNodes.value.map((node:any)=>[node.id,node])))
const selectedNode = computed(() => nodes.value.find((node:any)=>node.id===selectedNodeId.value))
const selectedEdge = computed(() => edges.value[selectedEdgeIndex.value])
const relationColor:Record<string,string> = {'共同作者':'#0d6b58','主题/方法相似':'#1769c2','同期研究主题':'#7954a1','时间邻近':'#8b8b8b'}
function shortTitle(value:string){return value.length>26?`${value.slice(0,26)}…`:value}
</script>

<template>
  <section v-if="figure" class="academic-graph">
    <header><div><strong>{{ figure.title }}</strong><span>{{ figure.subtitle }}</span></div><div class="graph-metrics"><b>{{ figure.metrics?.literature_count||nodes.length }}</b> 篇文献 · <b>{{ figure.metrics?.relation_count||edges.length }}</b> 条关系</div></header>
    <div class="network-shell">
      <svg viewBox="0 0 800 520" role="img" aria-label="参考文献关联关系图">
        <g class="edges">
          <line v-for="(edge,index) in edges" :key="`${edge.source}-${edge.target}-${index}`" :x1="positions[edge.source]?.x" :y1="positions[edge.source]?.y" :x2="positions[edge.target]?.x" :y2="positions[edge.target]?.y" :stroke="relationColor[edge.relation]||'#777'" :stroke-width="1+edge.strength*3" :opacity="selectedEdgeIndex<0||selectedEdgeIndex===index?.82:.12" @click="selectedEdgeIndex=index;selectedNodeId=''"/>
        </g>
        <g v-for="node in positionedNodes" :key="node.id" class="node" :class="{selected:selectedNodeId===node.id}" :transform="`translate(${node.x},${node.y})`" @click="selectedNodeId=node.id;selectedEdgeIndex=-1">
          <circle :r="selectedNodeId===node.id?25:21"/>
          <text y="4" text-anchor="middle">{{ node.year||'—' }}</text>
          <text class="node-title" y="36" text-anchor="middle">{{ shortTitle(node.label) }}</text>
        </g>
      </svg>
      <aside class="network-detail">
        <template v-if="selectedNode"><b>文献节点</b><h4>{{ selectedNode.label }}</h4><p>{{ selectedNode.authors||'作者信息待核验' }} · {{ selectedNode.year||'年份未知' }}</p><p>可信度 {{ selectedNode.credibility }} · {{ selectedNode.doi||'无 DOI' }}</p><a v-if="selectedNode.url" :href="selectedNode.url" target="_blank">核验原始来源</a></template>
        <template v-else-if="selectedEdge"><b>{{ selectedEdge.relation }}</b><h4>关系强度 {{ Math.round(selectedEdge.strength*100) }}%</h4><p>{{ selectedEdge.evidence }}</p><small>关系强度是本项目题录的计算结果；除明确元数据外，不代表直接引用或因果关系。</small></template>
        <template v-else><b>关系图使用方法</b><p>点击文献节点查看题录与来源；点击连线查看关系类型、计算证据和强度。</p><div class="legend"><span v-for="(color,label) in relationColor" :key="label"><i :style="{background:color}"/>{{ label }}</span></div></template>
      </aside>
    </div>
    <footer>所有节点保留项目 source_id；共同主题和时间邻近均明确标注推断边界。</footer>
  </section>
</template>

<style scoped>
.academic-graph{margin-top:10px;border:1px solid #172b3a;border-radius:5px;color:#111;background:#fff;font-family:"Times New Roman","SimSun",serif;overflow:hidden}.academic-graph>header{padding:11px 13px;display:flex;align-items:flex-start;justify-content:space-between;border-bottom:1px solid #172b3a}.academic-graph>header strong,.academic-graph>header span{display:block}.academic-graph>header strong{font-size:12px}.academic-graph>header span{max-width:520px;margin-top:4px;color:#59666e;font:8px "Microsoft YaHei"}.graph-metrics{font:8px "Microsoft YaHei"}.graph-metrics b{color:#1769c2;font-size:11px}.network-shell{display:grid;grid-template-columns:minmax(0,1fr) 190px;min-height:380px}.network-shell svg{width:100%;height:100%;min-height:380px;background:#fff}.edges line{cursor:pointer;transition:opacity .15s,stroke-width .15s}.edges line:hover{opacity:1!important;stroke-width:5}.node{cursor:pointer}.node circle{fill:#fff;stroke:#173f5b;stroke-width:2;transition:r .15s,fill .15s}.node:hover circle,.node.selected circle{fill:#e9f5fc;stroke:#1769c2;stroke-width:3}.node text{font-size:9px;font-weight:700;pointer-events:none}.node-title{font-size:7px!important;font-weight:400!important}.network-detail{padding:13px;border-left:1px solid #c9d3da;background:#f8fafb;font-family:"Microsoft YaHei"}.network-detail b{color:#1769c2;font-size:8px}.network-detail h4{margin:7px 0;font-size:10px;line-height:1.45}.network-detail p{margin:6px 0;color:#5d6f7a;font-size:8px;line-height:1.55}.network-detail small{display:block;color:#819099;font-size:7px;line-height:1.5}.network-detail a{color:#1769c2;font-size:8px}.legend{margin-top:10px;display:grid;gap:6px}.legend span{display:flex;align-items:center;gap:5px;font-size:7px}.legend i{width:15px;height:3px}.academic-graph>footer{padding:8px 12px;border-top:1px solid #adb8bf;color:#657680;font:7px "Microsoft YaHei"}
</style>
