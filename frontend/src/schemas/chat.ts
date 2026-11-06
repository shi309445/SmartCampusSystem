/** 智能助手类型（对应 app/schemas/chat.py 与 app/harness/confirm.py） */

/** 一个「等学生确认」的写操作 */
export interface PendingAction {
  tool_call_id: string
  /** make_reservation / cancel_my_reservation */
  tool_name: string
  summary: string
  args: Record<string, unknown>
  requires_confirmation: boolean
}

export interface DecisionBrief {
  stage: string | null
  allow_rag: boolean | null
}

/** POST /api/chat */
export interface ChatResult {
  /** need_confirm 时 pending_actions 里有待确认的写操作，此时预约**还没有提交** */
  status: 'ok' | 'need_confirm'
  answer: string
  thread_id: string | null
  trace_id: string | null
  tokens: number
  decision: DecisionBrief | null
  pending_actions: PendingAction[]
  needs_confirmation: boolean
  /** 主模型不可用、本轮走了降级 */
  degraded: boolean
}

/** POST /api/chat/confirm */
export interface ConfirmResult {
  status: 'approved' | 'rejected' | 'not_found' | 'forbidden' | 'error'
  ok: boolean
  answer: string
  tool_name: string
  summary: string
  detail: Record<string, unknown>
}

/** GET /api/chat/history 的单条消息 */
export interface HistoryMessage {
  role: 'human' | 'ai' | 'tool' | 'system'
  content: string
  seq: number
  created_at: string | null
  tool_name: string | null
  tool_call_id: string | null
  /** 这条 tool 消息是不是「还没确认」的写操作 */
  pending: boolean
}

export interface HistoryResult {
  thread_id: string
  total: number
  messages: HistoryMessage[]
}

export interface ThreadInfo {
  thread_id: string
  session: string | null
  messages: number
  last_at: string | null
}

/** 聊天窗口里的一条消息（前端展示用） */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  time: string
  /** 待确认的写操作（渲染成确认卡片） */
  pending?: PendingAction | null
  /** 已经处理过的结论：approved / rejected / error */
  confirmState?: 'approved' | 'rejected' | 'error' | null
  error?: boolean
  tokens?: number
  traceId?: string | null
  degraded?: boolean
}
