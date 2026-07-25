import { defineStore } from 'pinia'
import { api, type Entity } from '../services/api'

const TOKEN_KEY = 'evoagent-auth-token'

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null as Entity | null,
    ready: false,
    registrationRequired: false,
  }),
  actions: {
    async bootstrap() {
      try {
        const status = await api.get<Entity>('/auth/status')
        this.user = status.user || null
        this.registrationRequired = Boolean(status.registration_required)
      } catch {
        this.user = null
      } finally {
        this.ready = true
      }
    },
    acceptAuth(result: Entity) {
      window.localStorage.setItem(TOKEN_KEY, result.token)
      this.user = result.user
      this.registrationRequired = false
      this.ready = true
    },
    async login(username: string, password: string) {
      const result = await api.post<Entity>('/auth/login', { username, password })
      this.acceptAuth(result)
    },
    async register(username: string, displayName: string, password: string) {
      const result = await api.post<Entity>('/auth/register', {
        username,
        display_name: displayName,
        password,
      })
      this.acceptAuth(result)
    },
    async refresh() {
      this.user = await api.get<Entity>('/auth/me')
    },
    async logout() {
      try { await api.post('/auth/logout') } catch { /* Local logout still applies. */ }
      window.localStorage.removeItem(TOKEN_KEY)
      this.user = null
    },
  },
})
