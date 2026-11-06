import type { UserInfo } from '@/schemas/user'

/**
 * 角色与「系统页面」的对应关系：
 *   admin   -> /manager/home     管理员首页（待审核 / 资源概览 / 用户管理）
 *   student -> /manager/student  学生首页（我的预约 / 实验室与自习室入口）
 * 登录成功、访问根路径、以及无权限回退都统一走这里，避免各处硬编码首页地址。
 */

export const ADMIN_HOME_PATH = '/manager/home'
export const STUDENT_HOME_PATH = '/manager/student'

/** 仅管理员可访问的路径前缀 */
export const ADMIN_ONLY_PREFIXES = ['/manager/users', '/manager/home']

/** 角色对应的系统首页 */
export function homePathOf(role: string | null | undefined): string {
  return role === 'admin' ? ADMIN_HOME_PATH : STUDENT_HOME_PATH
}

/** 当前登录用户对应的系统首页 */
export function homePathOfUser(user: UserInfo | null | undefined): string {
  return homePathOf(user?.role)
}

/** 该路径是否属于管理员的系统页面 */
export function isAdminOnlyPath(path: string): boolean {
  return ADMIN_ONLY_PREFIXES.some((prefix) => path.startsWith(prefix))
}

/**
 * 计算登录成功后的落地地址
 * 优先使用路由守卫带上的 redirect，但必须校验该地址与当前角色匹配，
 * 否则一律回到本角色的系统首页（例如学生不能用 redirect 落到管理后台）。
 */
export function resolveLandingPath(
  role: string | null | undefined,
  redirect?: unknown
): string {
  const home = homePathOf(role)
  if (typeof redirect !== 'string' || !redirect.startsWith('/manager')) return home

  const isAdmin = role === 'admin'
  if (!isAdmin && isAdminOnlyPath(redirect)) return home
  if (isAdmin && redirect === STUDENT_HOME_PATH) return home
  return redirect
}
