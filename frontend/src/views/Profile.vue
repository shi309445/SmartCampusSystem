<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import {
  Calendar,
  Lock,
  Message,
  Phone,
  Postcard,
  User as UserIcon
} from '@element-plus/icons-vue'
import { updatePasswordApi, updateProfileApi } from '@/api/auth'
import { useUser } from '@/utils/user'
import { userRoleMeta, userStatusMeta } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'

/**
 * 个人中心
 *
 * 只能改 name / email / phone / avatar（后端 UserSelfUpdate 从模型层就没有 role / status），
 * 角色和状态由管理员在「用户管理」里维护。
 */
const { userInfo, isAdmin, updateUser, displayName } = useUser()

const profileRef = ref<FormInstance>()
const passwordRef = ref<FormInstance>()
const profileSaving = ref(false)
const passwordSaving = ref(false)

const profileForm = reactive({
  name: userInfo.value?.name ?? '',
  email: userInfo.value?.email ?? '',
  phone: userInfo.value?.phone ?? '',
  avatar: userInfo.value?.avatar ?? ''
})

const passwordForm = reactive({
  old_password: '',
  new_password: '',
  confirm: ''
})

const profileRules: FormRules<typeof profileForm> = {
  name: [
    { required: true, message: '请输入姓名', trigger: 'blur' },
    { max: 50, message: '姓名最长 50 个字符', trigger: 'blur' }
  ],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  phone: [{ max: 20, message: '手机号最长 20 个字符', trigger: 'blur' }]
}

const passwordRules: FormRules<typeof passwordForm> = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
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

const avatarText = computed(() => (displayName.value || '同学').slice(0, 1))
const roleMeta = computed(() => userRoleMeta(userInfo.value?.role ?? 'student'))
const statusMeta = computed(() => userStatusMeta(userInfo.value?.status ?? 1))

async function saveProfile(): Promise<void> {
  const valid = await profileRef.value?.validate().catch(() => false)
  if (!valid) return

  profileSaving.value = true
  try {
    const res = await updateProfileApi({
      name: profileForm.name.trim(),
      email: profileForm.email.trim() || null,
      phone: profileForm.phone.trim() || null,
      avatar: profileForm.avatar.trim() || null
    })
    if (res.code === 200) {
      updateUser(res.data)
      ElMessage.success('资料已更新')
    }
  } catch {
    // 提示已由响应拦截器统一弹出
  } finally {
    profileSaving.value = false
  }
}

async function savePassword(): Promise<void> {
  const valid = await passwordRef.value?.validate().catch(() => false)
  if (!valid) return

  passwordSaving.value = true
  try {
    const res = await updatePasswordApi({
      old_password: passwordForm.old_password,
      new_password: passwordForm.new_password
    })
    if (res.code === 200) {
      ElMessage.success('密码修改成功，下次登录请使用新密码')
      passwordForm.old_password = ''
      passwordForm.new_password = ''
      passwordForm.confirm = ''
      passwordRef.value?.clearValidate()
    }
  } catch {
    // 原密码错误等提示已由响应拦截器统一弹出
  } finally {
    passwordSaving.value = false
  }
}
</script>

