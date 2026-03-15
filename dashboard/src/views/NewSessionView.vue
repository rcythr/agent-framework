<script setup>
import { ref } from 'vue'
import SessionLauncher from './SessionLauncher.vue'
import SessionWorkspace from './SessionWorkspace.vue'

const activeSession = ref(null)
const prefill = ref(null)

function onLaunched(session) {
  activeSession.value = session
}

function onNewSession(p) {
  activeSession.value = null
  prefill.value = p
}
</script>

<template>
  <SessionWorkspace
    v-if="activeSession"
    :session="activeSession"
    @new-session="onNewSession"
  />
  <SessionLauncher
    v-else
    :prefill="prefill"
    @launched="onLaunched"
  />
</template>
