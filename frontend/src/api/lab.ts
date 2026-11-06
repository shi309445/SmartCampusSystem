import request from '@/utils/request'
import type { ApiResponse, PageData } from '@/schemas/api'
import type {
  EquipmentForm,
  EquipmentInfo,
  LabDay,
  LabForm,
  LabInfo,
  LabQuery
} from '@/schemas/lab'

/**
 * 实验室与设备接口（app/api/lab.py）
 *
 * 读：登录即可；写：仅管理员（非管理员 403，由后端 require_admin 拦截）。
 */

/** GET /api/labs */
export function getLabPageApi(params: LabQuery = {}) {
  return request<ApiResponse<PageData<LabInfo>>>({
    url: '/api/labs',
    method: 'get',
    params
  })
}

/** GET /api/labs/{id}（关闭的实验室也能看） */
export function getLabDetailApi(labId: number) {
  return request<ApiResponse<LabInfo>>({
    url: `/api/labs/${labId}`,
    method: 'get'
  })
}

/** GET /api/labs/{id}/schedule?day=YYYY-MM-DD（详情页一次拿排期 + 可约时段） */
export function getLabScheduleApi(labId: number, day?: string) {
  return request<ApiResponse<LabDay>>({
    url: `/api/labs/${labId}/schedule`,
    method: 'get',
    params: day ? { day } : undefined
  })
}

/** GET /api/labs/{id}/equipments */
export function getLabEquipmentsApi(labId: number) {
  return request<ApiResponse<EquipmentInfo[]>>({
    url: `/api/labs/${labId}/equipments`,
    method: 'get'
  })
}

/** POST /api/labs（管理员） */
export function createLabApi(data: LabForm) {
  return request<ApiResponse<LabInfo>>({
    url: '/api/labs',
    method: 'post',
    data
  })
}

/** PUT /api/labs/{id}（管理员，只传改动的字段） */
export function updateLabApi(labId: number, data: Partial<LabForm>) {
  return request<ApiResponse<LabInfo>>({
    url: `/api/labs/${labId}`,
    method: 'put',
    data
  })
}

/** DELETE /api/labs/{id}（管理员；有预约记录时 409） */
export function deleteLabApi(labId: number) {
  return request<ApiResponse<{ id: number }>>({
    url: `/api/labs/${labId}`,
    method: 'delete'
  })
}

/** GET /api/equipments */
export function getEquipmentPageApi(params: {
  lab_id?: number
  keyword?: string
  status?: number
  page?: number
  page_size?: number
} = {}) {
  return request<ApiResponse<PageData<EquipmentInfo>>>({
    url: '/api/equipments',
    method: 'get',
    params
  })
}

/** POST /api/labs/{id}/equipments（管理员批量新增） */
export function addEquipmentsApi(labId: number, equipments: EquipmentForm[]) {
  return request<ApiResponse<EquipmentInfo[]>>({
    url: `/api/labs/${labId}/equipments`,
    method: 'post',
    data: { equipments }
  })
}

/** PUT /api/equipments/{id}（管理员） */
export function updateEquipmentApi(equipmentId: number, data: Partial<EquipmentForm>) {
  return request<ApiResponse<EquipmentInfo>>({
    url: `/api/equipments/${equipmentId}`,
    method: 'put',
    data
  })
}

/** DELETE /api/equipments/{id}（管理员） */
export function deleteEquipmentApi(equipmentId: number) {
  return request<ApiResponse<{ id: number }>>({
    url: `/api/equipments/${equipmentId}`,
    method: 'delete'
  })
}
