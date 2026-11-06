<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Lock, OfficeBuilding, Reading, School, User } from '@element-plus/icons-vue'
import { loginApi } from '@/api/auth'
import { useUser } from '@/utils/user'
import { resolveLandingPath } from '@/utils/role'
import type { LoginForm } from '@/schemas/user'

/**
 * 登录页
 *
 * 成功后按角色落地（管理员 → /manager/home，学生 → /manager/student）；
 * 被路由守卫拦截时带上的 redirect 只在与角色权限匹配时才会采用。
 */
const route = useRoute()
const router = useRouter()
const { saveLoginData } = useUser()

const form = reactive<LoginForm>({
  // 注册成功跳回登录页时会带上账号，直接回填省去重复输入
  username: typeof route.query.username === 'string' ? route.query.username : '',
  password: ''
})

const formRef = ref<FormInstance>()
const loading = ref(false)

const rules: FormRules<LoginForm> = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

/** 测试账号：点一下直接回填，方便验收（正式环境可以把这块删掉） */
const DEMO_ACCOUNTS = [
  { label: '管理员', username: 'admin', password: 'Admin@123456' },
  { label: '学生', username: '20210001', password: 'Student@123456' }
]

function fillDemo(account: { username: string; password: string }): void {
  form.username = account.username
  form.password = account.password
}

