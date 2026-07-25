<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { X } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modelValue: boolean
  title: string
  eyebrow?: string
  description?: string
  size?: 'small' | 'medium' | 'large' | 'wide'
  closeOnBackdrop?: boolean
}>(), {
  eyebrow: 'WORKSPACE PANEL',
  description: '',
  size: 'medium',
  closeOnBackdrop: true,
})

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
let previousOverflow = ''

function close() {
  emit('update:modelValue', false)
}

function onKeydown(event: KeyboardEvent) {
  if (props.modelValue && event.key === 'Escape') close()
}

function updateBodyLock(open: boolean) {
  if (typeof document === 'undefined') return
  if (open) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return
  }
  const remaining = document.querySelectorAll('[data-floating-panel-layer]').length
  if (remaining <= 1) document.body.style.overflow = previousOverflow
}

watch(() => props.modelValue, updateBodyLock)
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  if (props.modelValue) updateBodyLock(true)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  if (props.modelValue) updateBodyLock(false)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="floating-panel">
      <div v-if="modelValue" class="floating-panel-layer" data-floating-panel-layer>
        <button v-if="closeOnBackdrop" class="floating-panel-backdrop" aria-label="关闭浮动窗口" @click="close" />
        <div v-else class="floating-panel-backdrop" />
        <section class="floating-panel-window" :class="`size-${size}`" role="dialog" aria-modal="true" :aria-label="title">
          <header>
            <div>
              <span>{{ eyebrow }}</span>
              <h2>{{ title }}</h2>
              <p v-if="description">{{ description }}</p>
            </div>
            <button class="floating-panel-close" aria-label="关闭" @click="close"><X :size="17" /></button>
          </header>
          <div class="floating-panel-body"><slot /></div>
          <footer v-if="$slots.footer"><slot name="footer" /></footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.floating-panel-layer{position:fixed;inset:0;z-index:950;display:grid;place-items:center;padding:28px}
.floating-panel-backdrop{position:absolute;inset:0;width:100%;height:100%;border:0;background:rgba(8,28,48,.48);backdrop-filter:blur(5px)}
.floating-panel-window{position:relative;display:grid;max-width:calc(100vw - 56px);max-height:calc(100vh - 56px);grid-template-rows:auto minmax(0,1fr) auto;overflow:hidden;border:1px solid #bdd4e5;border-radius:16px;background:#fff;box-shadow:0 30px 90px rgba(8,31,52,.32)}
.size-small{width:480px}.size-medium{width:680px}.size-large{width:900px}.size-wide{width:1120px}
.floating-panel-window>header{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:17px 20px;border-bottom:1px solid #dfe9f0;background:linear-gradient(120deg,#f8fbfe,#eef7fd)}
.floating-panel-window>header span{color:#3484bd;font-size:8px;font-weight:700;letter-spacing:1.45px}
.floating-panel-window>header h2{margin:4px 0 0;color:#173e63;font-size:17px}
.floating-panel-window>header p{margin:5px 0 0;color:#7890a5;font-size:9px;line-height:1.55}
.floating-panel-close{display:grid;width:32px;height:32px;flex:0 0 32px;padding:0;place-items:center;border:1px solid #ccdce8;border-radius:8px;color:#668096;background:#fff;cursor:pointer}
.floating-panel-close:hover{color:#1769c2;border-color:#8fbee0;background:#f1f8fd}
.floating-panel-body{min-height:0;padding:20px;overflow:auto;overscroll-behavior:contain}
.floating-panel-body :deep(.form-grid){padding:0}
.floating-panel-window>footer{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:13px 20px;border-top:1px solid #e0e9f0;background:#f8fbfd}
.floating-panel-enter-active,.floating-panel-leave-active{transition:opacity .18s ease}
.floating-panel-enter-active .floating-panel-window,.floating-panel-leave-active .floating-panel-window{transition:transform .18s ease}
.floating-panel-enter-from,.floating-panel-leave-to{opacity:0}
.floating-panel-enter-from .floating-panel-window,.floating-panel-leave-to .floating-panel-window{transform:translateY(12px) scale(.985)}
@media(max-width:720px){.floating-panel-layer{padding:0}.floating-panel-window{width:100vw;max-width:none;height:100vh;max-height:none;border-radius:0}.floating-panel-body{padding:15px}.floating-panel-window>header{padding:14px 15px}.floating-panel-window>footer{padding:12px 15px}}
</style>
