<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import JXG from 'jsxgraph'
import katex from 'katex'
import { marked } from 'marked'
import 'katex/dist/katex.min.css'

type ChartObject = {
  type: string
  expression?: string
  xExpression?: string
  yExpression?: string
  range?: number[]
  coords?: number[]
  points?: number[][]
  center?: number[]
  radius?: number
  name?: string
  color?: string
}

type ChartSpec = {
  title?: string
  boundingBox?: number[]
  axis?: boolean
  objects?: ChartObject[]
}

const props = defineProps<{ content: string }>()
const root = ref<HTMLElement | null>(null)
const boards: JXG.Board[] = []

function safeColor(value?: string) {
  return value && /^#[0-9a-f]{6}$/i.test(value) ? value : '#1769c2'
}

function compileExpression(expression: string) {
  const source = String(expression || '').trim()
  if (!source || !/^[0-9A-Za-z_+\-*/^().,\s]+$/.test(source)) {
    throw new Error('表达式包含不支持的字符')
  }
  const names: Record<string, string> = {
    pi: 'Math.PI',
    e: 'Math.E',
    sin: 'Math.sin',
    cos: 'Math.cos',
    tan: 'Math.tan',
    asin: 'Math.asin',
    acos: 'Math.acos',
    atan: 'Math.atan',
    sqrt: 'Math.sqrt',
    abs: 'Math.abs',
    exp: 'Math.exp',
    log: 'Math.log',
    floor: 'Math.floor',
    ceil: 'Math.ceil',
    round: 'Math.round',
    min: 'Math.min',
    max: 'Math.max',
  }
  const translated = source
    .replace(/\^/g, '**')
    .replace(/[A-Za-z_][A-Za-z0-9_]*/g, token => {
      const normalized = token.toLowerCase()
      if (normalized === 'x' || normalized === 't') return normalized
      if (names[normalized]) return names[normalized]
      throw new Error(`不支持的数学标识符：${token}`)
    })
  const evaluator = Function('x', 't', `"use strict";return (${translated});`) as (
    x: number,
    t: number,
  ) => number
  return (x = 0, t = 0) => {
    const value = Number(evaluator(x, t))
    return Number.isFinite(value) ? value : Number.NaN
  }
}

function renderContent(value: string) {
  const graphs: string[] = []
  const formulas: string[] = []
  let source = String(value || '').replace(
    /```jsxgraph\s*\n([\s\S]*?)\n```/gi,
    (_match, spec) => {
      const index = graphs.push(String(spec).trim()) - 1
      return `\n\nEVOGRAPH${index}TOKEN\n\n`
    },
  )
  source = source.replace(/\$\$([\s\S]+?)\$\$/g, (_match, formula) => {
    const index = formulas.push(
      katex.renderToString(String(formula).trim(), {
        displayMode: true,
        throwOnError: false,
      }),
    ) - 1
    return `\n\nEVOMATH${index}TOKEN\n\n`
  })
  source = source.replace(/(?<!\\)\$([^$\n]+?)\$/g, (_match, formula) => {
    const index = formulas.push(
      katex.renderToString(String(formula).trim(), {
        displayMode: false,
        throwOnError: false,
      }),
    ) - 1
    return `EVOMATH${index}TOKEN`
  })
  let html = marked.parse(source, {
    async: false,
    gfm: true,
    breaks: true,
  }) as string
  formulas.forEach((formula, index) => {
    html = html.replaceAll(`EVOMATH${index}TOKEN`, formula)
  })
  graphs.forEach((graph, index) => {
    const encoded = encodeURIComponent(graph)
    html = html.replace(
      new RegExp(`<p>\\s*EVOGRAPH${index}TOKEN\\s*</p>|EVOGRAPH${index}TOKEN`, 'g'),
      `<section class="jsxgraph-card"><header>交互式数学图表</header><div class="jsxgraph-board" data-spec="${encoded}"></div><small>可拖动、缩放并查看图形关系</small></section>`,
    )
  })
  return DOMPurify.sanitize(html)
}

function validPoint(value: unknown): value is number[] {
  return Array.isArray(value)
    && value.length === 2
    && value.every(item => Number.isFinite(Number(item)))
}

