<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import katex from 'katex'
import { marked } from 'marked'
import 'katex/dist/katex.min.css'

const props = defineProps<{ preview: Record<string, any> | null, zoom?: number }>()
const emit = defineEmits<{ imageOpen: [payload: { src: string, alt: string }] }>()

function renderFormula(formula: string, displayMode: boolean) {
  return katex.renderToString(formula.trim(), {
    displayMode,
    throwOnError: false,
    strict: false,
    trust: false,
  })
}

function stripCommands(value: string) {
  return value
    .replace(/\\(?:textbf|textit|emph|underline|texttt|mathrm|mathbf)\s*\{([^{}]*)\}/g, '$1')
    .replace(/\\(?:label|centering|small|footnotesize|scriptsize|toprule|midrule|bottomrule)\b(?:\{[^}]*\})?/g, '')
    .replace(/\\%/g, '%').replace(/\\&/g, '&').replace(/~/g, ' ')
}

function tableHtml(source: string) {
  const tabular = source.match(/\\begin\{tabular\}\{[^}]*\}([\s\S]*?)\\end\{tabular\}/)?.[1] || ''
  const rows = tabular
    .replace(/\\(?:toprule|midrule|bottomrule|hline)\b/g, '')
    .split(/\\\\(?:\[[^\]]*\])?/)
    .map(row => row.trim())
    .filter(Boolean)
    .map(row => row.split(/(?<!\\)&/).map(cell => stripCommands(cell.trim())))
  if (!rows.length) return ''
  const caption = stripCommands(source.match(/\\caption\{([^}]*)\}/)?.[1] || '')
  const body = rows.map((row, index) => `<tr>${row.map(cell => `<${index === 0 ? 'th' : 'td'}>${cell}</${index === 0 ? 'th' : 'td'}>`).join('')}</tr>`).join('')
  return `<figure class="latex-table">${caption ? `<figcaption>${caption}</figcaption>` : ''}<div><table>${body}</table></div></figure>`
}

function renderText(value: string) {
  let source = String(value || '').replace(/(^|[^\\])%.*$/gm, '$1')
  const protectedBlocks: string[] = []
  const protect = (html: string) => `LATEXBLOCK${protectedBlocks.push(html) - 1}TOKEN`

  source = source.replace(/\\begin\{table\*?\}[\s\S]*?\\end\{table\*?\}/g, block => protect(tableHtml(block)))
  source = source.replace(/\\begin\{figure\*?\}([\s\S]*?)\\end\{figure\*?\}/g, (_block, body) => {
    const rawPath = body.match(/\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}/)?.[1] || ''
    const path = rawPath.replace(/^\.\//, '')
    const caption = stripCommands(body.match(/\\caption\{([^}]*)\}/)?.[1] || '')
    const asset = props.preview?.assets?.[path]
    const visual = asset
      ? `<img src="${asset}" alt="${caption || path}">`
      : `<div class="missing-figure">图片资源缺失：${path || '未解析路径'}</div>`
    return protect(`<figure class="latex-figure">${visual}${caption ? `<figcaption>${caption}</figcaption>` : ''}</figure>`)
  })
  source = source.replace(/\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}([\s\S]*?)\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}/g, (_match, formula) => protect(renderFormula(String(formula).replace(/\\label\{[^}]*\}/g, ''), true)))
  source = source.replace(/\\\[([\s\S]+?)\\\]|\$\$([\s\S]+?)\$\$/g, (_match, left, right) => protect(renderFormula(String(left || right), true)))
  source = source.replace(/(?<!\\)\$([^$\n]+?)\$/g, (_match, formula) => protect(renderFormula(String(formula), false)))

  source = source
    .replace(/\\begin\{(?:enumerate|itemize)\}/g, '\n')
    .replace(/\\end\{(?:enumerate|itemize)\}/g, '\n')
    .replace(/\\item\s+/g, '\n- ')
    .replace(/\\cite\w*\{([^}]*)\}/g, (_m, keys) => `[${String(keys).split(',').map((key:string) => key.trim()).join('；')}]`)
    .replace(/(?:Figure|Fig\.|Table|Section)?~?\\(?:ref|eqref|autoref)\{([^}]*)\}/g, '[$1]')
    .replace(/\\url\{([^}]*)\}/g, '[$1]($1)')
    .replace(/\\href\{([^}]*)\}\{([^}]*)\}/g, '[$2]($1)')
    .replace(/\\textbf\s*\{([^{}]*)\}/g, '**$1**')
    .replace(/\\(?:textit|emph)\s*\{([^{}]*)\}/g, '*$1*')
    .replace(/\\texttt\s*\{([^{}]*)\}/g, '`$1`')
    .replace(/\\(?:label|vspace|hspace)\s*\{[^}]*\}/g, '')
    .replace(/\\(?:centering|small|footnotesize|scriptsize|noindent)\b/g, '')
    .replace(/\\(?:begin|end)\{[^}]+\}/g, '')
    .replace(/\\(?:sep|quad|qquad)\b/g, ' · ')
    .replace(/\\%/g, '%').replace(/\\&/g, '&').replace(/~/g, ' ')

  let html = marked.parse(source, { async: false, breaks: true }) as string
  protectedBlocks.forEach((block, index) => { html = html.replaceAll(`LATEXBLOCK${index}TOKEN`, block) })
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] })
}

