<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { DataLine, Delete, Edit, Key, Plus, Refresh, Search, UserFilled } from '@element-plus/icons-vue'
import {
  createUserApi,
  deleteUserApi,
  getUserPageApi,
  getUserUsageApi,
  resetUserPasswordApi,
  updateUserApi
} from '@/api/user'
import type { UsageInfo, UserInfo, UserRole } from '@/schemas/user'
import { useUser } from '@/utils/user'
import { userRoleMeta, userStatusMeta } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'

/**
 * 用户管理（管理员）
 *
 * 管理员能建号、改角色 / 状态、重置密码、删除账号，也能查看某个学生今天的助手用量。
 * 规则都在后端：不能把自己降级 / 禁用、不能删除有预约记录的用户（409）。
 */
const { userInfo, displayName } = useUser()

const loading = ref(false)
const users = ref<UserInfo[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)

const filters = reactive<{
  keyword: string
  role: string | null
  status: number | null
}>({
  keyword: '',
  role: null,
  status: null
})

const roleOptions = [
  { label: '全部角色', value: null },
  { label: '学生', value: 'student' },
  { label: '管理员', value: 'admin' }
]

const statusOptions = [
  { label: '全部状态', value: null },
  { label: '正常', value: 1 },
  { label: '已禁用', value: 0 }
]

// 新增 / 编辑用户
const userDialogVisible = ref(false)
const userSaving = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const editingUser = ref<UserInfo | null>(null)
const userFormRef = ref<FormInstance>()
const userForm = reactive({
  username: '',
  password: '',
  name: '',
  role: 'student' as UserRole,
  email: '',
  phone: '',
  status: 1
})

// 重置密码
const passwordVisible = ref(false)
const passwordSaving = ref(false)
const passwordTarget = ref<UserInfo | null>(null)
const passwordFormRef = ref<FormInstance>()
const passwordForm = reactive({ new_password: '', confirm: '' })

// 用量
const usageVisible = ref(false)
const usageLoading = ref(false)
const usageTarget = ref<UserInfo | null>(null)
const usage = ref<UsageInfo | null>(null)

const isEdit = computed(() => dialogMode.value === 'edit')
const dialogTitle = computed(() => (isEdit.value ? '编辑用户' : '新增用户'))

const tokenPercent = computed(() => {
  if (!usage.value || usage.value.daily_tokens_limit <= 0) return 0
  return Math.min(100, Math.round((usage.value.tokens / usage.value.daily_tokens_limit) * 100))
})
const callPercent = computed(() => {
  if (!usage.value || usage.value.daily_calls_limit <= 0) return 0
  return Math.min(100, Math.round((usage.value.calls / usage.value.daily_calls_limit) * 100))
})

const userRules = computed<FormRules<typeof userForm>>(() => ({
  username: isEdit.value
    ? []
    : [
        { required: true, message: '请输入账号', trigger: 'blur' },
        { min: 3, max: 50, message: '账号长度需在 3 ~ 50 个字符之间', trigger: 'blur' }
      ],
  password: isEdit.value
    ? []
    : [
        { required: true, message: '请输入初始密码', trigger: 'blur' },
        { min: 6, max: 64, message: '密码长度需在 6 ~ 64 个字符之间', trigger: 'blur' }
      ],
  name: [
    { required: true, message: '请输入姓名', trigger: 'blur' },
    { max: 50, message: '姓名最长 50 个字符', trigger: 'blur' }
  ],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }]
}))

const passwordRules: FormRules<typeof passwordForm> = {
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度需在 6 ~ 64 个字符之间', trigger: 'blur' }
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value !== passwordForm.new_password) callback(new Error('两次输入的新密码不一致'))
        else callback()
      },
      trigger: 'blur'
    }
  ]
}

