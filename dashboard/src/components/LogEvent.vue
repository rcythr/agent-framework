<script setup>
import { ref, computed } from 'vue'

const props = defineProps(['event'])
const open = ref(false)

const typeLabels = {
  llm_query: '🔵 LLM Query',
  llm_response: '💬 LLM Response',
  tool_call: '🔧 Tool Call',
  tool_result: '✅ Tool Result',
  complete: '🎉 Complete',
  error: '❌ Error',
  out_of_gas: '⚠ Out of Gas',
  gas_updated: 'gas_updated',
  input_request: '💬 Input Request',
  input_received: '💬 Input Received',
  interrupted: '⛔ Interrupted',
}

const p = computed(() => props.event.payload)
const hasBody = computed(() =>
  ['llm_query', 'llm_response', 'tool_call', 'tool_result', 'complete', 'error', 'out_of_gas'].includes(props.event.event_type)
)
const tokenCount = computed(() => p.value?.input_tokens || p.value?.output_tokens || p.value?.tokens)
const label = computed(() => typeLabels[props.event.event_type] || props.event.event_type)

function toggle() {
  if (hasBody.value) open.value = !open.value
}
</script>

<template>
  <!-- gas_updated: compact single-line -->
  <div v-if="event.event_type === 'gas_updated'" :class="`log-event log-${event.event_type}`">
    <div class="log-event-header" style="font-size: 11px">
      gas_updated — input: {{ (p.gas_used_input || 0).toLocaleString() }} / {{ (p.gas_limit_input || 0).toLocaleString() }}
      · output: {{ (p.gas_used_output || 0).toLocaleString() }} / {{ (p.gas_limit_output || 0).toLocaleString() }}
    </div>
  </div>

  <!-- all other event types -->
  <div v-else :class="`log-event log-${event.event_type}`">
    <div class="log-event-header" @click="toggle">
      <span v-if="hasBody">{{ open ? '▾' : '▸' }}</span>
      <span>{{ label }}</span>
      <span v-if="event.event_type === 'tool_call' && p.tool" style="opacity: 0.7">{{ p.tool }}</span>
      <span v-if="event.event_type === 'tool_result' && p.duration_ms" class="duration-badge">{{ p.duration_ms }}ms</span>
      <span v-if="tokenCount" class="token-badge">{{ tokenCount }} tokens</span>
    </div>

    <template v-if="open && hasBody">
      <!-- llm_query -->
      <div v-if="event.event_type === 'llm_query'" class="log-event-body">
        <div class="kv-row">
          <span class="kv-key">messages</span>
          <span class="kv-val">{{ p.message_count ?? (p.messages?.length ?? '?') }}</span>
        </div>
        <div v-if="p.tools?.length" class="kv-row">
          <span class="kv-key">tools</span>
          <span class="kv-val">{{ p.tools.join(', ') }}</span>
        </div>
      </div>

      <!-- llm_response -->
      <div v-else-if="event.event_type === 'llm_response'" class="log-event-body">
        <pre v-if="p.content">{{ p.content }}</pre>
        <div v-if="p.tool_calls?.length" style="margin-top: 6px; color: var(--purple)">
          Tool calls: {{ p.tool_calls.map(tc => tc.name || tc).join(', ') }}
        </div>
      </div>

      <!-- tool_call -->
      <div v-else-if="event.event_type === 'tool_call'" class="log-event-body">
        <div v-for="[k, v] in Object.entries(p.args || {})" :key="k" class="kv-row">
          <span class="kv-key">{{ k }}</span>
          <span class="kv-val">{{ typeof v === 'string' ? v : JSON.stringify(v) }}</span>
        </div>
      </div>

      <!-- tool_result -->
      <div v-else-if="event.event_type === 'tool_result'" class="log-event-body">
        <pre>{{ typeof p.result === 'string' ? p.result : JSON.stringify(p.result, null, 2) }}</pre>
      </div>

      <!-- complete -->
      <div v-else-if="event.event_type === 'complete'" class="log-event-body">
        <div class="kv-row"><span class="kv-key">LLM calls</span><span class="kv-val">{{ p.llm_calls ?? '?' }}</span></div>
        <div class="kv-row"><span class="kv-key">Tool calls</span><span class="kv-val">{{ p.tool_calls ?? '?' }}</span></div>
        <div class="kv-row"><span class="kv-key">Total time</span><span class="kv-val">{{ p.total_time_ms ? `${p.total_time_ms}ms` : '?' }}</span></div>
      </div>

      <!-- error -->
      <div v-else-if="event.event_type === 'error'" class="log-event-body">
        <div style="color: var(--red); margin-bottom: 6px">{{ p.message || p.error }}</div>
        <details v-if="p.traceback">
          <summary style="cursor: pointer; color: var(--text-muted); font-size: 11px">Traceback</summary>
          <pre style="margin-top: 6px">{{ p.traceback }}</pre>
        </details>
      </div>

      <!-- out_of_gas -->
      <div v-else-if="event.event_type === 'out_of_gas'" class="log-event-body">
        <div style="color: var(--amber)">{{ p.message || 'Agent paused: out of gas' }}</div>
      </div>

      <!-- fallback -->
      <div v-else-if="p && Object.keys(p).length" class="log-event-body">
        <pre>{{ JSON.stringify(p, null, 2) }}</pre>
      </div>
    </template>
  </div>
</template>