async function submit(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const res = await loginApi(form)
    if (res.code === 200) {
      saveLoginData(res.data)
      ElMessage.success(`欢迎回来，${res.data.user.name}`)
      await router.push(resolveLandingPath(res.data.user.role, route.query.redirect))
    }
  } catch {
    // 账号或密码错误等提示已由响应拦截器统一弹出
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <!-- 左侧品牌区：说明系统能做什么 -->
    <section class="login__brand">
      <div class="brand-top">
        <img class="brand-logo" src="@/assets/imgs/logo.svg" alt="logo" />
        <div>
          <div class="brand-name">兰州理工大学</div>
          <div class="brand-sub">智能校园助手</div>
        </div>
      </div>

      <div class="brand-copy">
        <h1>校规问答与场馆预约，一个入口搞定</h1>
        <p class="brand-desc">
          基于 RAG 的规章制度问答，实验室 / 自习室在线预约，
          写操作两阶段确认——助手只负责提议，提交前一定会问你。
        </p>

        <ul class="feature-list">
          <li>
            <span class="feature-icon"><el-icon><Reading /></el-icon></span>
            <div>
              <div class="feature-title">规章制度问答</div>
              <div class="feature-desc">奖学金、助学贷款、请销假，答案都带条款出处</div>
            </div>
          </li>
          <li>
            <span class="feature-icon"><el-icon><OfficeBuilding /></el-icon></span>
            <div>
              <div class="feature-title">实验室预约</div>
              <div class="feature-desc">按小时看空闲时段，提交后由管理员审核</div>
            </div>
          </li>
          <li>
            <span class="feature-icon"><el-icon><School /></el-icon></span>
            <div>
              <div class="feature-title">图书馆选座</div>
              <div class="feature-desc">按时段查空座、点座位下单，最长 4 小时</div>
            </div>
          </li>
        </ul>
      </div>

      <div class="brand-foot">Smart Campus System · 兰州理工大学</div>
      <span class="blob blob--1" />
      <span class="blob blob--2" />
    </section>

    <!-- 右侧登录表单 -->
    <section class="login__panel">
      <div class="login-box">
        <h2 class="login-title">登录</h2>
        <p class="login-sub">使用学号 / 工号登录校园助手</p>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-width="0"
          size="large"
          @keyup.enter="submit"
        >
          <el-form-item prop="username">
            <el-input
              v-model="form.username"
              placeholder="请输入账号（学号 / 工号）"
              :prefix-icon="User"
              clearable
            />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              :prefix-icon="Lock"
              show-password
            />
          </el-form-item>

          <el-button
            class="login-submit"
            type="primary"
            size="large"
            :loading="loading"
            @click="submit"
          >
            登 录
          </el-button>
        </el-form>

        <div class="login-links">
          <span class="muted">还没有账号？</span>
          <el-link type="primary" underline="never" @click="router.push('/register')">
            注册学生账号
          </el-link>
        </div>

        <div class="demo-block">
          <div class="demo-title">测试账号（点一下直接填入）</div>
          <div class="demo-list">
            <button
              v-for="account in DEMO_ACCOUNTS"
              :key="account.username"
              type="button"
              class="demo-item"
              @click="fillDemo(account)"
            >
              <span class="demo-role">{{ account.label }}</span>
              <span class="demo-name">{{ account.username }}</span>
              <span class="demo-pwd">{{ account.password }}</span>
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.login {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(420px, 0.95fr);
  min-height: 100vh;
  background-color: #f5f7fa;
}

/* ---------------- 左侧品牌区 ---------------- */
.login__brand {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 48px 56px;
  overflow: hidden;
  color: #fff;
  background: linear-gradient(135deg, #1f4fd8 0%, #4080ff 55%, #6f9dff 100%);
}

.brand-top {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-logo {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background-color: rgba(255, 255, 255, 0.16);
}

.brand-name {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 1px;
}

.brand-sub {
  font-size: 13px;
  opacity: 0.85;
}

.brand-copy h1 {
  max-width: 480px;
  font-size: 34px;
  line-height: 1.35;
  font-weight: 700;
}

.brand-desc {
  max-width: 460px;
  margin-top: 16px;
  font-size: 14px;
  line-height: 1.9;
  opacity: 0.9;
}

.feature-list {
  display: flex;
  flex-direction: column;
  gap: 18px;
  margin-top: 40px;
}

.feature-list li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.feature-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex: none;
  border-radius: 10px;
  font-size: 20px;
  background-color: rgba(255, 255, 255, 0.18);
}

.feature-title {
  font-size: 15px;
  font-weight: 600;
}

.feature-desc {
  margin-top: 2px;
  font-size: 13px;
  opacity: 0.82;
}

.brand-foot {
  font-size: 12px;
  letter-spacing: 0.5px;
  opacity: 0.7;
}

.blob {
  position: absolute;
  border-radius: 50%;
  background-color: rgba(255, 255, 255, 0.1);
  pointer-events: none;
}

.blob--1 {
  width: 340px;
  height: 340px;
  right: -120px;
  top: -90px;
}

.blob--2 {
  width: 240px;
  height: 240px;
  left: -80px;
  bottom: -80px;
  background-color: rgba(255, 255, 255, 0.08);
}

/* ---------------- 右侧表单 ---------------- */
.login__panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
}

.login-box {
  width: 100%;
  max-width: 380px;
}

.login-title {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
}

.login-sub {
  margin: 8px 0 28px;
  font-size: 13px;
  color: #909399;
}

.login-submit {
  width: 100%;
  letter-spacing: 4px;
}

.login-links {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 18px;
  font-size: 13px;
}

.demo-block {
  margin-top: 36px;
  padding: 14px;
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
  background-color: #fff;
}

.demo-title {
  margin-bottom: 10px;
  font-size: 12px;
  color: #909399;
}

.demo-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.demo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  font-size: 12px;
  color: #606266;
  text-align: left;
  background-color: #f5f7fa;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.demo-item:hover {
  background-color: var(--el-color-primary-light-9);
}

.demo-role {
  flex: none;
  padding: 1px 6px;
  font-size: 11px;
  color: var(--el-color-primary);
  background-color: var(--el-color-primary-light-9);
  border-radius: 4px;
}

.demo-name {
  font-weight: 600;
  color: #303133;
}

.demo-pwd {
  margin-left: auto;
  color: #909399;
}

@media (max-width: 992px) {
  .login {
    grid-template-columns: minmax(0, 1fr);
  }

  .login__brand {
    display: none;
  }
}
</style>
