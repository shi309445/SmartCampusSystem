import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { clearThreadApi, confirmActionApi, getHistoryApi, sendMessageApi } from '@/api/chat'
import type { ChatMessage, PendingAction } from '@/schemas/chat'

/**
 * 智能助手聊天状态（模块级单例）
 *
 * 聊天窗口挂在 Layout 上，切换路由时组件不会卸载；这里再把记录放在模块作用域，
 * 组件被重建（例如刷新布局）时会话内容也不会丢。
 *
 * **两阶段写操作**（app/api/chat.py 的约定）：
 *
 *     send()      → status = "ok"           直接回答
 *                 → status = "need_confirm" 回答里是「待确认」，**还没有提交**
 *     confirm()   → status = "approved"     学生确认后才真正落库
 *
 * 所以确认卡片不是装饰：不点「确认提交」，后端一条预约都不会写。
 */

const WELCOME_TEXT =
  '你好，我是兰州理工大学智能校园助手 👋\n' +
  '可以问我校规校纪、奖助学金、请销假流程，也能帮你查实验楼和图书馆的座位、办理预约。'

/** 空会话时的引导问题，点一下直接发送 */
export const CHAT_SUGGESTIONS = [
  '国家励志奖学金需要满足什么条件？',
  '学生请假超过三天要谁审批？',
  '帮我约明天下午两点的物理实验室',
  '现在图书馆还有空座位吗？'
]

const MAX_MESSAGES = 200

let messageSeq = 0

const messages = ref<ChatMessage[]>([])
const visible = ref(false)
const sending = ref(false)
/** 正在确认的 tool_call_id（同一时刻只允许提交一个写操作） */
const confirming = ref<string | null>(null)
const hasUnread = ref(false)
const threadId = ref<string | null>(null)
const historyLoaded = ref(false)

const unread = computed(() => !visible.value && hasUnread.value)

function nextId(prefix = 'msg'): string {
  messageSeq += 1
  return `${prefix}-${messageSeq}`
}

function nowText(): string {
  const now = new Date()
  const pad = (num: number) => String(num).padStart(2, '0')
  return `${pad(now.getHours())}:${pad(now.getMinutes())}`
}

function push(
  role: ChatMessage['role'],
  content: string,
  extra: Partial<ChatMessage> = {}
): ChatMessage {
  const message: ChatMessage = {
    id: nextId(),
    role,
    content,
    time: nowText(),
    ...extra
  }
  messages.value.push(message)
  if (messages.value.length > MAX_MESSAGES) {
    messages.value.splice(0, messages.value.length - MAX_MESSAGES)
  }
  return message
}

function ensureWelcome(): void {
  if (!messages.value.length) {
    push('assistant', WELCOME_TEXT)
  }
}

/** 助手消息统一走这里：窗口收起时标记未读 */
function pushAssistant(content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  const message = push('assistant', content, extra)
  if (!visible.value) hasUnread.value = true
  return message
}

