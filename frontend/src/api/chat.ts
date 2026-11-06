import request from '@/utils/request'
import type { ApiResponse } from '@/schemas/api'
import type {
  ChatResult,
  ConfirmResult,
  HistoryResult,
  ThreadInfo
} from '@/schemas/chat'

/**
 * 智能助手接口（app/api/chat.py）
 *
 * 两阶段写操作：
 *   POST /api/chat          → status=need_confirm 时预约**还没提交**
 *   POST /api/chat/confirm  → 学生确认后服务端才真正落库
 *
 * 助手内部要调模型 + 工具 + 向量检索，耗时远大于普通接口，单独放宽超时。
 */

/** POST /api/chat */
export function sendMessageApi(data: {
  message: string
  session_id?: string | null
  force_rag?: boolean | null
}) {
  return request<ApiResponse<ChatResult>>({
    url: '/api/chat',
    method: 'post',
    data,
    timeout: 120000
  })
}

/**
 * POST /api/chat/confirm
 *
 * action 是兜底回放：把实时返回的 pending_actions[0] 的 tool_name/args 带回来，
 * 即使会话记忆（Milvus）不可用也能完成确认。
 */
export function confirmActionApi(data: {
  thread_id: string
  tool_call_id: string
  approve: boolean
  action?: { tool_name: string; args: Record<string, unknown> } | null
}) {
  return request<ApiResponse<ConfirmResult>>({
    url: '/api/chat/confirm',
    method: 'post',
    data,
    timeout: 60000
  })
}

/** GET /api/chat/history（只返回当前学生自己的会话） */
export function getHistoryApi(params: { session_id?: string; limit?: number } = {}) {
  return request<ApiResponse<HistoryResult>>({
    url: '/api/chat/history',
    method: 'get',
    params
  })
}

/** GET /api/chat/threads（我的会话列表） */
export function getThreadsApi(limit = 20) {
  return request<ApiResponse<ThreadInfo[]>>({
    url: '/api/chat/threads',
    method: 'get',
    params: { limit }
  })
}

/** DELETE /api/chat/thread（清空会话） */
export function clearThreadApi(sessionId?: string) {
  return request<ApiResponse<{ thread_id: string; deleted: number }>>({
    url: '/api/chat/thread',
    method: 'delete',
    params: sessionId ? { session_id: sessionId } : undefined
  })
}