function renderObject(board: JXG.Board, object: ChartObject) {
  const color = safeColor(object.color)
  const attributes = {
    name: String(object.name || ''),
    strokeColor: color,
    fillColor: color,
    withLabel: Boolean(object.name),
    strokeWidth: 2,
  }
  if (object.type === 'functiongraph' && object.expression) {
    const evaluator = compileExpression(object.expression)
    const range = Array.isArray(object.range) && object.range.length === 2
      ? object.range.map(Number)
      : [-10, 10]
    board.create('functiongraph', [(x: number) => evaluator(x, 0), range[0], range[1]], attributes)
  } else if (object.type === 'curve' && object.xExpression && object.yExpression) {
    const xEvaluator = compileExpression(object.xExpression)
    const yEvaluator = compileExpression(object.yExpression)
    const range = Array.isArray(object.range) && object.range.length === 2
      ? object.range.map(Number)
      : [0, 2 * Math.PI]
    board.create(
      'curve',
      [(t: number) => xEvaluator(0, t), (t: number) => yEvaluator(0, t), range[0], range[1]],
      attributes,
    )
  } else if (object.type === 'point' && validPoint(object.coords)) {
    board.create('point', object.coords.map(Number), {
      ...attributes,
      size: 3,
    })
  } else if (
    ['line', 'segment', 'arrow'].includes(object.type)
    && Array.isArray(object.points)
    && object.points.length >= 2
    && validPoint(object.points[0])
    && validPoint(object.points[1])
  ) {
    const points = object.points.slice(0, 2).map(coords =>
      board.create('point', coords.map(Number), { visible: false, fixed: true }),
    )
    board.create(object.type, points, attributes)
  } else if (
    object.type === 'polygon'
    && Array.isArray(object.points)
    && object.points.length >= 3
    && object.points.every(validPoint)
  ) {
    board.create(
      'polygon',
      object.points.map(coords => coords.map(Number)),
      {
        ...attributes,
        fillOpacity: 0.12,
      },
    )
  } else if (
    object.type === 'circle'
    && validPoint(object.center)
    && Number(object.radius) > 0
  ) {
    const center = board.create('point', object.center.map(Number), {
      ...attributes,
      size: 2,
    })
    board.create('circle', [center, Number(object.radius)], {
      ...attributes,
      fillOpacity: 0.06,
    })
  }
}

function clearBoards() {
  while (boards.length) {
    const board = boards.pop()
    if (board) JXG.JSXGraph.freeBoard(board)
  }
}

async function renderBoards() {
  await nextTick()
  clearBoards()
  if (!root.value) return
  for (const container of root.value.querySelectorAll<HTMLElement>('.jsxgraph-board')) {
    try {
      const spec = JSON.parse(decodeURIComponent(container.dataset.spec || '')) as ChartSpec
      const boundingBox = (Array.isArray(spec.boundingBox)
        && spec.boundingBox.length === 4
        && spec.boundingBox.every(item => Number.isFinite(Number(item)))
        ? spec.boundingBox.map(Number)
        : [-5, 5, 5, -5]) as [number, number, number, number]
      const board = JXG.JSXGraph.initBoard(container, {
        boundingbox: boundingBox,
        axis: spec.axis !== false,
        keepaspectratio: false,
        showNavigation: true,
        showCopyright: false,
        pan: { enabled: true },
        zoom: { wheel: true },
      })
      boards.push(board)
      for (const object of (spec.objects || []).slice(0, 30)) {
        renderObject(board, object)
      }
      const heading = container.previousElementSibling
      if (heading && spec.title) heading.textContent = spec.title
    } catch (error: any) {
      container.classList.add('jsxgraph-error')
      container.textContent = `图表配置无法渲染：${error.message}`
    }
  }
}

watch(() => props.content, renderBoards, { flush: 'post' })
onMounted(renderBoards)
onBeforeUnmount(clearBoards)
</script>

<template>
  <div ref="root" class="rich-agent-message" v-html="renderContent(content)" />
</template>

