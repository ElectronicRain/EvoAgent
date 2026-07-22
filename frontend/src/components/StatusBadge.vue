<script setup lang="ts">
const props = defineProps<{ status: string }>()
const labels: Record<string, string> = {
  active: '已启用', completed: '已完成', healthy: '健康', ready: '就绪', approved: '已批准',
  pending: '待审批', running: '执行中', evaluated: '已评测', candidate: '候选版本', draft: '草稿',
  failed: '失败', unhealthy: '异常', rejected: '已拒绝', archived: '已归档', unknown: '未检测',
}
const tone = (status: string) => ['active','completed','healthy','ready','approved'].includes(status)
  ? 'success' : ['failed','unhealthy','rejected'].includes(status) ? 'danger'
  : ['pending','running','evaluated','candidate'].includes(status) ? 'warning' : 'neutral'
</script>

<template><span class="status-badge" :class="`status-${tone(props.status)}`"><i />{{ labels[status] || status }}</span></template>
