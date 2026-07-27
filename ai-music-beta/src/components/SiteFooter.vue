<script setup>
import { ref } from 'vue'

const year = ref(new Date().getFullYear())
</script>

<template>
  <footer class="relative border-t border-white/5 py-12">
    <div class="max-w-6xl mx-auto px-4 sm:px-6">
      <div class="flex flex-col sm:flex-row items-center justify-between gap-6">
        <div class="flex items-center gap-3">
          <div class="w-7 h-7 rounded-lg bg-gradient-to-br from-brand-400 to-purple-600 flex items-center justify-center">
            <svg class="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2z"/></svg>
          </div>
          <div>
            <div class="text-sm font-semibold text-white">Avireon <span class="text-brand-400 font-mono text-[10px]">Beta 0.1</span></div>
            <div class="text-xs text-white/30">&copy; {{ year }} Avireon. All rights reserved.</div>
          </div>
        </div>
        <div class="flex items-center gap-6 text-xs text-white/30">
          <a href="/privacy" class="hover:text-white/50 transition">隐私政策</a>
          <a href="/terms" class="hover:text-white/50 transition">服务条款</a>
          <a href="https://ai-music-backend-db6h.onrender.com/docs" class="hover:text-white/50 transition">API 文档</a>
        </div>
      </div>
      <div class="mt-8 text-center text-[10px] text-white/10">
        本服务由 AI 驱动生成，结果仅供参考，不构成专业建议
      </div>
    </div>
  </footer>
</template>
