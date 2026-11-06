import request from '@/utils/request'
import type { ApiResponse, PageData } from '@/schemas/api'
import type {
  LabReservation,
  LabReserveForm,
  LibraryReservation,
  LibraryReserveForm,
  ReservationQuery,
  ReservationSummary
} from '@/schemas/reservation'

/**
 * 预约接口
 *
 * 学生侧：app/api/reservation.py
 *   我的预约 / 提交 / 取消（申请人身份由后端从 JWT 取，请求体里不传 user_id）
 *
 * 管理员侧：app/api/admin_reservation.py
 *   全部预约 / 审核 / 代取消（非管理员 403）
 */

// ==================================================================
// 一、学生：我的预约
// ==================================================================

/** GET /api/reservations/mine/summary（首页概览：四种状态的数量） */
export function getMyReservationSummaryApi() {
  return request<ApiResponse<ReservationSummary>>({
    url: '/api/reservations/mine/summary',
    method: 'get'
  })
}

/** GET /api/reservations/lab/mine */
export function getMyLabReservationsApi(params: ReservationQuery = {}) {
  return request<ApiResponse<PageData<LabReservation>>>({
    url: '/api/reservations/lab/mine',
    method: 'get',
    params
  })
}

/** GET /api/reservations/library/mine */
export function getMyLibraryReservationsApi(params: ReservationQuery = {}) {
  return request<ApiResponse<PageData<LibraryReservation>>>({
    url: '/api/reservations/library/mine',
    method: 'get',
    params
  })
}

/** POST /api/reservations/lab（提交后是「待审核」） */
export function createLabReservationApi(data: LabReserveForm) {
  return request<ApiResponse<LabReservation>>({
    url: '/api/reservations/lab',
    method: 'post',
    data
  })
}

/** POST /api/reservations/library（默认也是「待审核」） */
export function createLibraryReservationApi(data: LibraryReserveForm) {
  return request<ApiResponse<LibraryReservation>>({
    url: '/api/reservations/library',
    method: 'post',
    data
  })
}

/** PUT /api/reservations/lab/{id}/cancel */
export function cancelLabReservationApi(reservationId: number, remark?: string) {
  return request<ApiResponse<LabReservation>>({
    url: `/api/reservations/lab/${reservationId}/cancel`,
    method: 'put',
    data: { remark: remark ?? null }
  })
}

/** PUT /api/reservations/library/{id}/cancel */
export function cancelLibraryReservationApi(reservationId: number, remark?: string) {
  return request<ApiResponse<LibraryReservation>>({
    url: `/api/reservations/library/${reservationId}/cancel`,
    method: 'put',
    data: { remark: remark ?? null }
  })
}

// ==================================================================
// 二、管理员：全部预约 / 审核 / 代取消
// ==================================================================

/** GET /api/admin/reservations/lab */
export function getAllLabReservationsApi(params: ReservationQuery = {}) {
  return request<ApiResponse<PageData<LabReservation>>>({
    url: '/api/admin/reservations/lab',
    method: 'get',
    params
  })
}

/** GET /api/admin/reservations/library */
export function getAllLibraryReservationsApi(params: ReservationQuery = {}) {
  return request<ApiResponse<PageData<LibraryReservation>>>({
    url: '/api/admin/reservations/library',
    method: 'get',
    params
  })
}

/** PUT /api/admin/reservations/lab/{id}/review（status: 1-通过 2-驳回） */
export function reviewLabReservationApi(
  reservationId: number,
  status: 1 | 2,
  remark?: string
) {
  return request<ApiResponse<LabReservation>>({
    url: `/api/admin/reservations/lab/${reservationId}/review`,
    method: 'put',
    data: { status, remark: remark ?? null }
  })
}

/** PUT /api/admin/reservations/library/{id}/review */
export function reviewLibraryReservationApi(
  reservationId: number,
  status: 1 | 2,
  remark?: string
) {
  return request<ApiResponse<LibraryReservation>>({
    url: `/api/admin/reservations/library/${reservationId}/review`,
    method: 'put',
    data: { status, remark: remark ?? null }
  })
}

/** PUT /api/admin/reservations/lab/{id}/cancel（管理员代取消） */
export function adminCancelLabReservationApi(reservationId: number, remark?: string) {
  return request<ApiResponse<LabReservation>>({
    url: `/api/admin/reservations/lab/${reservationId}/cancel`,
    method: 'put',
    data: { remark: remark ?? null }
  })
}

/** PUT /api/admin/reservations/library/{id}/cancel */
export function adminCancelLibraryReservationApi(
  reservationId: number,
  remark?: string
) {
  return request<ApiResponse<LibraryReservation>>({
    url: `/api/admin/reservations/library/${reservationId}/cancel`,
    method: 'put',
    data: { remark: remark ?? null }
  })
}