<template>
  <div class="page profile">
    <div class="page-header">
      <div>
        <h2 class="page-title">个人中心</h2>
        <div class="page-desc">查看账号信息、修改资料与登录密码</div>
      </div>
    </div>

    <div class="profile__grid">
      <!-- 左：账号概览 -->
      <section class="panel profile-card">
        <div class="profile-card__top">
          <div class="profile-avatar" :class="isAdmin ? 'is-admin' : 'is-student'">
            {{ avatarText }}
          </div>
          <div class="profile-name">{{ displayName }}</div>
          <div class="profile-tags">
            <el-tag size="small" :type="roleMeta.type" effect="light">{{ roleMeta.label }}</el-tag>
            <el-tag size="small" :type="statusMeta.type" effect="plain">
              {{ statusMeta.label }}
            </el-tag>
          </div>
        </div>

        <dl class="info-list">
          <div class="info-row">
            <dt><el-icon><Postcard /></el-icon> 账号</dt>
            <dd>{{ userInfo?.username || '—' }}</dd>
          </div>
          <div class="info-row">
            <dt><el-icon><UserIcon /></el-icon> 姓名</dt>
            <dd>{{ userInfo?.name || '—' }}</dd>
          </div>
          <div class="info-row">
            <dt><el-icon><Message /></el-icon> 邮箱</dt>
            <dd>{{ userInfo?.email || '未填写' }}</dd>
          </div>
          <div class="info-row">
            <dt><el-icon><Phone /></el-icon> 手机号</dt>
            <dd>{{ userInfo?.phone || '未填写' }}</dd>
          </div>
          <div class="info-row">
            <dt><el-icon><Calendar /></el-icon> 最近登录</dt>
            <dd>{{ formatDateTime(userInfo?.last_login_time) }}</dd>
          </div>
          <div class="info-row">
            <dt><el-icon><Calendar /></el-icon> 注册时间</dt>
            <dd>{{ formatDateTime(userInfo?.create_time) }}</dd>
          </div>
        </dl>

        <div class="profile-tip">
          角色与账号状态由管理员维护；如需调整请联系实验室管理中心。
        </div>
      </section>

      <!-- 右：资料 + 密码 -->
      <div class="profile__forms">
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><UserIcon /></el-icon>
              修改资料
            </div>
          </div>
          <div class="panel__body">
            <el-form
              ref="profileRef"
              :model="profileForm"
              :rules="profileRules"
              label-width="86px"
            >
              <el-form-item label="姓名" prop="name">
                <el-input v-model="profileForm.name" maxlength="50" placeholder="请输入姓名" />
              </el-form-item>
              <el-form-item label="邮箱" prop="email">
                <el-input v-model="profileForm.email" maxlength="255" placeholder="例如 name@lut.edu.cn" />
              </el-form-item>
              <el-form-item label="手机号" prop="phone">
                <el-input v-model="profileForm.phone" maxlength="20" placeholder="例如 13800000000" />
              </el-form-item>
              <el-form-item label="头像地址">
                <el-input
                  v-model="profileForm.avatar"
                  maxlength="500"
                  placeholder="图片 URL，可留空"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="profileSaving" @click="saveProfile">
                  保存资料
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </section>

        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Lock /></el-icon>
              修改密码
            </div>
            <span class="muted">修改后立即生效，请使用新密码重新登录</span>
          </div>
          <div class="panel__body">
            <el-form
              ref="passwordRef"
              :model="passwordForm"
              :rules="passwordRules"
              label-width="86px"
            >
              <el-form-item label="原密码" prop="old_password">
                <el-input
                  v-model="passwordForm.old_password"
                  type="password"
                  show-password
                  maxlength="64"
                  placeholder="请输入当前密码"
                />
              </el-form-item>
              <el-form-item label="新密码" prop="new_password">
                <el-input
                  v-model="passwordForm.new_password"
                  type="password"
                  show-password
                  maxlength="64"
                  placeholder="6 ~ 64 个字符"
                />
              </el-form-item>
              <el-form-item label="确认密码" prop="confirm">
                <el-input
                  v-model="passwordForm.confirm"
                  type="password"
                  show-password
                  maxlength="64"
                  placeholder="再次输入新密码"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="passwordSaving" @click="savePassword">
                  修改密码
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.profile__grid {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.profile-card {
  padding: 24px 20px;
  text-align: center;
}

.profile-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 76px;
  height: 76px;
  margin: 0 auto 12px;
  font-size: 30px;
  font-weight: 600;
  color: #fff;
  border-radius: 50%;
}

.profile-avatar.is-admin {
  background: linear-gradient(135deg, #f0a020, #ffc46b);
}

.profile-avatar.is-student {
  background: linear-gradient(135deg, var(--el-color-primary), #6f9dff);
}

.profile-name {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.profile-tags {
  display: flex;
  justify-content: center;
  gap: 6px;
  margin-top: 8px;
}

.info-list {
  margin: 22px 0 0;
  text-align: left;
}

.info-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0;
  font-size: 13px;
  border-bottom: 1px dashed #f0f2f5;
}

.info-row:last-child {
  border-bottom: none;
}

.info-row dt {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: none;
  color: #909399;
}

.info-row dd {
  margin: 0;
  color: #303133;
  text-align: right;
  word-break: break-all;
}

.profile-tip {
  margin-top: 18px;
  padding: 10px;
  font-size: 12px;
  line-height: 1.7;
  color: #909399;
  text-align: left;
  background-color: #f5f7fa;
  border-radius: 6px;
}

.profile__forms {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.profile__forms .panel + .panel {
  margin-top: 0;
}

@media (max-width: 992px) {
  .profile__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