async function loadUsers(): Promise<void> {
  loading.value = true
  try {
    const res = await getUserPageApi({
      page: page.value,
      page_size: pageSize.value,
      keyword: filters.keyword.trim() || undefined,
      role: filters.role ? [filters.role] : undefined,
      status: filters.status === null ? undefined : filters.status
    })
    users.value = res.data?.data ?? []
    total.value = res.data?.total ?? 0
  } catch {
    users.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void loadUsers()
}

function reset(): void {
  filters.keyword = ''
  filters.role = null
  filters.status = null
  search()
}

function openCreate(): void {
  dialogMode.value = 'create'
  editingUser.value = null
  userForm.username = ''
  userForm.password = ''
  userForm.name = ''
  userForm.role = 'student'
  userForm.email = ''
  userForm.phone = ''
  userForm.status = 1
  userDialogVisible.value = true
  userFormRef.value?.clearValidate()
}

function openEdit(user: UserInfo): void {
  dialogMode.value = 'edit'
  editingUser.value = user
  userForm.username = user.username
  userForm.password = ''
  userForm.name = user.name
  userForm.role = user.role
  userForm.email = user.email ?? ''
  userForm.phone = user.phone ?? ''
  userForm.status = user.status
  userDialogVisible.value = true
  userFormRef.value?.clearValidate()
}

async function saveUser(): Promise<void> {
  const valid = await userFormRef.value?.validate().catch(() => false)
  if (!valid) return

  userSaving.value = true
  try {
    if (isEdit.value && editingUser.value) {
      const res = await updateUserApi(editingUser.value.id, {
        name: userForm.name.trim(),
        role: userForm.role,
        status: userForm.status === 1 ? 1 : 0,
        email: userForm.email.trim() || null,
        phone: userForm.phone.trim() || null
      })
      if (res.code === 200) {
        ElMessage.success('用户已更新')
        userDialogVisible.value = false
        await loadUsers()
      }
    } else {
      const res = await createUserApi({
        username: userForm.username.trim(),
        password: userForm.password,
        name: userForm.name.trim(),
        role: userForm.role,
        email: userForm.email.trim() || null,
        phone: userForm.phone.trim() || null,
        status: userForm.status === 1 ? 1 : 0
      })
      if (res.code === 200) {
        ElMessage.success('用户已创建')
        userDialogVisible.value = false
        await loadUsers()
      }
    }
  } catch {
    // 账号重复 / 不能改自己角色等提示已由响应拦截器弹出
  } finally {
    userSaving.value = false
  }
}

function openResetPassword(user: UserInfo): void {
  passwordTarget.value = user
  passwordForm.new_password = ''
  passwordForm.confirm = ''
  passwordVisible.value = true
  passwordFormRef.value?.clearValidate()
}

async function submitResetPassword(): Promise<void> {
  const target = passwordTarget.value
  if (!target) return

  const valid = await passwordFormRef.value?.validate().catch(() => false)
  if (!valid) return

  passwordSaving.value = true
  try {
    const res = await resetUserPasswordApi(target.id, passwordForm.new_password)
    if (res.code === 200) {
      ElMessage.success(`已重置 ${target.name} 的密码`)
      passwordVisible.value = false
    }
  } catch {
    // 与原密码相同等提示已由响应拦截器弹出
  } finally {
    passwordSaving.value = false
  }
}

async function openUsage(user: UserInfo): Promise<void> {
  usageTarget.value = user
  usage.value = null
  usageVisible.value = true
  usageLoading.value = true
  try {
    const res = await getUserUsageApi(user.id)
    usage.value = res.data
  } catch {
    usage.value = null
  } finally {
    usageLoading.value = false
  }
}

async function removeUser(user: UserInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${user.name}（${user.username}）」吗？存在预约记录的用户无法删除。`,
      '删除用户',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  try {
    const res = await deleteUserApi(user.id)
    if (res.code === 200) {
      ElMessage.success('用户已删除')
      if (users.value.length === 1 && page.value > 1) page.value -= 1
      await loadUsers()
    }
  } catch {
    // 409（存在预约）/ 不能删自己等提示已由响应拦截器弹出
  }
}

function isSelf(user: UserInfo): boolean {
  return user.id === userInfo.value?.id
}

onMounted(loadUsers)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">用户管理</h2>
        <div class="page-desc">
          共 {{ total }} 个账号 · 当前登录：{{ displayName }}（管理员不能禁用或删除自己）
        </div>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" @click="loadUsers">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增用户</el-button>
      </div>
    </div>

    <section class="panel filters">
      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          class="filters__keyword"
          placeholder="搜索账号 / 姓名 / 邮箱 / 手机号"
          :prefix-icon="Search"
          clearable
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="filters.role" class="filters__select" placeholder="全部角色">
          <el-option
            v-for="option in roleOptions"
            :key="String(option.value)"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-select v-model="filters.status" class="filters__select" placeholder="全部状态">
          <el-option
            v-for="option in statusOptions"
            :key="String(option.value)"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>
    </section>

    <section class="panel table-panel">
      <div v-loading="loading" class="table-wrap">
        <el-table :data="users" stripe empty-text="没有符合条件的用户">
          <el-table-column label="账号" min-width="130">
            <template #default="{ row }">
              <div class="user-cell">
                <span class="user-cell__avatar" :class="row.role === 'admin' ? 'is-admin' : 'is-student'">
                  {{ (row.name || row.username).slice(0, 1) }}
                </span>
                <div class="user-cell__meta">
                  <div class="user-cell__name">{{ row.name }}</div>
                  <div class="user-cell__account">{{ row.username }}</div>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="角色" width="95" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="userRoleMeta(row.role).type" effect="light">
                {{ userRoleMeta(row.role).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="95" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="userStatusMeta(row.status).type" effect="plain">
                {{ userStatusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="邮箱" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.email || '—' }}</template>
          </el-table-column>
          <el-table-column label="手机号" width="130">
            <template #default="{ row }">{{ row.phone || '—' }}</template>
          </el-table-column>
          <el-table-column label="最近登录" width="150">
            <template #default="{ row }">{{ formatDateTime(row.last_login_time) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="285" align="center" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" :icon="Edit" @click="openEdit(row)">
                编辑
              </el-button>
              <el-button link type="warning" size="small" :icon="Key" @click="openResetPassword(row)">
                重置密码
              </el-button>
              <el-button link type="info" size="small" :icon="DataLine" @click="openUsage(row)">
                用量
              </el-button>
              <el-button
                link
                type="danger"
                size="small"
                :icon="Delete"
                :disabled="isSelf(row)"
                @click="removeUser(row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="total > pageSize" class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="loadUsers"
        />
      </div>
    </section>

    <!-- 新增 / 编辑用户 -->
    <el-dialog v-model="userDialogVisible" :title="dialogTitle" width="520px" append-to-body>
      <el-form ref="userFormRef" :model="userForm" :rules="userRules" label-width="90px">
        <el-form-item label="账号" prop="username">
          <el-input
            v-model="userForm.username"
            :disabled="isEdit"
            maxlength="50"
            placeholder="学号 / 工号，创建后不可修改"
          />
        </el-form-item>
        <el-form-item v-if="!isEdit" label="初始密码" prop="password">
          <el-input
            v-model="userForm.password"
            type="password"
            show-password
            maxlength="64"
            placeholder="6 ~ 64 个字符"
          />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="userForm.name" maxlength="50" placeholder="真实姓名" />
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="userForm.role">
            <el-radio value="student">学生</el-radio>
            <el-radio value="admin">管理员</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="userForm.status"
            :active-value="1"
            :inactive-value="0"
            active-text="正常"
            inactive-text="禁用"
            inline-prompt
          />
          <span v-if="isEdit && editingUser && isSelf(editingUser)" class="form-hint">
            不能禁用自己
          </span>
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="userForm.email" maxlength="255" placeholder="可选" />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input v-model="userForm.phone" maxlength="20" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="userSaving" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="passwordVisible" title="重置密码" width="420px" append-to-body>
      <div v-if="passwordTarget" class="dialog-hint">
        正在重置「{{ passwordTarget.name }}（{{ passwordTarget.username }}）」的密码。
      </div>
      <el-form ref="passwordFormRef" :model="passwordForm" :rules="passwordRules" label-width="80px">
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="passwordForm.new_password"
            type="password"
            show-password
            maxlength="64"
            placeholder="6 ~ 64 个字符"
          />
        </el-form-item>
        <el-form-item label="确认" prop="confirm">
          <el-input
            v-model="passwordForm.confirm"
            type="password"
            show-password
            maxlength="64"
            placeholder="再次输入新密码"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordVisible = false">取消</el-button>
        <el-button type="primary" :loading="passwordSaving" @click="submitResetPassword">
          重置密码
        </el-button>
      </template>
    </el-dialog>

    <!-- 助手用量 -->
    <el-dialog v-model="usageVisible" title="今日助手用量" width="440px" append-to-body>
      <div v-loading="usageLoading">
        <div v-if="usageTarget" class="dialog-hint">
          {{ usageTarget.name }}（{{ usageTarget.username }}）
        </div>

        <template v-if="usage">
          <div class="usage-row">
            <span class="usage-label">Token</span>
            <span class="usage-value">{{ usage.tokens.toLocaleString() }}</span>
          </div>
          <el-progress
            :percentage="tokenPercent"
            :stroke-width="8"
            :show-text="false"
            :status="tokenPercent >= 90 ? 'exception' : undefined"
          />
          <div class="usage-hint">
            日配额 {{ usage.daily_tokens_limit.toLocaleString() }} tokens
          </div>

          <div class="usage-row usage-row--mt">
            <span class="usage-label">调用次数</span>
            <span class="usage-value">{{ usage.calls }} / {{ usage.daily_calls_limit }}</span>
          </div>
          <el-progress
            :percentage="callPercent"
            :stroke-width="8"
            :show-text="false"
            :status="callPercent >= 90 ? 'exception' : undefined"
          />
          <div class="usage-hint">统计日期 {{ usage.date }}</div>
        </template>
        <el-empty v-else-if="!usageLoading" description="暂时取不到该用户的用量" :image-size="64" />
      </div>
      <template #footer>
        <el-button @click="usageVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filters {
  padding: 14px 16px;
  margin-bottom: 16px;
}

.filters__keyword {
  width: 280px;
}

.filters__select {
  width: 140px;
}

.table-panel {
  padding: 8px 8px 4px;
}

.table-wrap {
  min-height: 240px;
}

.user-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-cell__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  border-radius: 50%;
}

.user-cell__avatar.is-admin {
  background: linear-gradient(135deg, #f0a020, #ffc46b);
}

.user-cell__avatar.is-student {
  background: linear-gradient(135deg, var(--el-color-primary), #6f9dff);
}

.user-cell__name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.user-cell__account {
  font-size: 11px;
  color: #909399;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}

.dialog-hint {
  margin-bottom: 12px;
  font-size: 13px;
  color: #606266;
}

.usage-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}

.usage-row--mt {
  margin-top: 20px;
}

.usage-label {
  font-size: 12px;
  color: #909399;
}

.usage-value {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.usage-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

@media (max-width: 768px) {
  .filters__keyword,
  .filters__select {
    width: 100%;
  }
}
</style>
