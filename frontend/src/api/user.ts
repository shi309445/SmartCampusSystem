import request from '@/utils/request'
import type { ApiResponse, PageData } from '@/schemas/api'
import type {
  UsageInfo,
  UserCreateForm,
  UserInfo,
  UserUpdateForm
} from '@/schemas/user'

/**
 * 用户管理接口（app/api/user.py，全部仅管理员）
 *
 * 用户操作自己的接口在 api/auth.ts（/api/auth/me、/api/auth/profile）。
 */

/** GET /api/users */
export function getUserPageApi(params: {
  keyword?: string
  role?: string[]
  status?: number
  page?: number
  page_size?: number
} = {}) {
  return request<ApiResponse<PageData<UserInfo>>>({
    url: '/api/users',
    method: 'get',
    params
  })
}

/** GET /api/users/{id} */
export function getUserDetailApi(userId: number) {
  return request<ApiResponse<UserInfo>>({
    url: `/api/users/${userId}`,
    method: 'get'
  })
}

/** POST /api/users（管理员建号可以指定角色） */
export function createUserApi(data: UserCreateForm) {
  return request<ApiResponse<UserInfo>>({
    url: '/api/users',
    method: 'post',
    data
  })
}

/** PUT /api/users/{id}（只传改动字段） */
export function updateUserApi(userId: number, data: UserUpdateForm) {
  return request<ApiResponse<UserInfo>>({
    url: `/api/users/${userId}`,
    method: 'put',
    data
  })
}

/** PUT /api/users/{id}/password（管理员重置，不需要原密码） */
export function resetUserPasswordApi(userId: number, newPassword: string) {
  return request<ApiResponse<null>>({
    url: `/api/users/${userId}/password`,
    method: 'put',
    data: { new_password: newPassword }
  })
}

/** DELETE /api/users/{id}（有预约记录的用户会被拒绝） */
export function deleteUserApi(userId: number) {
  return request<ApiResponse<{ id: number }>>({
    url: `/api/users/${userId}`,
    method: 'delete'
  })
}

/** GET /api/admin/usage/{user_id}（管理员查看某个学生今天的助手用量） */
export function getUserUsageApi(userId: number) {
  return request<ApiResponse<UsageInfo>>({
    url: `/api/admin/usage/${userId}`,
    method: 'get'
  })
}