export function useChatStore() {
  function openChat(): void {
    ensureWelcome()
    visible.value = true
    hasUnread.value = false
    void loadHistory()
  }

  function closeChat(): void {
    visible.value = false
  }

  function toggleChat(): void {
    if (visible.value) closeChat()
    else openChat()
  }

  /**
   * 从后端拉最近的历史消息（只在第一次打开窗口时拉一次）
   *
   * 只展示 human / ai 两类文本；tool 消息如果带「待确认」标记，
   * 会还原成一张确认卡片（tool_call_id 在历史里有，args 没有——
   * 确认时交给后端从会话记忆里取，取不到会返回 not_found）。
   */
  async function loadHistory(force = false): Promise<void> {
    if (historyLoaded.value && !force) return
    try {
      const res = await getHistoryApi({ limit: 30 })
      const data = res.data
      threadId.value = data.thread_id
      const restored: ChatMessage[] = []
      for (const item of data.messages) {
        const time = item.created_at
          ? `${item.created_at.slice(11, 16)}`
          : ''
        if (item.role === 'human' && item.content.trim()) {
          restored.push({
            id: nextId('hist'),
            role: 'user',
            content: item.content,
            time
          })
        } else if (item.role === 'ai' && item.content.trim()) {
          restored.push({
            id: nextId('hist'),
            role: 'assistant',
            content: item.content,
            time
          })
        } else if (item.role === 'tool' && item.pending && item.tool_call_id) {
          restored.push({
            id: nextId('hist'),
            role: 'assistant',
            content: '有一条写操作还在等你确认。',
            time,
            pending: {
              tool_call_id: item.tool_call_id,
              tool_name: item.tool_name ?? '',
              summary: item.content,
              args: {},
              requires_confirmation: true
            }
          })
        }
      }
      // 后端有历史就以历史为准；没有就保留本地欢迎语
      if (restored.length) messages.value = restored
    } catch {
      // 服务没起 / 没有会话时静默降级为空会话（不影响直接聊天）
    } finally {
      historyLoaded.value = true
    }
  }

  /** 发一条消息；有写操作时返回的卡片由模板渲染，等待学生点击确认 */
  async function send(text: string): Promise<void> {
    const content = text.trim()
    if (!content || sending.value) return

    ensureWelcome()
    push('user', content)
    sending.value = true

    try {
      const res = await sendMessageApi({ message: content })
      if (res.code !== 200) {
        pushAssistant('抱歉，助手暂时无法回答，请稍后再试。', { error: true })
        return
      }
      const data = res.data
      threadId.value = data.thread_id ?? threadId.value

      const actions = data.pending_actions ?? []
      if (!actions.length) {
        pushAssistant(data.answer || '（助手没有返回内容）', {
          tokens: data.tokens,
          traceId: data.trace_id,
          degraded: data.degraded
        })
        return
      }

      // 第一条卡片带上助手的说明，其余卡片只展示待确认内容
      actions.forEach((action, index) => {
        pushAssistant(index === 0 ? data.answer : '', {
          pending: action,
          tokens: index === 0 ? data.tokens : undefined,
          traceId: index === 0 ? data.trace_id : null,
          degraded: data.degraded
        })
      })
    } catch {
      pushAssistant('请求失败，请检查后端服务或稍后重试。', { error: true })
    } finally {
      sending.value = false
    }
  }

  /**
   * 确认 / 取消一个待提交的写操作
   *
   * `action` 兜底回放只在「实时返回的卡片」上有 args；历史还原的卡片没有 args，
   * 但后端会从会话记忆里找回参数（Milvus 不可用时会返回 not_found，如实提示）。
   */
  async function confirm(message: ChatMessage, approve: boolean): Promise<void> {
    const action = message.pending
    if (!action || confirming.value) return
    if (!threadId.value) {
      ElMessage.warning('会话还没准备好，请刷新后重试')
      return
    }

    confirming.value = action.tool_call_id
    try {
      const res = await confirmActionApi({
        thread_id: threadId.value,
        tool_call_id: action.tool_call_id,
        approve,
        action: action.args && Object.keys(action.args).length
          ? { tool_name: action.tool_name, args: action.args }
          : null
      })
      const data = res.data
      message.pending = null
      message.confirmState = data.status === 'approved' ? 'approved' : 'rejected'
      message.content = data.answer || message.content
      if (!visible.value) hasUnread.value = true
    } catch (error) {
      const body = (error as { response?: { data?: { data?: { answer?: string } } } })?.response
        ?.data
      message.confirmState = 'error'
      message.pending = null
      message.content =
        body?.data?.answer || '确认失败，可能这条操作已经处理过了，请刷新后重试。'
      message.error = true
    } finally {
      confirming.value = null
    }
  }

  /** 清空会话（后端删记忆 + 本地清空），用于「新会话」或隐私清理 */
  async function clearThread(): Promise<void> {
    try {
      await clearThreadApi()
    } catch {
      // 后端不可用时也让本地清空，至少界面上是干净的
    }
    messages.value = []
    threadId.value = null
    historyLoaded.value = true
    ensureWelcome()
    ElMessage.success('已开始新的会话')
  }

  /** 登录 / 退出时重置（避免上一个账号的聊天记录留在界面上） */
  function reset(): void {
    messages.value = []
    threadId.value = null
    historyLoaded.value = false
    hasUnread.value = false
    visible.value = false
    ensureWelcome()
  }

  return {
    messages,
    visible,
    sending,
    confirming,
    unread,
    threadId,
    openChat,
    closeChat,
    toggleChat,
    loadHistory,
    send,
    confirm,
    clearThread,
    reset
  }
}

/** 给「确认卡片」用：把 args 里最关键的几个字段翻成人话（没有就返回空数组） */
export function describePendingArgs(action: PendingAction): string[] {
  const args = action.args ?? {}
  const lines: string[] = []

  const asText = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')
  const date = asText(args['reserve_date'])
  const start = asText(args['reserve_start_time'])
  const end = asText(args['reserve_end_time'])
  if (start || end) {
    const range = start && end ? `${start} ~ ${end}` : start || end
    // 模型有时把日期写在时间串里（"2026-10-16 14:00"），这时不再重复日期
    const withDate = start.includes('-') || end.includes('-') ? range : `${date} ${range}`.trim()
    lines.push(`时间：${withDate}`)
  }

  if (typeof args['lab_id'] === 'number') lines.push(`实验室 ID：${args['lab_id']}`)
  if (typeof args['seat_id'] === 'number') lines.push(`座位 ID：${args['seat_id']}`)
  if (typeof args['people_num'] === 'number') lines.push(`人数：${args['people_num']}`)
  if (typeof args['purpose'] === 'string' && args['purpose']) {
    lines.push(`用途：${args['purpose']}`)
  }
  if (typeof args['reservation_id'] === 'number') {
    lines.push(`预约 ID：${args['reservation_id']}`)
  }
  if (typeof args['reason'] === 'string' && args['reason']) {
    lines.push(`原因：${args['reason']}`)
  }
  return lines
}
