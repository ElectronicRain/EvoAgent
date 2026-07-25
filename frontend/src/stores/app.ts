import { defineStore } from 'pinia'
import { API_ORIGIN } from '../services/api'

export const useAppStore = defineStore('app', {
  state: () => ({
    backendOnline: false,
    loadingCount: 0,
    toast: null as null | { type: 'success' | 'error' | 'info'; message: string },
  }),
  actions: {
    loading(value: boolean) { this.loadingCount += value ? 1 : -1; if (this.loadingCount < 0) this.loadingCount = 0 },
    notify(message: string, type: 'success' | 'error' | 'info' = 'success') {
      this.toast = { type, message }
      window.setTimeout(() => { this.toast = null }, 3200)
    },
    async checkBackend(): Promise<boolean> {
      try {
        const response = await fetch(`${API_ORIGIN}/health`)
        this.backendOnline = response.ok
      } catch { this.backendOnline = false }
      return this.backendOnline
    },
    async waitForBackend(attempts = 30, delayMs = 500): Promise<boolean> {
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        if (await this.checkBackend()) return true
        await new Promise(resolve => window.setTimeout(resolve, delayMs))
      }
      return false
    },
  },
})
