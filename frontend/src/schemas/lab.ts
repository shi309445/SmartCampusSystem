/** 实验室 / 设备类型（对应 app/services/laboratory/models.py 与 app/schemas/portal.py） */

import type { LabReservation } from './reservation'

/** 实验室状态：0-关闭, 1-开放 */
export type LabStatus = 0 | 1

export interface LabInfo {
  id: number
  name: string
  location: string | null
  maxpeople: number
  /** 形如 "08:00:00"；未配置时为 null */
  open_time: string | null
  close_time: string | null
  description: string | null
  img: string | null
  status: LabStatus
  create_time: string
  update_time: string
}

/** 设备状态：0-不可用, 1-可用 */
export type EquipmentStatus = 0 | 1

export interface EquipmentInfo {
  id: number
  lab_id: number
  name: string
  code: string
  model: string | null
  description: string | null
  status: EquipmentStatus
  create_time: string
  update_time: string
}

/** 新增 / 更新实验室的表单（更新时不传的字段保持不变） */
export interface LabForm {
  name: string
  location?: string | null
  maxpeople: number
  open_time?: string | null
  close_time?: string | null
  description?: string | null
  img?: string | null
  /** 0-关闭, 1-开放（用 number 而不是字面量联合：表单里来自 switch，类型更宽松） */
  status: number
}

export interface LabQuery {
  keyword?: string
  people_num?: number
  status?: number
  page?: number
  page_size?: number
}

export interface EquipmentForm {
  name: string
  code: string
  model?: string | null
  description?: string | null
  status?: number
}

/** GET /api/labs/{id}/schedule 的单个时段 */
export interface TimeSlot {
  start_time: string
  end_time: string
  available: boolean
}

/** GET /api/labs/{id}/schedule → LabDayOut */
export interface LabDay {
  lab: LabInfo
  day: string
  schedule: LabReservation[]
  slots: TimeSlot[]
}
