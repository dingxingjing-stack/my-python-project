<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const displayText = ref('')
const fullText = '描述你的音乐灵感，AI 在数分钟内为你生成完整歌曲。'

let timer = null
let animId = null
let particles = []

onMounted(() => {
  let i = 0
  timer = setInterval(() => {
    if (i < fullText.length) {
      displayText.value += fullText[i]
      i++
    } else {
      clearInterval(timer)
    }
  }, 50)

  const canvas = document.getElementById('hero-canvas')
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  let w, h

  const resize = () => {
    w = canvas.width = window.innerWidth
    h = canvas.height = window.innerHeight
  }
  resize()
  window.addEventListener('resize', resize)

  const count = Math.min(80, Math.floor(w * h / 15000))
  particles = Array.from({ length: count }, () => ({
    x: Math.random() * w,
    y: Math.random() * h,
    vx: (Math.random() - 0.5) * 0.5,
    vy: (Math.random() - 0.5) * 0.5,
    r: Math.random() * 2 + 0.5,
    a: Math.random() * 0.4 + 0.1,
  }))

  const draw = () => {
    ctx.clearRect(0, 0, w, h)
    for (const p of particles) {
      p.x += p.vx
      p.y += p.vy
      if (p.x < 0) p.x = w
      if (p.x > w) p.x = 0
      if (p.y < 0) p.y = h
      if (p.y > h) p.y = 0
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
      ctx.fillStyle = `rgba(165, 180, 252, ${p.a})`
      ctx.fill()
    }
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x
        const dy = particles[i].y - particles[j].y
        const dist = Math.sqrt(dx * dx + dy * dy)
        if (dist < 120) {
          ctx.beginPath()
          ctx.moveTo(particles[i].x, particles[i].y)
          ctx.lineTo(particles[j].x, particles[j].y)
          ctx.strokeStyle = `rgba(165, 180, 252, ${0.06 * (1 - dist / 120)})`
          ctx.stroke()
        }
      }
    }
    animId = requestAnimationFrame(draw)
  }
  draw()
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (animId) cancelAnimationFrame(animId)
  particles = []
})
</script>

<template>
  <section class="relative min-h-screen flex items-center justify-center overflow-hidden pt-24 pb-16">
    <!-- Particle canvas background -->
    <canvas id="hero-canvas" class="absolute inset-0 w-full h-full pointer-events-none" />

    <!-- Subtle gradient orbs -->
    <div class="absolute top-1/4 -left-32 w-96 h-96 bg-brand-500/10 rounded-full blur-[120px]" />
    <div class="absolute bottom-1/4 -right-32 w-80 h-80 bg-purple-500/10 rounded-full blur-[120px]" />

    <div class="relative z-10 max-w-4xl mx-auto px-4 text-center">
      <!-- Badge -->
      <div class="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white/5 border border-white/10 text-sm text-white/60 mb-8 animate-fade-up">
        <span class="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
        公测进行中 — 每日免费额度
      </div>

      <!-- Title -->
      <h1 class="text-4xl sm:text-5xl md:text-7xl font-bold tracking-tight leading-[1.1] mb-6 animate-fade-up animate-fade-up-delay-1 glow-text">
        <span class="bg-gradient-to-r from-white via-white to-white/60 bg-clip-text text-transparent">
          AI 音乐生成
        </span>
        <br />
        <span class="bg-gradient-to-r from-brand-300 via-brand-400 to-purple-400 bg-clip-text text-transparent">
          公测版
        </span>
      </h1>

      <!-- Typewriter subtitle -->
      <p class="text-base sm:text-lg text-white/50 max-w-2xl mx-auto mb-10 min-h-[1.8em] animate-fade-up animate-fade-up-delay-2">
        <span class="typewriter-cursor">{{ displayText }}</span>
      </p>

      <!-- CTA -->
      <div class="flex flex-col sm:flex-row items-center justify-center gap-4 animate-fade-up animate-fade-up-delay-3">
        <a href="#generator" class="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl text-base font-semibold text-white bg-brand-600 hover:bg-brand-500 transition shadow-lg shadow-brand-600/25 btn-press">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          开始创作
        </a>
        <a href="#faq" class="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl text-base font-medium text-white/70 bg-white/5 hover:bg-white/10 transition border border-white/10 btn-press">
          了解公测
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3"/></svg>
        </a>
      </div>

      <!-- Stats -->
      <div class="mt-16 flex items-center justify-center gap-8 sm:gap-12 text-center animate-fade-up animate-fade-up-delay-4">
        <div><div class="text-2xl font-bold text-white">零</div><div class="text-xs text-white/40 mt-1">注册门槛</div></div>
        <div class="w-px h-10 bg-white/10" />
        <div><div class="text-2xl font-bold text-white">10</div><div class="text-xs text-white/40 mt-1">每日免费生成</div></div>
        <div class="w-px h-10 bg-white/10" />
        <div><div class="text-2xl font-bold text-white">秒级</div><div class="text-xs text-white/40 mt-1">快速出曲</div></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
#hero-canvas {
  opacity: 0.6;
}
</style>
