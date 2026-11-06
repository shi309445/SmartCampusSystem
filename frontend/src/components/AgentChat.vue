<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  ChatDotRound,
  CircleCheckFilled,
  CircleCloseFilled,
  Close,
  MagicStick,
  Promotion,
  RefreshLeft,
  WarningFilled
} from '@element-plus/icons-vue'
import { useChatStore, CHAT_SUGGESTIONS, describePendingArgs } from '@/utils/chatStore'
import { useUser } from '@/utils/user'
import { toolNameLabel } from '@/utils/dict'
import type { ChatMessage, PendingAction } from '@/schemas/chat'

/**
 * 智能助手对话窗口
 *
 * 挂在 Layout 上，切页面不会卸载；聊天记录在 utils/chatStore（模块级单例）。
 *
 * 这里是唯一处理 **两阶段确认** 的地方：
 *   后端返回 status=need_confirm 时，一条预约还**没有提交**，
 *   学生点「确认提交」才会调 POST /api/chat/confirm 真正落库。
 * 所以确认卡片必须显示清楚「要提交什么」，按钮也必须真的可点。
 */
const { userInfo, displayName } = useUser()
const {
  messages,
  visible,
  sending,
  confirming,
  unread,
  openChat,
  closeChat,
  send,
  confirm,
  clearThread
} = useChatStore()

const input = ref('')
const bodyRef = ref<HTMLElement | null>(null)
const clearing = ref(false)

const canSend = computed(() => input.value.trim().length > 0 && !sending.value)
const showWelcomeHints = computed(() => messages.value.length <= 1 && !sending.value)
const roleText = computed(() => (userInfo.value?.role === 'admin' ? '管理员' : '学生'))

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = bodyRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(messages, () => void scrollToBottom(), { deep: true })
watch(visible, (value) => {
  if (value) void scrollToBottom()
})

async function handleSend(text?: string): Promise<void> {
  const content = (text ?? input.value).trim()
  if (!content || sending.value) return
  input.value = ''
  await send(content)
}

function onEnter(event: KeyboardEvent): void {
  // Enter 发送，Shift + Enter 换行
  if (event.shiftKey) return
  event.preventDefault()
  void handleSend()
}

async function onConfirm(message: ChatMessage, approve: boolean): Promise<void> {
  await confirm(message, approve)
}

async function onClear(): Promise<void> {
  clearing.value = true
  try {
    await clearThread()
    input.value = ''
    await scrollToBottom()
  } finally {
    clearing.value = false
  }
}

function argsLines(action: PendingAction): string[] {
  return describePendingArgs(action)
}

function bubbleStateText(message: ChatMessage): string {
  if (message.confirmState === 'approved') return '已提交'
  if (message.confirmState === 'rejected') return '已取消'
  if (message.confirmState === 'error') return '提交失败'
  return ''
}
</script>

