<script setup lang="ts">
defineProps<{ title: string; description: string; eyebrow?: string }>()
</script>

<template>
  <header class="page-header">
    <div>
      <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
    <div v-if="$slots.actions || $slots.default" class="page-actions">
      <slot name="actions"><slot /></slot>
    </div>
  </header>
</template>
