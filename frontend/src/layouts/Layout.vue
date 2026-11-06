<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  Calendar,
  ChatDotRound,
  Expand,
  Fold,
  HomeFilled,
  OfficeBuilding,
  School,
  SwitchButton,
  User as UserIcon,
  UserFilled
} from '@element-plus/icons-vue'
import { getMeApi } from '@/api/auth'
import { useUser } from '@/utils/user'
import { useChatStore } from '@/utils/chatStore'
import { ADMIN_HOME_PATH, STUDENT_HOME_PATH, homePathOf } from '@/utils/role'
import AgentChat from '@/components/AgentChat.vue'

/**
 * 业务布局：顶栏（品牌 + 助手入口 + 用户菜单）+ 侧边菜单 + 内容区
 *
 * 菜单按角色生成：学生看「实验室预约 / 自习室 / 我的预约」，
 * 管理员看「实验室管理 / 自习室管理 / 预约审批 / 用户管理」。
 * 真正的权限判断在后端（每个接口都会校验），这里只决定「显示哪些入口」。
 */
interface MenuItem {
  path: string
  label: string
  icon: unknown
}

const route = useRoute()
const router = useRouter()
const { userInfo, isAdmin, updateUser, clearLoginData, displayName } = useUser()
const { openChat } = useChatStore()

const collapsed = ref(false)

const menus = computed<MenuItem[]>(() => {
  const items: MenuItem[] = [
    isAdmin.value
      ? { path: ADMIN_HOME_PATH, label: '系统首页', icon: HomeFilled }
      : { path: STUDENT_HOME_PATH, label: '学生首页', icon: HomeFilled },
    {
      path: '/manager/lab',
      label: isAdmin.value ? '实验室管理' : '实验室预约',
      icon: OfficeBuilding
    },
    {
      path: '/manager/room',
      label: isAdmin.value ? '自习室管理' : '自习室座位',
      icon: School
    },
    {
      path: '/manager/reservations',
      label: isAdmin.value ? '预约审批' : '我的预约',
      icon: Calendar
    }
  ]
  if (isAdmin.value) {
    items.push({ path: '/manager/users', label: '用户管理', icon: UserFilled })
  }
  return items
})

/** 当前高亮菜单：详情页落到父级菜单，个人中心不高亮 */
const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/manager/profile')) return ''
  const matched = menus.value.find((item) => path.startsWith(item.path))
  return matched?.path ?? homePathOf(userInfo.value?.role)
})

const pageTitle = computed(() => (route.meta.title as string | undefined) ?? '')
const roleText = computed(() => (isAdmin.value ? '管理员' : '学生'))
const avatarText = computed(() => (displayName.value || '同学').slice(0, 1))
const avatarClass = computed(() =>
  isAdmin.value ? 'is-admin' : 'is-student'
)

onMounted(async () => {
  // 每次进壳都刷新一次用户信息：管理员改了角色 / 禁用账号后能立刻生效
  try {
    const res = await getMeApi()
    if (res.code === 200) updateUser(res.data)
  } catch {
    // 401 等异常已由响应拦截器统一处理
  }
})

function goProfile(): void {
  void router.push('/manager/profile')
}

function onCommand(command: string): void {
  if (command === 'profile') {
    goProfile()
    return
  }
  if (command === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '退出登录', {
      type: 'warning',
      confirmButtonText: '退出登录',
      cancelButtonText: '取消'
    })
      .then(() => {
        clearLoginData()
        ElMessage.success('已退出登录')
        void router.push('/login')
      })
      .catch(() => undefined)
  }
}
</script>