<template>
  <div class="agent">
    <!-- 悬浮入口 -->
    <button v-show="!visible" type="button" class="agent-fab" @click="openChat">
      <span class="agent-fab__icon">
        <el-icon><ChatDotRound /></el-icon>
        <span v-if="unread" class="agent-fab__dot" />
      </span>
      <span class="agent-fab__text">智能助手</span>
    </button>

    <!-- 对话窗口 -->
    <transition name="agent-slide">
      <section v-show="visible" class="agent-panel">
        <header class="agent-head">
          <div class="agent-head__avatar">
            <el-icon><MagicStick /></el-icon>
          </div>
          <div class="agent-head__meta">
            <div class="agent-head__name">校园智能助手</div>
            <div class="agent-head__sub">
              你好，{{ displayName }}（{{ roleText }}）· 校规问答 / 预约办理
            </div>
          </div>
          <el-button
            link
            class="agent-head__btn"
            :icon="RefreshLeft"
            title="开始新会话（清空历史）"
            :loading="clearing"
            @click="onClear"
          />
          <el-button
            link
            class="agent-head__btn"
            :icon="Close"
            title="收起窗口"
            @click="closeChat"
          />
        </header>

        <div ref="bodyRef" class="agent-body">
          <div
            v-for="message in messages"
            :key="message.id"
            class="agent-row"
            :class="message.role === 'user' ? 'is-user' : 'is-ai'"
          >
            <div
              class="agent-bubble"
              :class="{
                'is-error': message.error,
                'is-pending': !!message.pending
              }"
            >
              <div v-if="message.content" class="agent-text">{{ message.content }}</div>

              <!-- 待确认的写操作：确认后才真正提交 -->
              <div v-if="message.pending" class="confirm">
                <div class="confirm__head">
                  <el-icon><WarningFilled /></el-icon>
                  <span>待确认：{{ toolNameLabel(message.pending.tool_name) }}</span>
                </div>
                <div class="confirm__summary">{{ message.pending.summary }}</div>
                <ul v-if="argsLines(message.pending).length" class="confirm__args">
                  <li v-for="line in argsLines(message.pending)" :key="line">{{ line }}</li>
                </ul>
                <div class="confirm__hint">
                  助手只能提议，点「确认提交」后服务端才会真正写入。
                </div>
                <div class="confirm__actions">
                  <el-button
                    type="primary"
                    size="small"
                    :loading="confirming === message.pending.tool_call_id"
                    :disabled="!!confirming && confirming !== message.pending.tool_call_id"
                    @click="onConfirm(message, true)"
                  >
                    确认提交
                  </el-button>
                  <el-button
                    size="small"
                    :disabled="!!confirming"
                    @click="onConfirm(message, false)"
                  >
                    取消
                  </el-button>
                </div>
              </div>

              <div v-if="bubbleStateText(message)" class="confirm-state" :class="`is-${message.confirmState}`">
                <el-icon>
                  <CircleCheckFilled v-if="message.confirmState === 'approved'" />
                  <CircleCloseFilled v-else />
                </el-icon>
                <span>{{ bubbleStateText(message) }}</span>
              </div>

              <div class="agent-meta">
                <el-tag v-if="message.degraded" size="small" type="warning" effect="plain">
                  已降级
                </el-tag>
                <span v-if="message.tokens" class="agent-meta__tokens">{{ message.tokens }} tokens</span>
                <span class="agent-meta__time">{{ message.time }}</span>
              </div>
            </div>
          </div>

          <div v-if="sending" class="agent-row is-ai">
            <div class="agent-bubble agent-bubble--typing">
              <span class="dot" />
              <span class="dot" />
              <span class="dot" />
              <span class="typing-text">正在查资料 / 整理答案…</span>
            </div>
          </div>

          <div v-if="showWelcomeHints" class="agent-hints">
            <div class="agent-hints__title">可以试着问我：</div>
            <button
              v-for="item in CHAT_SUGGESTIONS"
              :key="item"
              type="button"
              class="agent-hints__item"
              @click="handleSend(item)"
            >
              {{ item }}
            </button>
          </div>
        </div>

        <footer class="agent-foot">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            resize="none"
            maxlength="2000"
            placeholder="输入问题，Enter 发送，Shift + Enter 换行"
            :disabled="sending"
            @keydown.enter="onEnter"
          />
          <el-button
            class="agent-send"
            type="primary"
            :icon="Promotion"
            :loading="sending"
            :disabled="!canSend"
            @click="handleSend()"
          >
            发送
          </el-button>
        </footer>
      </section>
    </transition>
  </div>
</template>

<style scoped>
.agent {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2000;
}

/* ---------------- 悬浮入口 ---------------- */
.agent-fab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, var(--el-color-primary), #6f9dff);
  border: none;
  border-radius: 24px;
  box-shadow: 0 8px 22px rgba(64, 128, 255, 0.38);
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.agent-fab:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 26px rgba(64, 128, 255, 0.46);
}

.agent-fab__icon {
  position: relative;
  display: flex;
  align-items: center;
  font-size: 20px;
}

.agent-fab__dot {
  position: absolute;
  top: -2px;
  right: -3px;
  width: 8px;
  height: 8px;
  border: 2px solid #fff;
  border-radius: 50%;
  background-color: var(--el-color-danger);
}

/* ---------------- 面板 ---------------- */
.agent-panel {
  display: flex;
  flex-direction: column;
  width: 400px;
  height: 600px;
  max-height: calc(100vh - 120px);
  overflow: hidden;
  background-color: #fff;
  border-radius: 14px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.2);
}

.agent-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  color: #fff;
  background: linear-gradient(135deg, #1f4fd8, #4080ff 60%, #6f9dff);
}

