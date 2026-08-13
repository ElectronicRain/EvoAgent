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
const LATEST_RELEASE_API = 'https://api.github.com/repos/ElectronicRain/EvoAgent/releases/latest'

function compareVersions(left: string, right: string) {
  const normalize = (value: string) => value.replace(/^v/i, '').split(/[.+-]/).slice(0, 3).map(part => Number(part) || 0)
  const [a, b] = [normalize(left), normalize(right)]
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1
  }
  return 0
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
    installedVersion: '2.0.0',
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
    async checkReleaseFallback() {
      try {
        const response = await fetch(LATEST_RELEASE_API, {
          cache: 'no-store',
          headers: { Accept: 'application/vnd.github+json' },
        })
        this.lastCheckedAt = new Date().toISOString()
        if (response.status === 404) {
          this.error = ''
          this.sourceNote = `GitHub 当前尚未发布更高版本；EvoAgent V${this.installedVersion} 已是最新正式版本。`
          return true
        }
        if (!response.ok) return false
        const release = await response.json()
        const latestVersion = String(release?.tag_name || release?.name || '').replace(/^v/i, '')
        if (!latestVersion) return false
        if (compareVersions(latestVersion, this.installedVersion) <= 0) {
          this.error = ''
          this.sourceNote = `已核对 GitHub Release，EvoAgent V${this.installedVersion} 是最新正式版本。`
        } else {
          this.error = `发现正式版本 V${latestVersion}，但发布方尚未上传可验证的桌面更新清单。请稍后重试。`
          this.sourceNote = ''
        }
        return true
      } catch { return false }
    },
    async check(manual = false) {
      if (this.checking || this.downloading) return this.update
      this.checking = true
      this.error = ''
      this.sourceNote = ''
      await this.loadInstalledVersion()
      try {
        const { check } = await import('@tauri-apps/plugin-updater')
        this.supported = true
        const result = await check({ timeout: 20_000 })
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
          // Tauri/WebView2 会因系统语言、TLS 和插件版本返回不同错误文本，
          // 因此桌面更新源失败后统一进入 GitHub Release 二次核验，不依赖错误字符串。
          const handled = await this.checkReleaseFallback()
          if (handled) {
            if (manual) this.dialogOpen = true
            return null
          }
          this.error = ''
          this.sourceNote = `EvoAgent V${this.installedVersion} 是当前安装包标记的最新正式版本；本次未能连接 GitHub 发布源，联网后可重新核验。`
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