<template>
  <div class="layout">
    <el-container class="layout__body">
      <el-header class="layout__header" height="60px">
        <div class="brand">
          <img class="brand__logo" src="@/assets/imgs/logo.svg" alt="logo" />
          <div class="brand__text">
            <div class="brand__title">兰州理工大学智能校园助手</div>
            <div class="brand__sub">Smart Campus Assistant · 校规问答 + 实验室 / 自习室预约</div>
          </div>
        </div>

        <div class="header-actions">
          <el-button class="assistant-btn" text @click="openChat">
            <el-icon><ChatDotRound /></el-icon>
            <span>智能助手</span>
          </el-button>

          <!-- 鼠标悬停弹出：个人中心 / 退出登录；点用户区域直接进个人中心 -->
          <el-dropdown
            class="header-user"
            trigger="hover"
            placement="bottom-end"
            :show-timeout="60"
            :hide-timeout="120"
            @command="onCommand"
          >
            <div class="header-user__inner" title="个人中心 / 退出登录" @click="goProfile">
              <el-avatar :size="32" class="header-user__avatar" :class="avatarClass">
                {{ avatarText }}
              </el-avatar>
              <div class="header-user__meta">
                <div class="header-user__name">{{ displayName }}</div>
                <div class="header-user__role">{{ roleText }}</div>
              </div>
              <el-icon class="header-user__arrow"><ArrowDown /></el-icon>
            </div>

            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  <span class="dropdown-account">
                    当前账号：{{ userInfo?.username || '—' }}（{{ roleText }}）
                  </span>
                </el-dropdown-item>
                <el-dropdown-item command="profile" :icon="UserIcon" divided>
                  个人中心
                </el-dropdown-item>
                <el-dropdown-item command="logout" :icon="SwitchButton" divided>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-container class="layout__content">
        <el-aside class="layout__aside" :width="collapsed ? '64px' : '220px'">
          <el-menu
            class="layout__menu"
            router
            :default-active="activeMenu"
            :collapse="collapsed"
            :collapse-transition="false"
          >
            <el-menu-item v-for="item in menus" :key="item.path" :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <template #title>{{ item.label }}</template>
            </el-menu-item>
          </el-menu>

          <div class="aside-footer">
            <el-button class="collapse-btn" text @click="collapsed = !collapsed">
              <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
              <span v-if="!collapsed">收起菜单</span>
            </el-button>
          </div>
        </el-aside>

        <el-main class="layout__main">
          <div class="crumb">
            <span class="crumb__dot" />
            <span>{{ pageTitle }}</span>
          </div>
          <router-view />
        </el-main>
      </el-container>
    </el-container>

    <!-- 智能助手常驻布局：切页面不会丢聊天记录 -->
    <AgentChat />
  </div>
</template>

<style scoped>
.layout {
  min-height: 100vh;
  background-color: #f5f7fa;
}

.layout__body {
  min-height: 100vh;
}

.layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 20px;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  position: sticky;
  top: 0;
  z-index: 100;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.brand__logo {
  width: 36px;
  height: 36px;
  flex: none;
}

.brand__text {
  min-width: 0;
}

.brand__title {
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
  color: #303133;
  white-space: nowrap;
}

.brand__sub {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.assistant-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--el-color-primary);
}

.header-user {
  outline: none;
}

.header-user :deep(.el-tooltip__trigger:focus-visible) {
  outline: none;
}

.header-user__inner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.header-user__inner:hover {
  background-color: #f5f7fa;
}

.header-user__avatar {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
}

.header-user__avatar.is-admin {
  background: linear-gradient(135deg, #f0a020, #ffc46b);
}

.header-user__avatar.is-student {
  background: linear-gradient(135deg, var(--el-color-primary), #6f9dff);
}

.header-user__meta {
  line-height: 1.3;
}

.header-user__name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.header-user__role {
  font-size: 12px;
  color: #909399;
}

.header-user__arrow {
  color: #909399;
}

.dropdown-account {
  font-size: 12px;
  color: #909399;
}

.layout__content {
  align-items: stretch;
}

.layout__aside {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  background-color: #fff;
  border-right: 1px solid #e4e7ed;
  transition: width 0.2s;
  overflow: hidden;
}

.layout__menu {
  flex: 1;
  border-right: none;
}

.layout__menu :deep(.el-menu-item.is-active) {
  background-color: var(--el-color-primary-light-9);
  font-weight: 600;
}

.aside-footer {
  padding: 10px;
  border-top: 1px solid #f0f2f5;
}

.collapse-btn {
  width: 100%;
  justify-content: flex-start;
  color: #909399;
}

.layout__main {
  padding: 16px 20px 90px;
  background-color: #f5f7fa;
  min-width: 0;
}

.crumb {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #909399;
}

.crumb__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--el-color-primary);
}

@media (max-width: 900px) {
  .brand__sub {
    display: none;
  }

  .brand__title {
    font-size: 15px;
  }

  .assistant-btn span {
    display: none;
  }

  .header-user__meta {
    display: none;
  }
}
</style>
