import type { UserInfo } from '@/schemas/user'

/**
 * 登录态本地存储
 *
 * token 与用户信息都放 localStorage：
 *   * 刷新页面不用重新登录；
 *   * 路由守卫可以在不请求后端的情况下先判断「有没有登录」。
 * 真正的权限判断仍然在后端（每个请求都会回库查用户），
 * 这里缓存的角色只用来决定「显示哪个菜单 / 进哪个首页」。
 */
const TOKEN_KEY = 'scs_token'
const USER_KEY = 'scs_user'

/** 获取 token，未登录时返回 null */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

/** 保存 token */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

/** 删除 token */
export function removeToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

/** 获取用户信息，未登录或数据损坏时返回 null */
export function getUserInfo(): UserInfo | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UserInfo
  } catch {
    return null
  }
}

/** 保存用户信息 */
export function setUserInfo(userInfo: UserInfo): void {
  localStorage.setItem(USER_KEY, JSON.stringify(userInfo))
}

/** 删除用户信息 */
export function removeUserInfo(): void {
  localStorage.removeItem(USER_KEY)
}

/** 登出：清除 token 和用户信息 */
export function logout(): void {
  removeToken()
  removeUserInfo()
}
