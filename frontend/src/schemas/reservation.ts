/** 预约类型（对应 app/services/laboratory/models.py、app/services/library/models.py） */

/** 预约状态：0-待审核, 1-预约成功, 2-预约失败, 3-已取消 */
export type ReservationStatus = 0 | 1 | 2 | 3

/** 实验室预约（GET /api/reservations/lab/mine 等） */
export interface LabReservation {
  id: number
  reservation_no: string
  lab_id: number
  lab_name: string
  user_id: number
  user_name: string
  purpose: string | null
  people_num: number | null
  reserve_start_time: string
  reserve_end_time: string
  status: ReservationStatus
  remark: string | null
  handler_id: number | null
  handler_name: string | null
  handle_time: string | null
  create_time: string
  update_time: string
}

/** 自习室座位预约 */
export interface LibraryReservation {
  id: number
  reservation_no: string
  room_id: number
  room_name: string
  seat_id: number
  seat_code: string
  user_id: number
  user_name: string
  reserve_start_time: string
  reserve_end_time: string
  status: ReservationStatus
  remark: string | null
  handler_id: number | null
  handler_name: string | null
  handle_time: string | null
  create_time: string
  update_time: string
}

export interface ReservationCounts {
  pending: number
  approved: number
  rejected: number
  cancelled: number
  total: number
}

/** GET /api/reservations/mine/summary */
export interface ReservationSummary {
  lab: ReservationCounts
  library: ReservationCounts
  pending_total: number
}

/**
 * 提交预约的请求体。
 *
 * 注意：**不带 user_id / user_name** —— 申请人身份由后端从 JWT 里取。
 * 这也是 service 层模型的约束（ReservationCreate 只有业务字段）。
 */
export interface LabReserveForm {
  lab_id: number
  reserve_start_time: string
  reserve_end_time: string
  purpose?: string | null
  people_num?: number | null
}

export interface LibraryReserveForm {
  seat_id: number
  reserve_start_time: string
  reserve_end_time: string
}

export interface ReservationQuery {
  status?: number[]
  keyword?: string
  page?: number
  page_size?: number
}
