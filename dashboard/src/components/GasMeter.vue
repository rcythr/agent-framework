<script setup>
import { ref, computed } from 'vue'
import { apiFetch } from '../api/index.js'

const props = defineProps(['job', 'sessionMode'])
const emit = defineEmits(['gasAdded'])

const inputTopup = ref(20000)
const outputTopup = ref(5000)
const adding = ref(false)

const isOutOfGas = computed(() => props.job.status === 'out_of_gas')
const inputRatio = computed(() =>
  props.job.gas_limit_input ? Math.min(props.job.gas_used_input / props.job.gas_limit_input, 1) : 0
)
const outputRatio = computed(() =>
  props.job.gas_limit_output ? Math.min(props.job.gas_used_output / props.job.gas_limit_output, 1) : 0
)
const inputExhausted = computed(() => isOutOfGas.value && inputRatio.value >= 1)
const outputExhausted = computed(() => isOutOfGas.value && outputRatio.value >= 1)
const gasPath = computed(() =>
  props.sessionMode ? `/sessions/${props.job.id}/gas` : `/agents/${props.job.id}/gas`
)

async function handleAddGas() {
  adding.value = true
  try {
    await apiFetch(gasPath.value, {
      method: 'POST',
      body: JSON.stringify({ input_amount: inputTopup.value, output_amount: outputTopup.value }),
    })
    emit('gasAdded')
  } finally {
    adding.value = false
  }
}
</script>

<template>
  <div class="gas-section">
    <div class="gas-title">Gas Budget</div>
    <div class="gas-label">
      <span>Input tokens</span>
      <span>{{ (job.gas_used_input || 0).toLocaleString() }} / {{ (job.gas_limit_input || 0).toLocaleString() }}</span>
    </div>
    <div class="progress-bar-wrap">
      <div :class="['progress-bar', inputExhausted ? 'amber' : '']" :style="{ width: `${inputRatio * 100}%` }" />
    </div>
    <div class="gas-label" style="margin-top: 6px">
      <span>Output tokens</span>
      <span>{{ (job.gas_used_output || 0).toLocaleString() }} / {{ (job.gas_limit_output || 0).toLocaleString() }}</span>
    </div>
    <div class="progress-bar-wrap">
      <div :class="['progress-bar', outputExhausted ? 'amber' : '']" :style="{ width: `${outputRatio * 100}%` }" />
    </div>
    <div v-if="isOutOfGas" class="out-of-gas-banner">
      <h4>⚠ Agent paused — out of gas. Review the execution trace and add more tokens to continue.</h4>
      <div class="topup-row">
        <span class="topup-label">Input tokens</span>
        <input class="topup-input" type="number" v-model.number="inputTopup" min="0" step="1000" />
        <span class="topup-label">Output tokens</span>
        <input class="topup-input" type="number" v-model.number="outputTopup" min="0" step="1000" />
        <button class="btn-add-gas" @click="handleAddGas" :disabled="adding">
          {{ adding ? 'Adding…' : 'Add Gas' }}
        </button>
      </div>
    </div>
  </div>
</template>
