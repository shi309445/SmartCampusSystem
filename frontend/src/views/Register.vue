<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'
import { registerApi } from '@/api/auth'

/**
 * 学生自助注册
 *
 * 后端只能注册成学生（app/services/user/models.py: RegisterRequest 不收 role / status），
 * 管理员账号由管理员在「用户管理」里创建。
 */
interface RegisterFormModel {
  username: string
  name: string
  password: string
  confirm: string
}

const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive<RegisterFormModel>({
  username: '',
  name: '',
  password: '',
  confirm: ''
})

const rules: FormRules<RegisterFormModel> = {
  username: [
    { required: true, message: '请输入学号 / 账号', trigger: 'blur' },
    { min: 3, max: 50, message: '账号长度需在 3 ~ 50 个字符之间', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (/\s/.test(value)) callback(new Error('账号不能包含空格'))
        else callback()
      },
      trigger: 'blur'
    }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度需在 6 ~ 64 个字符之间', trigger: 'blur' }
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value !== form.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur'
    }
  ]
}

async function submit(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const res = await registerApi({
      username: form.username.trim(),
      password: form.password,
      name: form.name.trim() || undefined
    })
    if (res.code === 200) {
      ElMessage.success('注册成功，请登录')
      await router.push({ path: '/login', query: { username: form.username.trim() } })
    }
  } catch {
    // 账号重复等提示已由响应拦截器统一弹出
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="register">
    <div class="register-card">
      <div class="register-brand">
        <img class="register-logo" src="@/assets/imgs/logo.svg" alt="logo" />
        <div>
          <div class="register-title">注册学生账号</div>
          <div class="register-sub">兰州理工大学智能校园助手</div>
        </div>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @keyup.enter="submit"
      >
        <el-form-item label="学号 / 账号" prop="username">
          <el-input
            v-model="form.username"
            placeholder="例如 20210001"
            :prefix-icon="User"
            maxlength="50"
            clearable
          />
        </el-form-item>
        <el-form-item label="姓名（可选，默认与账号相同）" prop="name">
          <el-input v-model="form.name" placeholder="例如 张小明" maxlength="50" clearable />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 个字符"
            :prefix-icon="Lock"
            show-password
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            placeholder="再次输入密码"
            :prefix-icon="Lock"
            show-password
            maxlength="64"
          />
        </el-form-item>

        <el-button
          class="register-submit"
          type="primary"
          size="large"
          :loading="loading"
          @click="submit"
        >
          注 册
        </el-button>
      </el-form>

      <div class="register-foot">
        <span class="muted">已有账号？</span>
        <el-link type="primary" underline="never" @click="router.push('/login')">
          返回登录
        </el-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.register {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 40px 20px;
  background:
    radial-gradient(circle at 12% 18%, rgba(64, 128, 255, 0.16), transparent 42%),
    radial-gradient(circle at 88% 82%, rgba(111, 157, 255, 0.18), transparent 40%),
    #f5f7fa;
}

.register-card {
  width: 100%;
  max-width: 460px;
  padding: 32px;
  background-color: #fff;
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(31, 79, 216, 0.1);
}

.register-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}

.register-logo {
  width: 42px;
  height: 42px;
}

.register-title {
  font-size: 22px;
  font-weight: 700;
  color: #303133;
}

.register-sub {
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.register-submit {
  width: 100%;
  margin-top: 6px;
  letter-spacing: 4px;
}

.register-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 20px;
  font-size: 13px;
}
</style>
