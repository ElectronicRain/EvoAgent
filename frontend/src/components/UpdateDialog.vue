<script setup lang="ts">
import { computed } from 'vue'
import { AlertCircle, CheckCircle2, Download, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import FloatingPanel from './FloatingPanel.vue'
import { useUpdaterStore } from '../stores/updater'

const updater = useUpdaterStore()
const model = computed({ get:()=>updater.dialogOpen, set:value=>{updater.dialogOpen=value} })
const dateText = computed(() => updater.update?.date ? new Date(updater.update.date).toLocaleString('zh-CN') : '未提供')
</script>

<template>
  <FloatingPanel v-model="model" title="软件更新" eyebrow="EVOAGENT UPDATE" description="更新包需通过 EvoAgent 发布签名验证，是否安装始终由你决定。" size="medium" :close-on-backdrop="!updater.downloading">
    <div v-if="updater.checking" class="update-state"><LoaderCircle class="spin"/><strong>正在检查新版本…</strong><span>正在连接官方 GitHub Release 更新源。</span></div>
    <div v-else-if="updater.update" class="update-content">
      <section class="version-line"><div><small>当前版本</small><strong>V{{ updater.update.currentVersion }}</strong></div><i>→</i><div class="latest"><small>可用版本</small><strong>V{{ updater.update.version }}</strong></div></section>
      <div class="release-meta"><span>发布日期：{{ dateText }}</span><span><ShieldCheck :size="13"/>签名验证后安装</span></div>
      <section class="release-notes"><header>本次更新内容</header><pre>{{ updater.update.body }}</pre></section>
      <div v-if="updater.downloading" class="download-progress"><div><span>正在下载并验证更新包</span><b>{{ updater.progressPercent }}%</b></div><progress :value="updater.downloadedBytes" :max="updater.totalBytes || 1"/><small>完成后软件将自动重启；本地项目和数据库不会被覆盖。</small></div>
      <p v-if="updater.error" class="update-error">{{ updater.error }}</p>
    </div>
    <div v-else class="update-state" :class="{failed:updater.error}"><AlertCircle v-if="updater.error"/><CheckCircle2 v-else/><strong>{{ updater.error ? '暂时无法检查更新' : '当前已是最新版本' }}</strong><span>{{ updater.error || updater.sourceNote || `EvoAgent V${updater.installedVersion} 暂无可用更新。` }}</span></div>
    <template #footer>
      <template v-if="updater.update">
        <button class="btn" :disabled="updater.downloading" @click="updater.skipCurrent">忽略此版本</button>
        <button class="btn" :disabled="updater.downloading" @click="updater.remindLater">稍后提醒</button>
        <button class="btn btn-primary" :disabled="updater.downloading" @click="updater.install"><LoaderCircle v-if="updater.downloading" class="spin" :size="14"/><Download v-else :size="14"/>{{ updater.downloading?'正在更新':'立即更新并重启' }}</button>
      </template>
      <button v-else class="btn" :disabled="updater.checking" @click="updater.check(true)"><RefreshCw :size="14"/>重新检查</button>
    </template>
  </FloatingPanel>
</template>

<style scoped>
.update-state{min-height:210px;display:grid;place-content:center;justify-items:center;gap:9px;color:#2a83b2}.update-state svg{width:34px;height:34px}.update-state strong{color:#244d6c;font-size:15px}.update-state span{color:#8195a4;font-size:9px}.update-content{display:grid;gap:14px}.version-line{padding:18px 24px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:20px;border-radius:12px;background:linear-gradient(120deg,#edf6fc,#f7fbfe)}.version-line>div{display:grid;gap:5px}.version-line small{color:#7c91a1;font-size:8px}.version-line strong{color:#4e6b80;font-size:18px}.version-line i{color:#73a6c7;font-style:normal}.version-line .latest strong{color:#1769c2}.release-meta{display:flex;justify-content:space-between;color:#718798;font-size:8px}.release-meta span{display:flex;align-items:center;gap:5px}.release-meta span:last-child{color:#16805f}.release-notes{overflow:hidden;border:1px solid #dce7ee;border-radius:10px}.release-notes header{padding:9px 12px;color:#315873;background:#f4f8fb;font-size:9px;font-weight:800}.release-notes pre{max-height:260px;margin:0;padding:13px;overflow:auto;white-space:pre-wrap;color:#536d7f;background:#fff;font:9px/1.75 system-ui}.download-progress{padding:12px;border-radius:9px;background:#eef7fd}.download-progress>div{display:flex;justify-content:space-between;color:#315d7c;font-size:9px}.download-progress progress{width:100%;height:7px;margin:9px 0}.download-progress small{color:#7f92a1;font-size:7px}.update-error{margin:0;color:#a64646;font-size:8px}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.update-state.failed{color:#bd654f}.update-state span{max-width:440px;line-height:1.7;text-align:center}
</style>
