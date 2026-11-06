import request from '@/utils/request'
import type { ApiResponse, PageData } from '@/schemas/api'
import type {
  RoomForm,
  RoomInfo,
  RoomQuery,
  RoomSeats,
  SeatForm,
  SeatInfo
} from '@/schemas/room'

/**
 * 自习室与座位接口（app/api/room.py）
 *
 * 座位图用 `getRoomSeatsApi` 一次拿全（含每个座位在该时段是否空闲），
 * 不用前端逐个座位再发请求。
 */

/** GET /api/rooms */
export function getRoomPageApi(params: RoomQuery = {}) {
  return request<ApiResponse<PageData<RoomInfo>>>({
    url: '/api/rooms',
    method: 'get',
    params
  })
}

/** GET /api/rooms/{id} */
export function getRoomDetailApi(roomId: number) {
  return request<ApiResponse<RoomInfo>>({
    url: `/api/rooms/${roomId}`,
    method: 'get'
  })
}

/**
 * GET /api/rooms/{id}/seats
 *
 * 传 start_time / end_time（ISO，不带时区）就按该时段判断座位是否空闲；
 * 都不传则按座位自身状态判断。
 */
export function getRoomSeatsApi(
  roomId: number,
  times: { start_time?: string; end_time?: string } = {}
) {
  return request<ApiResponse<RoomSeats>>({
    url: `/api/rooms/${roomId}/seats`,
    method: 'get',
    params: times.start_time && times.end_time ? times : undefined
  })
}

/** POST /api/rooms（管理员） */
export function createRoomApi(data: RoomForm) {
  return request<ApiResponse<RoomInfo>>({
    url: '/api/rooms',
    method: 'post',
    data
  })
}

/** PUT /api/rooms/{id}（管理员） */
export function updateRoomApi(roomId: number, data: Partial<RoomForm>) {
  return request<ApiResponse<RoomInfo>>({
    url: `/api/rooms/${roomId}`,
    method: 'put',
    data
  })
}

/** DELETE /api/rooms/{id}（管理员；有预约记录时 409） */
export function deleteRoomApi(roomId: number) {
  return request<ApiResponse<{ id: number }>>({
    url: `/api/rooms/${roomId}`,
    method: 'delete'
  })
}

/** POST /api/rooms/{id}/seats（管理员批量新增，总数不能超过容量） */
export function addSeatsApi(roomId: number, seats: SeatForm[]) {
  return request<ApiResponse<SeatInfo[]>>({
    url: `/api/rooms/${roomId}/seats`,
    method: 'post',
    data: { seats }
  })
}

/** PUT /api/seats/{id}（管理员） */
export function updateSeatApi(seatId: number, data: Partial<SeatForm>) {
  return request<ApiResponse<SeatInfo>>({
    url: `/api/seats/${seatId}`,
    method: 'put',
    data
  })
}

/** DELETE /api/seats/{id}（管理员） */
export function deleteSeatApi(seatId: number) {
  return request<ApiResponse<{ id: number }>>({
    url: `/api/seats/${seatId}`,
    method: 'delete'
  })
}
