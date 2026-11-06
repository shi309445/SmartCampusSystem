/** 自习室 / 座位类型（对应 app/services/library/models.py 与 app/schemas/portal.py） */

/** 自习室 / 座位状态：0-关闭/不可用, 1-开放/可用 */
export type RoomStatus = 0 | 1

export interface RoomInfo {
  id: number
  name: string
  location: string | null
  capacity: number
  open_time: string | null
  close_time: string | null
  description: string | null
  img: string | null
  status: RoomStatus
  create_time: string
  update_time: string
}

export interface SeatInfo {
  id: number
  room_id: number
  code: string
  area: string | null
  has_power: boolean
  description: string | null
  status: RoomStatus
  /** 该查询时段是否可约（不传时段时按座位状态 + 自习室状态判断） */
  available: boolean
}

/** GET /api/rooms/{id}/seats → RoomSeatsOut */
export interface RoomSeats {
  room: RoomInfo
  day: string | null
  start_time: string | null
  end_time: string | null
  total: number
  available: number
  seats: SeatInfo[]
}

export interface RoomForm {
  name: string
  location?: string | null
  capacity: number
  open_time?: string | null
  close_time?: string | null
  description?: string | null
  img?: string | null
  /** 0-关闭, 1-开放 */
  status: number
}

export interface RoomQuery {
  keyword?: string
  capacity?: number
  status?: number
  page?: number
  page_size?: number
}

export interface SeatForm {
  code: string
  area?: string | null
  has_power: boolean
  description?: string | null
  status?: number
}
