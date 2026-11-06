import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getToken, getUserInfo } from '@/utils/auth'
import { homePathOf } from '@/utils/role'

/**
 * 路由表
 *
 * 结构（参考 Laboratory-Reservation 的「登录 + 角色双首页」做法）：
 *
 *     /login /register                   独立页面
 *     /manager/*                         需要登录的壳（Layout：顶栏 + 侧边菜单 + 助手）
 *         home        管理员首页          仅 admin
 *         student     学生首页            仅 student
 *         lab         实验室列表 / 管理    两者都能进（按钮按角色显隐）
 *         lab/:id     实验室详情 + 预约
 *         room        自习室列表 / 管理
 *         room/:id    自习室详情 + 选座预约
 *         reservations 预约管理（学生）/ 预约审批（管理员）
 *         users       用户管理            仅 admin
 *         profile     个人中心
 */

/** 当前角色对应的系统首页；未登录时直接去登录页 */
function landingPath(): string {
  return getToken() ? homePathOf(getUserInfo()?.role) : '/login'
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: () => landingPath() },
    {
      path: '/manager',
      name: 'Layout',
      component: () => import('@/layouts/Layout.vue'),
      redirect: () => landingPath(),
      children: [
        {
          path: 'home',
          name: 'AdminHome',
          component: () => import('@/views/home/AdminHome.vue'),
          meta: { title: '系统首页', requiresAdmin: true }
        },
        {
          path: 'student',
          name: 'StudentHome',
          component: () => import('@/views/home/StudentHome.vue'),
          meta: { title: '学生首页', requiresStudent: true }
        },
        {
          path: 'lab',
          name: 'Lab',
          component: () => import('@/views/Lab.vue'),
          meta: { title: '实验室' }
        },
        {
          path: 'lab/:id',
          name: 'LabDetail',
          component: () => import('@/views/LabDetail.vue'),
          meta: { title: '实验室详情' }
        },
        {
          path: 'room',
          name: 'Room',
          component: () => import('@/views/Room.vue'),
          meta: { title: '自习室' }
        },
        {
          path: 'room/:id',
          name: 'RoomDetail',
          component: () => import('@/views/RoomDetail.vue'),
          meta: { title: '自习室详情' }
        },
        {
          path: 'reservations',
          name: 'Reservations',
          component: () => import('@/views/Reservations.vue'),
          meta: { title: '预约管理' }
        },
        {
          path: 'users',
          name: 'Users',
          component: () => import('@/views/Users.vue'),
          meta: { title: '用户管理', requiresAdmin: true }
        },
        {
          path: 'profile',
          name: 'Profile',
          component: () => import('@/views/Profile.vue'),
          meta: { title: '个人中心' }
        }
      ]
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/Login.vue'),
      meta: { title: '登录' }
    },
    {
      path: '/register',
      name: 'Register',
      component: () => import('@/views/Register.vue'),
      meta: { title: '注册' }
    },
    { path: '/:pathMatch(.*)*', redirect: () => landingPath() }
  ]
})

// 路由守卫：验证 token + 按角色做页面权限控制
router.beforeEach((to) => {
  // 浏览器标题跟着路由走，排障时一眼能看出在哪一页
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} · 智能校园助手` : '兰州理工大学智能校园助手'

  const token = getToken()

  if (to.path === '/login' || to.path === '/register') {
    // 已登录访问登录页：直接回自己的首页，避免「登录成功后又停在登录页」
    if (token) return { path: homePathOf(getUserInfo()?.role) }
    return true
  }

  if (!token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  const user = getUserInfo()
  const isAdmin = user?.role === 'admin'
  const home = homePathOf(user?.role)

  // 管理员专属页面（用户管理、管理后台首页）
  if (to.meta.requiresAdmin && !isAdmin) {
    ElMessage.warning('该页面仅管理员可访问')
    return { path: home }
  }

  // 学生专属页面（学生首页）
  if (to.meta.requiresStudent && isAdmin) {
    return { path: home }
  }

  return true
})

export default router
