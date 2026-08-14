<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import type { Entity } from '../services/api'
const props=defineProps<{ figure:Entity }>()
const emit=defineEmits<{ download:[figure:Entity] }>()
const safeSvg=computed(()=>DOMPurify.sanitize(String(props.figure?.svg||''),{USE_PROFILES:{svg:true,svgFilters:true}}))
</script>
<template>
  <article class="publication-figure">
    <header><div><strong>{{ figure.title || '论文图表' }}</strong><small>{{ figure.spec?.chart_type }} · {{ figure.spec?.journal }} · 矢量 SVG</small></div><button @click="emit('download',figure)">下载 SVG</button></header>
    <div class="svg-stage" v-html="safeSvg" />
    <section><p><b>图型选择：</b>{{ figure.spec?.reason }}</p><p><b>建议图注：</b>{{ figure.caption_template }}</p><p v-if="figure.spec?.warnings?.length" class="warning">{{ figure.spec.warnings.join('；') }}</p><div><span v-for="item in figure.spec?.quality_checks||[]" :key="item">{{ item }}</span></div></section>
  </article>
</template>
<style scoped>
.publication-figure{border:1px solid #d8e3eb;border-radius:10px;background:#fff;overflow:hidden}.publication-figure>header{padding:10px 12px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e5ecf1}.publication-figure strong,.publication-figure small{display:block}.publication-figure strong{color:#294a62;font-size:10px}.publication-figure small{margin-top:3px;color:#748a98;font-size:7px}.publication-figure button{padding:6px 9px;border:1px solid #91bad3;border-radius:6px;color:#1769c2;background:#fff;font-size:8px}.svg-stage{padding:8px;overflow:auto;background:#fff}.svg-stage :deep(svg){width:100%;min-width:620px;height:auto;display:block}.publication-figure>section{padding:10px 12px;border-top:1px solid #e5ecf1}.publication-figure p{margin:4px 0;color:#536d7e;font-size:8px;line-height:1.55}.publication-figure .warning{color:#9b5c14}.publication-figure>section>div{margin-top:7px;display:flex;gap:5px;flex-wrap:wrap}.publication-figure>section span{padding:3px 6px;border:1px solid #d7e3ea;border-radius:5px;color:#4d6f84;font-size:7px;background:#f8fafb}
</style>