<style scoped>
.rich-agent-message{width:100%;box-sizing:border-box;white-space:normal}.rich-agent-message :deep(> :first-child){margin-top:0}.rich-agent-message :deep(> :last-child){margin-bottom:0}.rich-agent-message :deep(h1),.rich-agent-message :deep(h2),.rich-agent-message :deep(h3),.rich-agent-message :deep(h4){margin:17px 0 8px;color:#173f60;line-height:1.35}.rich-agent-message :deep(h1){padding-bottom:8px;border-bottom:1px solid #cbdde9;font-size:18px}.rich-agent-message :deep(h2){padding-bottom:6px;border-bottom:1px solid #d6e4ed;font-size:16px}.rich-agent-message :deep(h3){font-size:14px}.rich-agent-message :deep(h4){font-size:13px}.rich-agent-message :deep(p){margin:0 0 10px;padding:0;border:0;border-radius:0;color:inherit;background:transparent;font-size:12px;line-height:1.75;white-space:normal}.rich-agent-message :deep(strong){margin:0;color:#163e5e;font-size:inherit}.rich-agent-message :deep(hr){height:1px;margin:15px 0;border:0;background:#cedde7}.rich-agent-message :deep(blockquote){margin:10px 0;padding:9px 12px;border-left:3px solid #2b88bd;border-radius:0 7px 7px 0;color:#49697e;background:#e7f2f8}.rich-agent-message :deep(blockquote p){margin:0}.rich-agent-message :deep(ul),.rich-agent-message :deep(ol){margin:8px 0 11px;padding-left:23px}.rich-agent-message :deep(li){margin:4px 0;padding-left:2px;line-height:1.7}.rich-agent-message :deep(table){display:block;width:100%;margin:11px 0;overflow-x:auto;border-collapse:collapse;font-size:11px}.rich-agent-message :deep(th),.rich-agent-message :deep(td){min-width:90px;padding:7px 9px;border:1px solid #bfd1dd;text-align:left;vertical-align:top}.rich-agent-message :deep(th){color:#224d69;background:#deedf5;font-weight:750}.rich-agent-message :deep(tr:nth-child(even) td){background:rgba(255,255,255,.56)}.rich-agent-message :deep(code){padding:2px 5px;border-radius:4px;color:#b13e58;background:#e5edf3;font:10px/1.5 Consolas,monospace}.rich-agent-message :deep(pre){margin:10px 0;padding:11px;overflow:auto;border-radius:8px;color:#dce9f2;background:#17364e}.rich-agent-message :deep(pre code){padding:0;color:inherit;background:transparent}.rich-agent-message :deep(a){color:#126fb5;text-decoration:none}.rich-agent-message :deep(a:hover){text-decoration:underline}.rich-agent-message :deep(img){display:block;width:min(100%,760px);max-height:620px;margin:12px auto;border:1px solid #c7dbe7;border-radius:12px;object-fit:contain;background:#f4f8fb;box-shadow:0 10px 28px rgba(20,63,91,.12)}.rich-agent-message :deep(.katex-display){margin:13px 0;padding:10px;overflow-x:auto;border:1px solid #d5e4ec;border-radius:8px;background:#f8fbfd}.rich-agent-message :deep(.katex){color:#173f60;font-size:1.05em}.rich-agent-message :deep(.jsxgraph-card){margin:14px 0;padding:10px;border:1px solid #bfd8e6;border-radius:11px;background:linear-gradient(145deg,#fbfeff,#eef7fb);box-shadow:0 8px 22px rgba(27,76,105,.08)}.rich-agent-message :deep(.jsxgraph-card header){margin-bottom:8px;color:#174e72;font-size:11px;font-weight:800}.rich-agent-message :deep(.jsxgraph-card small){display:block;margin-top:7px;color:#7890a0;font-size:8px}.rich-agent-message :deep(.jsxgraph-board){width:100%;height:420px;overflow:hidden;border:1px solid #c8dae5;border-radius:8px;background:#fff}.rich-agent-message :deep(.jsxgraph-error){height:auto;min-height:80px;padding:20px;box-sizing:border-box;color:#a43f3f;background:#fff4f4;font-size:10px}
</style>
