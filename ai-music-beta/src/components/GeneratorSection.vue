<script setup>
import { ref, watch } from 'vue'

const props = defineProps({ isGenerating: Boolean })
const emit = defineEmits(['generate'])

const prompt = ref('')
const style = ref('pop')
const duration = ref(30)
const charsLeft = ref(500)

const styles = [
  { value: 'pop', label: '流行' },
  { value: 'rock', label: '摇滚' },
  { value: 'electronic', label: '电子' },
  { value: 'hiphop', label: '嘻哈' },
  { value: 'rnb', label: 'R&B' },
  { value: 'classical', label: '古典' },
  { value: 'jazz', label: '爵士' },
  { value: 'acoustic', label: '民谣' },
  { value: 'lofi', label: 'Lofi' },
  { value: 'cinematic', label: '电影配乐' },
]

watch(prompt, (v) => { charsLeft.value = 500 - (v?.length || 0) })

const handleSubmit = () => {
  if (!prompt.value || prompt.value.trim().length < 5) return alert('提示词至少 5 个字符')
  emit('generate', {
    prompt: prompt.value.trim(),
    style: style.value,
    duration: duration.value,
    type: 'song',
  })
}
</script>

<template>
  <section id="generator" class="relative py-24 sm:py-32">
    <div class="max-w-3xl mx-auto px-4 sm:px-6">
      <div class="scroll-reveal text-center mb-12">
        <h2 class="text-3xl sm:text-4xl font-bold mb-4">开始创作</h2>
        <p class="text-white/50">输入灵感描述，选择曲风与时长，一键生成</p>
      </div>

      <div class="scroll-reveal rounded-2xl bg-surface-50 border border-white/5 p-6 sm:p-8 glow-border">
        <!-- Prompt -->
        <div class="mb-6">
          <label class="block text-sm font-medium text-white/70 mb-2">音乐描述</label>
          <textarea
            v-model="prompt"
            :disabled="isGenerating"
            rows="4"
            maxlength="500"
            placeholder="例如：一首轻快的流行歌曲，关于夏日的海滩和阳光，带钢琴伴奏..."
            class="w-full bg-surface border border-white/10 rounded-xl px-4 py-3.5 text-white placeholder:text-white/20 text-sm resize-none transition focus:outline-none focus:border-brand-500/50 focus:ring-1 focus:ring-brand-500/20 disabled:opacity-50"
          />
          <div class="flex justify-between mt-1.5 text-xs text-white/30">
            <span v-if="prompt.length < 5">至少 5 个字符</span>
            <span v-else />
            <span :class="{ 'text-amber-400': charsLeft < 50 }">{{ charsLeft }}</span>
          </div>
        </div>

        <!-- Style & Duration -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-6 mb-8">
          <div>
            <label class="block text-sm font-medium text-white/70 mb-3">曲风</label>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="s in styles"
                :key="s.value"
                :disabled="isGenerating"
                @click="style = s.value"
                :class="[
                  'px-3.5 py-1.5 rounded-lg text-xs font-medium transition border btn-press',
                  style === s.value
                    ? 'bg-brand-600/20 text-brand-300 border-brand-500/40'
                    : 'bg-white/5 text-white/50 border-white/10 hover:bg-white/10'
                ]"
              >{{ s.label }}</button>
            </div>
          </div>
          <div>
            <label class="block text-sm font-medium text-white/70 mb-3">
              时长：<span class="text-brand-300 font-semibold">{{ duration }}秒</span>
            </label>
            <input
              type="range"
              v-model.number="duration"
              :disabled="isGenerating"
              min="15"
              max="180"
              step="15"
              class="w-full h-1.5 bg-white/10 rounded-full appearance-none cursor-pointer accent-brand-500 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-brand-500 [&::-webkit-slider-thumb]:shadow-lg [&::-webkit-slider-thumb]:shadow-brand-500/30"
            />
            <div class="flex justify-between text-xs text-white/30 mt-1.5">
              <span>15s</span>
              <span>180s</span>
            </div>
          </div>
        </div>

        <!-- Generate button -->
        <button
          @click="handleSubmit"
          :disabled="isGenerating || prompt.trim().length < 5"
          class="relative w-full py-3.5 rounded-xl text-base font-semibold text-white bg-gradient-to-r from-brand-600 to-purple-600 hover:from-brand-500 hover:to-purple-500 transition shadow-lg shadow-brand-600/25 disabled:opacity-40 disabled:cursor-not-allowed btn-press overflow-hidden"
        >
          <span v-if="!isGenerating" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            AI 生成音乐
          </span>
          <span v-else class="flex items-center justify-center gap-3">
            <span class="flex gap-1">
              <span class="w-2 h-2 bg-white/80 rounded-full animate-[pulse-dot_1.4s_ease-in-out_infinite_both]" />
              <span class="w-2 h-2 bg-white/80 rounded-full animate-[pulse-dot_1.4s_ease-in-out_infinite_both]" style="animation-delay: 0.16s" />
              <span class="w-2 h-2 bg-white/80 rounded-full animate-[pulse-dot_1.4s_ease-in-out_infinite_both]" style="animation-delay: 0.32s" />
            </span>
            AI 正在生成中...
          </span>
        </button>
      </div>
    </div>
  </section>
</template>
