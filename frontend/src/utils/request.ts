import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig
} from 'axios'
import { ElMessage } from 'element-plus'
import { getToken, logout } from './auth'
import router from '@/router'
import type { ApiResponse } from '@/schemas/api'

/**
 * 全局 axios 实例
 *
 * 后端所有接口都是统一信封 `{code, message, data}`（app/common/response.py），
 * 所以响应拦截器直接 `return data`，业务代码里写 `const res = await xxxApi(); res.data`
 * 就是拿到真正的业务数据；`schemas/axios.d.ts` 给这套写法补了类型。
 *
 * `paramsSerializer: { indexes: null }`：数组参数序列化成 `status=0&status=1`，
 * 与 FastAPI 的 `list[int]` 查询参数一致（axios 默认会生成 `status[]=0`，后端收不到）。
 */
const service: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 30000,
  paramsSerializer: { indexes: null }
})

// 请求拦截器：带上 JWT
service.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (err: AxiosError) => Promise.reject(err)
)

/** 参数校验失败（422）时后端把字段级错误放在 data 里 */
interface ValidationDetail {
  loc?: unknown
  msg?: unknown
}

/** 把字段路径 ["body", "password"] 拼成 "password" */
function formatLocation(loc: unknown): string {
  if (!Array.isArray(loc)) return ''
  return loc
    .filter((part) => typeof part === 'string' && part !== 'body' && part !== 'query')
    .join('.')
}

/**
 * 从后端错误响应里提取尽量具体的提示文案
 * 422 的顶层 message 只有「请求参数校验错误」，真正的字段原因在 data 数组里，
 * 这里拼上第一条明细，避免用户看到报错却不知道哪个字段填错了。
 */
export function pickErrorMessage(body: unknown): string {
  if (!body || typeof body !== 'object') return '请求失败'
  const res = body as { message?: unknown; detail?: unknown; data?: unknown }
  const base =
    typeof res.message === 'string' && res.message
      ? res.message
      : typeof res.detail === 'string' && res.detail
        ? res.detail
        : '请求失败'

  if (Array.isArray(res.data) && res.data.length) {
    const first = res.data[0] as ValidationDetail | undefined
    const location = formatLocation(first?.loc)
    const msg = typeof first?.msg === 'string' ? first.msg : ''
    if (msg) {
      return location ? `${base}（${location}: ${msg}）` : `${base}（${msg}）`
    }
  }
  return base
}

/** 登录接口的 401 是「账号或密码错误」，不能当成登录态失效去强制登出 */
function isLoginRequest(url: string | undefined): boolean {
  return (url ?? '').includes('/api/auth/login')
}

// 响应拦截器：统一处理业务码与 401
service.interceptors.response.use(
  ((res: AxiosResponse<ApiResponse>) => {
    const data = res.data
    if (!data || typeof data.code !== 'number') {
      // 少数非信封响应（例如网关的纯文本错误）也给出提示，避免静默失败
      ElMessage.error('服务返回格式异常')
      return Promise.reject(data)
    }
    if (data.code !== 200) {
      ElMessage.error(data.message || '请求失败')
      if (data.code === 401) {
        logout()
        void router.push('/login')
        return Promise.reject(data)
      }
    }
    return data
  }) as any,
  (err: AxiosError<ApiResponse>) => {
    if (err.response) {
      const status = err.response.status
      if (status === 401 && !isLoginRequest(err.config?.url)) {
        ElMessage.error('登录状态已失效，请重新登录')
        logout()
        void router.push('/login')
      } else {
        ElMessage.error(pickErrorMessage(err.response.data))
      }
    } else {
      ElMessage.error('网络异常，请确认后端服务已启动')
    }
    return Promise.reject(err)
  }
)

export default service
