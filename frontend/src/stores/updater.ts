import { markRaw } from 'vue'
import { defineStore } from 'pinia'

export type UpdateInfo = {
  version: string
  currentVersion: string
  date?: string
  body?: string
}

const AUTO_CHECK_KEY = 'evoagent-update-auto-check'
const SKIPPED_VERSION_KEY = 'evoagent-update-skipped-version'
const UPDATE_HEADERS = {
  Accept: 'application/json',
  'User-Agent': 'EvoAgent-Desktop-Updater',
}

export const useUpdaterStore = defineStore('updater', {
  state: () => ({
    supported: false,
    checking: false,
    downloading: false,
    downloadedBytes: 0,
    totalBytes: 0,
    dialogOpen: false,
    update: null as UpdateInfo | null,
    error: '',
    sourceNote: '',
    lastCheckedAt: '',
    installedVersion: '2.1.1',
    automaticCheck: window.localStorage.getItem(AUTO_CHECK_KEY) !== 'false',
    skippedVersion: window.localStorage.getItem(SKIPPED_VERSION_KEY) || '',
    pendingUpdate: null as any,
  }),
  getters: {
    progressPercent(state): number {
      return state.totalBytes > 0 ? Math.min(100, Math.round(state.downloadedBytes / state.totalBytes * 100)) : 0
    },
  },
  actions: {
    setAutomaticCheck(value: boolean) {
      this.automaticCheck = value
      window.localStorage.setItem(AUTO_CHECK_KEY, String(value))
    },
    async loadInstalledVersion() {
      try {
        const { getVersion } = await import('@tauri-apps/api/app')
        this.installedVersion = await getVersion()
      } catch { /* Browser preview keeps the packaged fallback version. */ }
      return this.installedVersion
    },
    async loadSystemProxy() {
      try {
        const { invoke } = await import('@tauri-apps/api/core')
        return await invoke<string | null>('system_update_proxy')
      } catch { return null }
    },
    async checkWithNetworkPaths() {
      const { check } = await import('@tauri-apps/plugin-updater')
      const proxy = await this.loadSystemProxy()
      const paths = proxy ? [{ proxy }, {}] : [{}]
      let lastError: unknown = null
      for (const path of paths) {
        try {
          return await check({
            ...path,
            headers: UPDATE_HEADERS,
            timeout: 15_000,
          })
        } catch (error) {
          lastError = error
        }
      }
      throw lastError || new Error('官方更新源连接失败')
    },
    async check(manual = false) {
      if (this.checking || this.downloading) return this.update
      this.checking = true
      this.error = ''
      this.sourceNote = ''
      await this.loadInstalledVersion()
      try {
        this.supported = true
        const result = await this.checkWithNetworkPaths()
        this.lastCheckedAt = new Date().toISOString()
        if (!result) {
          this.update = null
          this.pendingUpdate = null
          this.sourceNote = `已核对官方更新清单，EvoAgent V${this.installedVersion} 是最新正式版本。`
          if (manual) this.dialogOpen = true
          return null
        }
        this.pendingUpdate = markRaw(result)
        this.update = {
          version: result.version,
          currentVersion: result.currentVersion || await this.loadInstalledVersion(),
          date: result.date || undefined,
          body: result.body || '本次发布未提供更新说明。',
        }
        if (manual || result.version !== this.skippedVersion) this.dialogOpen = true
        return this.update
      } catch (error:any) {
        this.update = null
        this.pendingUpdate = null
        const message = String(error?.message || error || '')
        if (/not found|not allowed|unknown command|__TAURI_INTERNALS__/i.test(message)) {
          this.supported = false
          this.error = '浏览器开发模式不执行桌面更新；请在 EvoAgent Windows 客户端中检查。'
        } else {
          this.supported = true
          const concise = message.replace(/^.*?:\s*/, '').slice(0, 180)
          this.error = `暂时无法连接官方更新源。已尝试 Windows 系统代理和直连${concise ? `：${concise}` : '，请检查网络或代理后重试。'}`
          this.sourceNote = ''
        }
        if (manual) this.dialogOpen = true
        return null
      } finally { this.checking = false }
    },
    skipCurrent() {
      if (this.update?.version) {
        this.skippedVersion = this.update.version
        window.localStorage.setItem(SKIPPED_VERSION_KEY, this.update.version)
      }
      this.dialogOpen = false
    },
    remindLater() { this.dialogOpen = false },
    async install() {
      if (!this.pendingUpdate || this.downloading) return
      this.downloading = true
      this.downloadedBytes = 0
      this.totalBytes = 0
      this.error = ''
      try {
        await this.pendingUpdate.downloadAndInstall((event:any) => {
          if (event.event === 'Started') this.totalBytes = Number(event.data?.contentLength || 0)
          if (event.event === 'Progress') this.downloadedBytes += Number(event.data?.chunkLength || 0)
          if (event.event === 'Finished' && this.totalBytes) this.downloadedBytes = this.totalBytes
        })
        const { relaunch } = await import('@tauri-apps/plugin-process')
        await relaunch()
      } catch (error:any) {
        this.error = `更新安装失败：${error?.message || error}`
      } finally { this.downloading = false }
    },
  },
})