const authors = computed(() => props.preview?.authors || [])
const keywords = computed(() => props.preview?.keywords || [])
function handleImageClick(event: MouseEvent) {
  const target = event.target as HTMLImageElement
  if (target?.tagName === 'IMG' && target.closest('.latex-figure')) {
    emit('imageOpen', { src: target.src, alt: target.alt || '论文图片' })
  }
}
</script>

<template>
  <article class="latex-paper" :style="{ zoom: zoom || 1 }" @click="handleImageClick">
    <div v-if="preview?.warnings?.length" class="latex-warnings">
      <strong>项目检查</strong><span v-for="warning in preview.warnings" :key="warning">{{ warning }}</span>
    </div>
    <h1>{{ preview?.title || 'LaTeX 即时预览' }}</h1>
    <p v-if="authors.length" class="latex-authors">{{ authors.join('　·　') }}</p>
    <section v-if="preview?.abstract" class="latex-abstract">
      <h2>摘要</h2><div v-html="renderText(preview.abstract)" />
      <p v-if="keywords.length" class="latex-keywords"><b>关键词：</b>{{ keywords.join('；') }}</p>
    </section>
    <section v-for="(section,index) in preview?.sections || []" :key="`${section.title}-${index}`">
      <h2 v-if="section.level==='section'">{{ section.title }}</h2>
      <h3 v-else-if="section.level==='subsection'">{{ section.title }}</h3>
      <h4 v-else>{{ section.title }}</h4>
      <div class="latex-body" v-html="renderText(section.content)" />
    </section>
    <footer v-if="preview?.citations?.length">引用键：{{ preview.citations.join('；') }}</footer>
  </article>
</template>

<style scoped>
.latex-paper{width:min(100%,820px);min-height:960px;margin:0 auto;padding:62px 72px;color:#111;background:#fff;box-shadow:0 10px 34px rgba(28,52,74,.12);font-family:"Times New Roman","SimSun",serif;transform-origin:top center}.latex-paper h1{margin:0 0 12px;text-align:center;font-size:26px;line-height:1.3}.latex-authors{margin:0 0 28px;text-align:center;color:#333;font-size:13px}.latex-paper h2{margin:28px 0 12px;font-size:18px}.latex-paper h3{margin:20px 0 9px;font-size:15px}.latex-paper h4{margin:16px 0 8px;font-size:14px}.latex-body,.latex-abstract{font-size:14px;line-height:1.9;text-align:justify}.latex-abstract{padding:0 22px}.latex-abstract h2{text-align:center}.latex-keywords{margin-top:10px}.latex-paper :deep(p){margin:0 0 12px}.latex-paper :deep(ul),.latex-paper :deep(ol){margin:8px 0;padding-left:25px}.latex-paper :deep(.katex-display){margin:14px 0;overflow:auto}.latex-paper :deep(.latex-figure){margin:22px auto;text-align:center}.latex-paper :deep(.latex-figure img){max-width:100%;max-height:520px;object-fit:contain;cursor:zoom-in;transition:filter .16s,transform .16s}.latex-paper :deep(.latex-figure img:hover){filter:brightness(.97);transform:scale(1.01)}.latex-paper :deep(figcaption){margin:8px auto;color:#333;font-size:12px}.latex-paper :deep(.missing-figure){padding:28px;border:1px dashed #c88b68;color:#92552f;background:#fff8f2;font-family:"Microsoft YaHei",sans-serif;font-size:11px}.latex-paper :deep(.latex-table>div){overflow:auto}.latex-paper :deep(table){width:100%;border-collapse:collapse;font-size:11px}.latex-paper :deep(th),.latex-paper :deep(td){padding:5px 7px;border-top:1px solid #444;border-bottom:1px solid #777;text-align:center}.latex-paper footer{margin-top:40px;padding-top:14px;border-top:1px solid #bbb;color:#555;font-size:11px}.latex-warnings{margin:-38px -48px 26px;padding:10px 12px;display:flex;flex-wrap:wrap;gap:7px;border:1px solid #e2b665;color:#81530b;background:#fff8e9;font-family:"Microsoft YaHei",sans-serif;font-size:10px}.latex-warnings strong{margin-right:4px}.latex-warnings span{padding:2px 6px;border-radius:99px;background:#fff0ca}
</style>
