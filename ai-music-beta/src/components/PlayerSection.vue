<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'

const props = defineProps({ audioUrl: String })

const playing = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const audioRef = ref(null)
const canvasRef = ref(null)
const showDownload = ref(false)

let audioCtx = null
let analyser = null
let source = null
let animId = null
let dataArray = null

const formatTime = (s) => {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

const drawWaveform = () => {
  if (!canvasRef.value || !analyser) return
  const canvas = canvasRef.value
  const ctx = canvas.getContext('2d')
  const w = canvas.width
  const h = canvas.height

  if (!playing.value) {
    // Static gradient bar when paused
    ctx.clearRect(0, 0, w, h)
    const bars = 64
    const gap = 2
    const barW = (w - gap * bars) / bars
    for (let i = 0; i < bars; i++) {
      const x = i * (barW + gap)
      const barH = 4 + Math.sin(i * 0.3) * 8
      const y = (h - barH) / 2
      ctx.fillStyle = `rgba(99, 102, 241, ${0.2 + Math.sin(i * 0.2) * 0.1})`
      ctx.beginPath()
      ctx.roundRect(x, y, barW, barH, 2)
      ctx.fill()
    }
    return
  }

  // Real-time waveform
  const draw = () => {
    if (!analyser) return
    analyser.getByteFrequencyData(dataArray)
    ctx.clearRect(0, 0, w, h)

    const bars = dataArray.length
    const gap = 2
    const barW = (w - gap * bars) / bars

    for (let i = 0; i < bars; i++) {
      const val = dataArray[i] / 255
      const barH = Math.max(2, val * h * 0.9)
      const x = i * (barW + gap)
      const y = (h - barH) / 2
      const hue = 240 + val * 20
      ctx.fillStyle = `hsla(${hue}, 70%, ${55 + val * 20}%, ${0.7 + val * 0.3})`
      ctx.beginPath()
      ctx.roundRect(x, y, barW, barH, 2)
      ctx.fill()
    }
    animId = requestAnimationFrame(draw)
  }
  draw()
}

const initAudio = async () => {
  const audio = audioRef.value
  if (!audio) return

  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)()
  }
  if (audioCtx.state === 'suspended') await audioCtx.resume()

  if (!source) {
    source = audioCtx.createMediaElementSource(audio)
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = 128
    dataArray = new Uint8Array(analyser.frequencyBinCount)
    source.connect(analyser)
    analyser.connect(audioCtx.destination)
  }
}

const togglePlay = async () => {
  const audio = audioRef.value
  if (!audio) return

  if (playing.value) {
    audio.pause()
    if (animId) cancelAnimationFrame(animId)
    playing.value = false
    return
  }

  try {
    await initAudio()
    await audio.play()
    playing.value = true
    nextTick(drawWaveform)
  } catch (e) {
    // autoplay blocked - user needs to interact
  }
}

const handleTimeUpdate = () => {
  const audio = audioRef.value
  if (audio) {
    currentTime.value = audio.currentTime
    duration.value = audio.duration || 0
  }
}

const handleEnded = () => {
  playing.value = false
  if (animId) cancelAnimationFrame(animId)
  currentTime.value = 0
}

const seek = (e) => {
  const audio = audioRef.value
  if (!audio) return
  const rect = e.currentTarget.getBoundingClientRect()
  const pct = (e.clientX - rect.left) / rect.width
  audio.currentTime = pct * (audio.duration || 0)
}

const handleDownload = () => {
  const a = document.createElement('a')
  a.href = props.audioUrl
  a.download = 'avireon-music.mp3'
  a.click()
}

watch(() => props.audioUrl, () => {
  playing.value = false
  currentTime.value = 0
  duration.value = 0
  if (animId) cancelAnimationFrame(animId)
  if (source) {
    source.disconnect()
    source = null
  }
  showDownload.value = true
  nextTick(() => {
    const canvas = canvasRef.value
    if (canvas) drawWaveform()
  })
})

onUnmounted(() => {
  if (animId) cancelAnimationFrame(animId)
  if (source) { source.disconnect(); source = null }
  if (audioCtx) { audioCtx.close(); audioCtx = null }
})
</script>

<template>
  <section v-if="audioUrl" id="player" class="relative py-16">
    <div class="max-w-2xl mx-auto px-4 sm:px-6">
      <div class="scroll-reveal rounded-2xl bg-surface-50 border border-white/5 p-6 sm:p-8 glow-border">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-sm font-medium text-white/70">生成结果</h3>
          <span class="text-xs text-white/30">AI 生成音频</span>
        </div>

        <!-- Waveform canvas -->
        <div class="relative mb-4">
          <canvas
            ref="canvasRef"
            class="w-full h-24 rounded-lg bg-black/20 cursor-pointer"
            :class="{ 'opacity-80': !playing }"
            @click="togglePlay"
          />
          <div
            v-if="playing"
            class="absolute bottom-2 left-2 text-[10px] text-white/40 font-mono"
          >{{ formatTime(currentTime) }} / {{ formatTime(duration) }}</div>
        </div>

        <!-- Controls -->
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <button @click="togglePlay" class="w-10 h-10 rounded-full bg-brand-600 hover:bg-brand-500 flex items-center justify-center transition btn-press shadow-lg shadow-brand-600/20">
              <svg v-if="!playing" class="w-5 h-5 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
              <svg v-else class="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 24 24"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>
            </button>
            <span class="text-xs text-white/40 font-mono">{{ formatTime(currentTime) }} / {{ formatTime(duration) }}</span>
          </div>
          <button
            v-if="showDownload"
            @click="handleDownload"
            class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-white/70 bg-white/5 hover:bg-white/10 transition border border-white/10 btn-press"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
            下载 MP3
          </button>
        </div>

        <!-- Progress bar -->
        <div
          @click="seek"
          class="relative mt-4 h-1 bg-white/10 rounded-full cursor-pointer group"
        >
          <div
            class="absolute left-0 top-0 h-full bg-gradient-to-r from-brand-500 to-purple-500 rounded-full transition-all duration-200"
            :style="{ width: duration ? `${(currentTime / duration) * 100}%` : '0%' }"
          />
        </div>

        <!-- Hidden audio element -->
        <audio
          ref="audioRef"
          :src="audioUrl"
          preload="auto"
          @timeupdate="handleTimeUpdate"
          @ended="handleEnded"
          @loadedmetadata="handleTimeUpdate"
        />
      </div>
    </div>
  </section>
</template>
