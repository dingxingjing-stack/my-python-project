<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const scrolled = ref(false)
const betaBanner = ref(true)

let handler = null
onMounted(() => {
  handler = () => { scrolled.value = window.scrollY > 20 }
  window.addEventListener('scroll', handler, { passive: true })
})
onUnmounted(() => {
  if (handler) window.removeEventListener('scroll', handler)
})
</script>

<template>
  <!-- Beta banner -->
  <div v-if="betaBanner" class="relative z-50 bg-gradient-to-r from-brand-600 via-purple-600 to-brand-500 text-white text-center text-xs sm:text-sm py-2 px-4">
    <span class="font-medium"> 公测阶段 — 每日免费 10 次生成，无需注册 </span>
    <button @click="betaBanner = false" class="absolute right-3 top-1/2 -translate-y-1/2 opacity-60 hover:opacity-100 transition">
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
    </button>
  </div>

  <!-- Navbar -->
  <nav :class="[
    'fixed top-0 left-0 right-0 z-40 transition-all duration-500',
    scrolled || !betaBanner
      ? 'glass bg-black/70 border-b border-white/5'
      : 'bg-transparent'
  ]" :style="{ top: betaBanner ? '40px' : '0' }">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-400 to-purple-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2z"/></svg>
        </div>
        <span class="text-lg font-bold tracking-tight bg-gradient-to-r from-white to-white/70 bg-clip-text text-transparent">Avireon</span>
        <span class="hidden sm:inline-flex text-[10px] font-semibold uppercase tracking-widest text-brand-400 border border-brand-500/30 rounded-full px-2.5 py-0.5">Beta</span>
      </div>

      <div class="flex items-center gap-4">
        <a href="#generator" class="hidden sm:inline-flex text-sm text-white/60 hover:text-white transition">立即体验</a>
        <a href="#faq" class="hidden sm:inline-flex text-sm text-white/60 hover:text-white transition">常见问题</a>
        <a href="/login" class="text-sm text-white/80 hover:text-white transition">登录</a>
        <a href="/register" class="text-sm font-medium bg-brand-600 hover:bg-brand-500 text-white px-4 py-2 rounded-lg transition btn-press">开始使用</a>
      </div>
    </div>
  </nav>
</template>
