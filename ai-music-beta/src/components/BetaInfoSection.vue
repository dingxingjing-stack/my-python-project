<script setup>
import { ref } from 'vue'

const faqs = [
  { q: '公测期间有哪些限制？', a: '每日免费 10 次生成，单次最长 180 秒。无需注册即可使用，注册后额度升级至每日 20 次。' },
  { q: '生成的音乐可以商用吗？', a: '公测期间生成的音乐仅限个人测试用途。正式商用授权将在正式版上线后开放。' },
  { q: '支持哪些曲风和语言？', a: '支持流行、摇滚、电子、嘻哈、古典等 10 种以上曲风。歌词支持中文、英文及多语言混合。' },
  { q: '生成后如何下载？', a: '生成完成后页面会显示播放器，点击下载按钮即可保存为 MP3 格式。' },
  { q: '为什么生成失败了？', a: '请检查提示词是否超过 500 字，或后端服务可能暂时过载。请稍后重试，或尝试简化提示词。' },
  { q: '正式版什么时候发布？', a: '公测阶段预计持续 2-3 个月。正式版将开放无限生成、人声克隆、Remix 混音等专业功能。' },
]

const rules = [
  '禁止生成违法、色情、暴力内容',
  '禁止批量抓取、爬取生成的音频文件',
  '每日免费额度限单人使用，不可共享',
  '生成的音频版权归属以正式版协议为准',
  '服务可能因公测调整不定期暂停维护',
]

const openIdx = ref(null)
const toggle = (i) => { openIdx.value = openIdx.value === i ? null : i }
</script>

<template>
  <section id="faq" class="relative py-24 sm:py-32">
    <div class="max-w-3xl mx-auto px-4 sm:px-6">
      <div class="scroll-reveal text-center mb-14">
        <h2 class="text-3xl sm:text-4xl font-bold mb-4">公测说明</h2>
        <p class="text-white/50">了解公测规则与常见问题</p>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-3 gap-6 mb-16">
        <div class="scroll-reveal card-hover rounded-xl bg-surface-50 border border-white/5 p-6 text-center">
          <div class="w-10 h-10 rounded-xl bg-brand-500/10 flex items-center justify-center mx-auto mb-3">
            <span class="text-xl">🎵</span>
          </div>
          <div class="text-2xl font-bold text-white mb-1">10</div>
          <div class="text-sm text-white/40">每日免费生成</div>
        </div>
        <div class="scroll-reveal card-hover rounded-xl bg-surface-50 border border-white/5 p-6 text-center">
          <div class="w-10 h-10 rounded-xl bg-brand-500/10 flex items-center justify-center mx-auto mb-3">
            <span class="text-xl">⏱️</span>
          </div>
          <div class="text-2xl font-bold text-white mb-1">180s</div>
          <div class="text-sm text-white/40">单次最长时长</div>
        </div>
        <div class="scroll-reveal card-hover rounded-xl bg-surface-50 border border-white/5 p-6 text-center">
          <div class="w-10 h-10 rounded-xl bg-brand-500/10 flex items-center justify-center mx-auto mb-3">
            <span class="text-xl">🎤</span>
          </div>
          <div class="text-2xl font-bold text-white mb-1">10+</div>
          <div class="text-sm text-white/40">曲风可选</div>
        </div>
      </div>

      <!-- Usage rules -->
      <div class="scroll-reveal mb-12">
        <h3 class="text-lg font-semibold mb-4">使用须知</h3>
        <div class="rounded-xl bg-surface-50 border border-white/5 p-6">
          <ul class="space-y-3">
            <li v-for="(rule, i) in rules" :key="i" class="flex items-start gap-3 text-sm text-white/60">
              <span class="w-5 h-5 rounded-full bg-brand-500/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                <span class="text-[10px] text-brand-400 font-semibold">{{ i + 1 }}</span>
              </span>
              {{ rule }}
            </li>
          </ul>
        </div>
      </div>

      <!-- FAQ accordion -->
      <div class="scroll-reveal">
        <h3 class="text-lg font-semibold mb-4">常见问题</h3>
        <div class="space-y-2">
          <div
            v-for="(faq, i) in faqs"
            :key="i"
            class="rounded-xl bg-surface-50 border border-white/5 overflow-hidden transition"
          >
            <button
              @click="toggle(i)"
              class="flex items-center justify-between w-full px-6 py-4 text-left text-sm font-medium text-white/80 hover:text-white transition"
            >
              {{ faq.q }}
              <svg
                :class="{ 'rotate-180': openIdx === i }"
                class="w-4 h-4 text-white/40 transition-transform duration-300 flex-shrink-0"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
            <div
              :class="[
                'grid transition-all duration-300 ease-in-out',
                openIdx === i ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
              ]"
            >
              <div class="overflow-hidden">
                <p class="px-6 pb-4 text-sm text-white/40 leading-relaxed">{{ faq.a }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
