import request from '@/utils/request'
import type { ApiResponse } from '@/schemas/api'
import type {
  LoginData,
  LoginForm,
  PasswordForm,
  ProfileForm,
  RegisterForm,
  UserInfo
} from '@/schemas/user'

/**
 * 认证接口（app/api/auth.py）
 *
 * 登录 / 注册不需要 token；其余接口由 request 拦截器自动带上 Authorization。
 */

/** POST /api/auth/login */
export function loginApi(data: LoginForm) {
  return request<ApiResponse<LoginData>>({
    url: '/api/auth/login',
    method: 'post',
    data
  })
}

/** POST /api/auth/register（只能注册学生） */
export function registerApi(data: RegisterForm) {
  return request<ApiResponse<UserInfo>>({
    url: '/api/auth/register',
    method: 'post',
    data
  })
}

/** GET /api/auth/me */
export function getMeApi() {
  return request<ApiResponse<UserInfo>>({
    url: '/api/auth/me',
    method: 'get'
  })
}

/** PUT /api/auth/profile（只能改 name / email / phone / avatar） */
export function updateProfileApi(data: ProfileForm) {
  return request<ApiResponse<UserInfo>>({
    url: '/api/auth/profile',
    method: 'put',
    data
  })
}

/** PUT /api/auth/password（必须提供原密码） */
export function updatePasswordApi(data: PasswordForm) {
  return request<ApiResponse<null>>({
    url: '/api/auth/password',
    method: 'put',
    data
  })
}
