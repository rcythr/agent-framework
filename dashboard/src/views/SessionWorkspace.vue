<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { apiFetch } from '../api/index.js'
import { elapsed, timeAgo } from '../utils/time.js'
import StatusPill from '../components/StatusPill.vue'
import GasMeter from '../components/GasMeter.vue'
import LogEvent from '../components/LogEvent.vue'

const props = defineProps(['session'])
const emit = defineEmits(['newSession'])

const session = ref({ ...props.session })
const messages = ref([])
const logEvents = ref([])
const input = ref('')
const sending = ref(false)
const tick = ref(0)
const threadEl = ref(null)
let autoScroll = true

const TERMINAL = ['complete', 'failed', 'cancelled', 'out_of_gas']
const isTerminal = computed(() => TERMINAL.includes(session.value.status))
const isWaiting = computed(() => session.value.status === 'waiting_for_user')

const messageTypeLabel = {
  instruction: 'instruction',
  interrupt: 'interrupt',
  agent_response: 'response',
  input_request: 'question',
  input_response: 'answer',
}

let tickTimer = null
let es = null
let pollTimer = null

onMounted(() => {
  tickTimer = setInterval(() => tick.value++, 1000)
  connectSSE()
  schedulePoll()
})

onUnmounted(() => {
  clearInterval(tickTimer)
  clearTimeout(pollTimer)
  if (es) es.close()
})

function connectSSE() {
  if (es) es.close()
  es = new EventSource(`/sessions/${session.value.id}/stream`)
  es.onmessage = e => {
    if (!e.data) return
    try {
      const item = JSON.parse(e.data)
      if (item.type === 'session_message') {
        if (!messages.value.some(m => m.session_id === item.session_id && m.sequence === item.sequence)) {
          messages.value.push(item)
        }
        if (item.role === 'agent' && item.message_type === 'input_request') {
          session.value = { ...session.value, status: 'waiting_for_user' }
        }
        if (item.role === 'agent' && item.message_type === 'agent_response') {
          if (session.value.status !== 'waiting_for_user') {
            session.value = { ...session.value, status: 'running' }
          }
        }
      } else if (item.type === 'log_event') {
        logEvents.value.push({ ...item, job_id: item.job_id || session.value.id })
      }
    } catch {}
  }
  es.onerror = () => es.close()
}

function schedulePoll() {
  if (TERMINAL.includes(session.value.status)) return
  pollTimer = setTimeout(async () => {
    try {
      const data = await apiFetch(`/sessions/${session.value.id}`)
      session.value = data
      if (!TERMINAL.includes(data.status)) schedulePoll()
    } catch {}
  }, 3000)
}

watch(messages, async () => {
  if (!autoScroll) return
  await nextTick()
  if (threadEl.value) threadEl.value.scrollTop = threadEl.value.scrollHeight
}, { deep: true })

function handleThreadScroll() {
  const el = threadEl.value
  if (!el) return
  autoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

async function handleSend() {
  if (!input.value.trim()) return
  const messageType = isWaiting.value ? 'input_response' : 'interrupt'
  sending.value = true
  try {
    await apiFetch(`/sessions/${session.value.id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content: input.value.trim(), message_type: messageType }),
    })
    input.value = ''
    if (isWaiting.value) session.value = { ...session.value, status: 'running' }
  } catch {} finally {
    sending.value = false
  }
}

async function handleCancel() {
  try {
    await apiFetch(`/sessions/${session.value.id}/cancel`, { method: 'POST' })
    session.value = { ...session.value, status: 'cancelled' }
  } catch {}
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

async function handleGasAdded() {
  try {
    const d = await apiFetch(`/sessions/${session.value.id}/gas`)
    session.value = { ...session.value, ...d }
  } catch {}
}
</script>

<template>
  <div class="session-workspace">
    <!-- Header -->
    <div class="session-header">
      <div class="session-header-info">
        <span class="session-header-title">{{ session.project_path }}</span>
        <span class="session-header-meta">{{ session.branch }}</span>
        <span v-if="session.mr_iid" class="session-header-meta">!{{ session.mr_iid }}</span>
        <StatusPill :status="session.status" />
        <span class="elapsed">{{ elapsed(session.created_at) }}</span>
      </div>
      <button v-if="!isTerminal" class="btn-cancel btn-sm" @click="handleCancel">Cancel</button>
      <button v-else class="btn-primary btn-sm" @click="emit('newSession', { project: session.project_path, branch: session.branch })">
        New Session
      </button>
    </div>

    <!-- Panes -->
    <div class="session-panes">
      <!-- Left: Conversation thread -->
      <div class="session-pane">
        <div class="session-pane-title">Conversation</div>
        <div class="conversation-thread" ref="threadEl" @scroll="handleThreadScroll">
          <div v-if="messages.length === 0" style="color: var(--text-muted); font-size: 12px; text-align: center; padding: 24px">
            Session starting…
          </div>
          <div
            v-for="(m, i) in messages"
            :key="i"
            :class="`conv-message ${m.role}`"
          >
            <div class="conv-bubble">{{ m.content }}</div>
            <span class="conv-type-badge">{{ messageTypeLabel[m.message_type] || m.message_type }}</span>
            <span class="conv-meta">{{ timeAgo(m.timestamp) }}</span>
          </div>
        </div>
        <div class="session-input-area">
          <div v-if="isTerminal" style="color: var(--text-muted); font-size: 12px; text-align: center">
            Session ended · {{ session.status }}
          </div>
          <template v-else>
            <div :class="['session-input-label', isWaiting ? 'waiting' : 'interrupt']">
              {{ isWaiting ? 'Agent is waiting for your answer' : 'Redirect the agent (sends interrupt)' }}
            </div>
            <div class="session-input-row">
              <textarea
                class="form-input"
                v-model="input"
                :placeholder="isWaiting ? 'Type your answer…' : 'Type a redirect message…'"
                @keydown="handleKeydown"
              />
              <button class="btn-primary" @click="handleSend" :disabled="sending || !input.trim()">
                {{ sending ? '…' : 'Send' }}
              </button>
            </div>
          </template>
        </div>
      </div>

      <!-- Right: Execution trace -->
      <div class="session-pane">
        <div class="session-pane-title">Execution Trace</div>
        <div style="flex: 1; overflow-y: auto; padding: 12px; font-size: 12px">
          <div v-if="logEvents.length === 0" style="color: var(--text-muted); font-size: 12px">No events yet.</div>
          <LogEvent v-for="(ev, i) in logEvents" :key="i" :event="ev" />
        </div>
        <!-- Gas meter -->
        <div style="padding: 12px 16px; border-top: 1px solid var(--border)">
          <GasMeter :job="session" :session-mode="true" @gas-added="handleGasAdded" />
        </div>
      </div>
    </div>
  </div>
</template>
