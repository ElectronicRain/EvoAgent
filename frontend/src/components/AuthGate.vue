<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowRight, BrainCircuit, KeyRound, LockKeyhole, Sparkles, UserRound } from 'lucide-vue-next'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const mode = ref<'login'|'register'>(userStore.registrationRequired ? 'register' : 'login')
const username = ref('')
const displayName = ref('')
const password = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const error = ref('')
const heading = computed(() => mode.value === 'register' ? '创建本地账号' : '欢迎回来')

watch(() => userStore.registrationRequired, value => {
  if (value) mode.value = 'register'
})

function switchMode(next: 'login'|'register') {
  mode.value = next
  error.value = ''
}

async function submit() {
  error.value = ''
  if (mode.value === 'register' && password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  submitting.value = true
  try {
    if (mode.value === 'register') {
      await userStore.register(username.value.trim(), displayName.value.trim(), password.value)
    } else {
      await userStore.login(username.value.trim(), password.value)
    }
  } catch (cause: any) {
    error.value = cause?.message || '操作失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="auth-gate">
    <section class="auth-story">
      <div class="auth-brand"><Sparkles :size="22" /><strong>EvoAgent</strong><span>USER PERCEPTION</span></div>
      <div class="story-copy">
        <span class="eyebrow">LOCAL · PRIVATE · PERSONAL</span>
        <h1>让每一次对话，<br><em>更懂你的工作方式。</em></h1>
        <p>账号、用量、提问记忆和回复偏好均保存在本机。登录后，所有 Agent 会使用同一套个性化体验。</p>
      </div>
      <div class="story-features">
        <article><KeyRound :size="18" /><div><strong>本地身份</strong><span>独立账号与会话</span></div></article>
        <article><BrainCircuit :size="18" /><div><strong>持续感知</strong><span>从提问中生成画像</span></div></article>
        <article><LockKeyhole :size="18" /><div><strong>数据留在本地</strong><span>不依赖外部账号系统</span></div></article>
      </div>
    </section>

    <section class="auth-form-panel">
      <div class="auth-form">
        <div class="form-icon"><UserRound :size="23" /></div>
        <span class="eyebrow">{{ mode === 'register' ? 'FIRST STEP' : 'SIGN IN' }}</span>
        <h2>{{ heading }}</h2>
        <p>{{ mode === 'register' ? '首个账号会自动接管现有对话和历史用量。' : '继续使用你的 Agent、画像与回复偏好。' }}</p>

        <div class="auth-tabs">
          <button :class="{ active:mode==='login' }" @click="switchMode('login')">登录</button>
          <button :class="{ active:mode==='register' }" @click="switchMode('register')">注册</button>
        </div>
        <form @submit.prevent="submit">
          <label v-if="mode==='register'">显示名称<input v-model="displayName" autocomplete="name" placeholder="例如：张老师" required></label>
          <label>用户名<input v-model="username" autocomplete="username" placeholder="至少 3 个字符" required></label>
          <label>密码<input v-model="password" type="password" :autocomplete="mode==='register'?'new-password':'current-password'" placeholder="至少 8 个字符" required minlength="8"></label>
          <label v-if="mode==='register'">确认密码<input v-model="confirmPassword" type="password" autocomplete="new-password" placeholder="再次输入密码" required></label>
          <div v-if="error" class="auth-error">{{ error }}</div>
          <button class="submit" :disabled="submitting"><span>{{ submitting ? '正在处理…' : mode==='register' ? '创建账号并进入' : '登录工作台' }}</span><ArrowRight :size="17" /></button>
        </form>
        <small><LockKeyhole :size="12" />密码使用 PBKDF2 加盐哈希后保存在本地数据库中。</small>
      </div>
    </section>
  </main>
</template>

<style scoped>
.auth-gate{position:fixed;inset:0;z-index:2000;display:grid;grid-template-columns:minmax(520px,1.2fr) minmax(420px,.8fr);color:#173d5f;background:#eef5fa}.auth-story{position:relative;padding:48px 64px;display:flex;overflow:hidden;flex-direction:column;color:#fff;background:radial-gradient(circle at 80% 20%,rgba(61,197,213,.42),transparent 35%),linear-gradient(145deg,#063a66,#075487 58%,#087d95)}.auth-story:before,.auth-story:after{content:"";position:absolute;border:1px solid rgba(255,255,255,.13);border-radius:50%}.auth-story:before{width:520px;height:520px;right:-180px;bottom:-210px}.auth-story:after{width:290px;height:290px;right:-35px;bottom:-90px}.auth-brand{display:flex;align-items:center;gap:10px;position:relative;z-index:1}.auth-brand strong{font-size:17px}.auth-brand span{padding-left:10px;border-left:1px solid #ffffff55;font-size:9px;letter-spacing:2px;color:#b8eaf2}.story-copy{max-width:680px;margin:auto 0;position:relative;z-index:1}.eyebrow{display:block;margin-bottom:10px;color:#39b7d1;font-size:9px;font-weight:800;letter-spacing:2.2px}.auth-story .eyebrow{color:#88e7ef}.story-copy h1{margin:0;font-size:46px;line-height:1.18;letter-spacing:-2px}.story-copy h1 em{color:#7fe3ed;font-style:normal}.story-copy p{max-width:590px;margin:24px 0 0;color:#c5e2ed;font-size:13px;line-height:1.9}.story-features{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;position:relative;z-index:1}.story-features article{padding:14px;display:flex;align-items:center;gap:10px;border:1px solid #ffffff2f;border-radius:12px;background:#ffffff12;backdrop-filter:blur(8px)}.story-features article>svg{color:#7be4ee}.story-features article div{display:flex;flex-direction:column;gap:3px}.story-features strong{font-size:10px}.story-features span{font-size:8px;color:#b9dce7}.auth-form-panel{display:grid;place-items:center;padding:40px;background:linear-gradient(135deg,#f7fbfe,#edf4f9)}.auth-form{width:min(390px,100%)}.form-icon{width:48px;height:48px;margin-bottom:24px;display:grid;place-items:center;border-radius:14px;color:#fff;background:linear-gradient(135deg,#1769c2,#25a5bc);box-shadow:0 12px 28px #1769c244}.auth-form h2{margin:0;color:#153d60;font-size:26px}.auth-form>p{margin:8px 0 22px;color:#768b9d;font-size:10px;line-height:1.6}.auth-tabs{margin-bottom:18px;padding:4px;display:grid;grid-template-columns:1fr 1fr;border-radius:9px;background:#e3edf4}.auth-tabs button{height:34px;border:0;border-radius:7px;color:#6e8395;background:transparent;font-size:10px;font-weight:700}.auth-tabs button.active{color:#1769c2;background:#fff;box-shadow:0 3px 10px #214e7020}.auth-form form{display:grid;gap:12px}.auth-form label{display:grid;gap:6px;color:#42627d;font-size:9px;font-weight:700}.auth-form input{height:42px;padding:0 12px;border:1px solid #cbdbe7;border-radius:8px;outline:0;color:#183d5b;background:#fff;font-size:11px}.auth-form input:focus{border-color:#5da1d7;box-shadow:0 0 0 3px #3e93d51a}.submit{height:44px;margin-top:5px;padding:0 14px;border:0;border-radius:8px;display:flex;align-items:center;justify-content:space-between;color:#fff;background:linear-gradient(120deg,#1769c2,#178eaa);font-size:11px;font-weight:800;box-shadow:0 10px 24px #1769c233}.submit:disabled{opacity:.6}.auth-error{padding:9px;border-radius:7px;color:#a33d3d;background:#fff0f0;font-size:9px}.auth-form>small{margin-top:16px;display:flex;align-items:center;gap:5px;color:#8698a8;font-size:8px;line-height:1.5}@media(max-width:900px){.auth-gate{grid-template-columns:1fr}.auth-story{display:none}.auth-form-panel{padding:24px}}
</style>