.agent-head__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex: none;
  font-size: 20px;
  border-radius: 10px;
  background-color: rgba(255, 255, 255, 0.18);
}

.agent-head__meta {
  flex: 1;
  min-width: 0;
  line-height: 1.3;
}

.agent-head__name {
  font-size: 14px;
  font-weight: 600;
}

.agent-head__sub {
  overflow: hidden;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
  opacity: 0.85;
}

.agent-head__btn {
  color: #fff;
}

.agent-body {
  flex: 1;
  padding: 14px;
  overflow-y: auto;
  background-color: #f5f7fa;
}

/* ---------------- 消息 ---------------- */
.agent-row {
  display: flex;
  margin-bottom: 12px;
}

.agent-row.is-user {
  justify-content: flex-end;
}

.agent-row.is-ai {
  justify-content: flex-start;
}

.agent-bubble {
  max-width: 84%;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.65;
  color: #303133;
  word-break: break-word;
  background-color: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.agent-bubble.is-pending {
  max-width: 92%;
  border: 1px solid var(--el-color-warning-light-5);
  background-color: #fffdf7;
}

.agent-row.is-user .agent-bubble {
  color: #fff;
  background-color: var(--el-color-primary);
}

.agent-bubble.is-error {
  color: var(--el-color-danger);
  background-color: var(--el-color-danger-light-9);
}

.agent-text {
  white-space: pre-wrap;
}

.agent-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 6px;
  font-size: 11px;
  color: #a8abb2;
}

.agent-row.is-user .agent-meta {
  color: rgba(255, 255, 255, 0.75);
}

.agent-meta__tokens {
  margin-left: auto;
}

/* ---------------- 确认卡片 ---------------- */
.confirm {
  margin-top: 8px;
  padding: 10px;
  background-color: #fff;
  border: 1px dashed var(--el-color-warning);
  border-radius: 8px;
}

.confirm__head {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-warning-dark-2);
}

.confirm__summary {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
}

.confirm__args {
  margin-top: 6px;
  font-size: 12px;
  color: #606266;
}

.confirm__args li {
  position: relative;
  padding-left: 10px;
  line-height: 1.8;
}

.confirm__args li::before {
  position: absolute;
  top: 8px;
  left: 0;
  width: 4px;
  height: 4px;
  content: '';
  border-radius: 50%;
  background-color: var(--el-color-warning);
}

.confirm__hint {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.6;
  color: #909399;
}

.confirm__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

.confirm-state {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  font-size: 12px;
}

.confirm-state.is-approved {
  color: var(--el-color-success);
}

.confirm-state.is-rejected {
  color: var(--el-color-info);
}

.confirm-state.is-error {
  color: var(--el-color-danger);
}

/* ---------------- 输入中 ---------------- */
.agent-bubble--typing {
  display: flex;
  align-items: center;
  gap: 4px;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--el-color-primary);
  animation: agent-blink 1.2s infinite ease-in-out;
}

.dot:nth-child(2) {
  animation-delay: 0.2s;
}

.dot:nth-child(3) {
  animation-delay: 0.4s;
}

.typing-text {
  margin-left: 6px;
  font-size: 12px;
  color: #909399;
}

@keyframes agent-blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}

/* ---------------- 引导问题 ---------------- */
.agent-hints {
  margin-top: 4px;
}

.agent-hints__title {
  margin-bottom: 6px;
  font-size: 12px;
  color: #909399;
}

.agent-hints__item {
  display: block;
  width: 100%;
  padding: 8px 10px;
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--el-color-primary);
  text-align: left;
  background-color: #fff;
  border: 1px dashed var(--el-color-primary-light-5);
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.agent-hints__item:hover {
  background-color: var(--el-color-primary-light-9);
}

/* ---------------- 底部输入 ---------------- */
.agent-foot {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 12px;
  background-color: #fff;
  border-top: 1px solid #ebeef5;
}

.agent-send {
  flex: none;
}

.agent-slide-enter-active,
.agent-slide-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.agent-slide-enter-from,
.agent-slide-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.98);
}

@media (max-width: 560px) {
  .agent {
    right: 12px;
    bottom: 12px;
  }

  .agent-panel {
    width: calc(100vw - 24px);
    height: calc(100vh - 140px);
  }

  .agent-fab__text {
    display: none;
  }
}
</style>
