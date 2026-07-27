<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import NavBar from './components/NavBar.vue'
import HeroSection from './components/HeroSection.vue'
import GeneratorSection from './components/GeneratorSection.vue'
import PlayerSection from './components/PlayerSection.vue'
import BetaInfoSection from './components/BetaInfoSection.vue'
import SiteFooter from './components/SiteFooter.vue'

const API_BASE = 'https://ai-music-backend-db6h.onrender.com'
const audioUrl = ref(null)
const isGenerating = ref(false)

const revealElements = () => {
  document.querySelectorAll('.scroll-reveal').forEach((el) => {
    const rect = el.getBoundingClientRect()
    if (rect.top < window.innerHeight - 60) {
      el.classList.add('revealed')
    }
  })
}

let scrollHandler = null
onMounted(() => {
  revealElements()
  scrollHandler = () => revealElements()
  window.addEventListener('scroll', scrollHandler, { passive: true })
})

onUnmounted(() => {
  if (scrollHandler) window.removeEventListener('scroll', scrollHandler)
})

const handleGenerate = async (params) => {
  isGenerating.value = true
  audioUrl.value = null
  try {
    const res = await fetch(`${API_BASE}/api/v1/ai/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    const data = await res.json()
    if (data.success && data.audio_url) {
      audioUrl.value = data.audio_url
    } else {
      alert(data.error || '生成失败，请稍后重试')
    }
  } catch (e) {
    alert('网络错误，请检查后端服务状态')
  } finally {
    isGenerating.value = false
  }
}

</script>

<template>
  <div class="relative min-h-screen bg-surface overflow-hidden">
    <NavBar />
    <HeroSection />
    <GeneratorSection :isGenerating="isGenerating" @generate="handleGenerate" />
    <PlayerSection :audioUrl="audioUrl" />
    <BetaInfoSection />
    <SiteFooter />
  </div>
</template>
