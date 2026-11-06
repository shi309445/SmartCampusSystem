// 仅供页面 setup 函数使用
import { computed, ref } from 'vue'
import { getUserInfo, removeToken, removeUserInfo, setToken, setUserInfo } from './auth'
import type { LoginData, UserInfo } from '@/schemas/user'

/** 响应式的当前用户：登录 / 改资料 / 退出都会更新它，页面里的计算属性随之刷新 */
const userInfo = ref<UserInfo | null>(getUserInfo())

export function useUser() {
  /** 当前用户是否管理员 */
  const isAdmin = computed(() => userInfo.value?.role === 'admin')

  /** 当前用户是否学生 */
  const isStudent = computed(() => userInfo.value?.role === 'student')

  /** 是否已登录 */
  const isLogin = computed(() => userInfo.value !== null)

  /** 展示用名字：姓名优先，其次账号 */
  const displayName = computed(
    () => userInfo.value?.name || userInfo.value?.username || '同学'
  )

  /** 保存登录数据：token + 用户信息 */
  function saveLoginData(data: LoginData): void {
    setToken(data.token)
    setUserInfo(data.user)
    userInfo.value = data.user
  }

  /** 更新用户信息（改资料 / 拉 /api/auth/me 之后调） */
  function updateUser(user: UserInfo): void {
    setUserInfo(user)
    userInfo.value = user
  }

  /** 从 localStorage 重新读取用户信息 */
  function reloadUser(): void {
    userInfo.value = getUserInfo()
  }

  /** 退出登录：清空本地缓存与响应式状态 */
  function clearLoginData(): void {
    removeToken()
    removeUserInfo()
    userInfo.value = null
  }

  return {
    userInfo,
    isAdmin,
    isStudent,
    isLogin,
    displayName,
    saveLoginData,
    updateUser,
    reloadUser,
    clearLoginData
  }
}
