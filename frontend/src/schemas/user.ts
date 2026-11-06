/** 用户 / 认证相关类型（对应 app/services/user/models.py 与 app/schemas/table.py） */

export type UserRole = 'student' | 'admin'

/** 用户状态：0-禁用, 1-正常（app/schemas/table.py） */
export type UserStatus = 0 | 1

export interface UserInfo {
  id: number
  username: string
  name: string
  role: UserRole
  email: string | null
  phone: string | null
  avatar: string | null
  status: UserStatus
  last_login_time: string | null
  create_time: string
  update_time: string
}

/** POST /api/auth/login → LoginResponse */
export interface LoginData {
  token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface LoginForm {
  username: string
  password: string
}

export interface RegisterForm {
  username: string
  password: string
  name?: string
}

/** PUT /api/auth/profile 只允许改这四项（角色 / 状态改不了） */
export interface ProfileForm {
  name?: string
  email?: string | null
  phone?: string | null
  avatar?: string | null
}

export interface PasswordForm {
  old_password: string
  new_password: string
}

/** 管理员新增用户（POST /api/users） */
export interface UserCreateForm {
  username: string
  password: string
  name: string
  role: UserRole
  email?: string | null
  phone?: string | null
  avatar?: string | null
  status?: UserStatus
}

/** 管理员更新用户（PUT /api/users/{id}，不传 = 不改） */
export interface UserUpdateForm {
  name?: string
  role?: UserRole
  status?: UserStatus
  email?: string | null
  phone?: string | null
  avatar?: string | null
}

/** GET /api/admin/usage/{user_id} → UsageOut */
export interface UsageInfo {
  user_id: number
  date: string
  tokens: number
  calls: number
  daily_tokens_limit: number
  daily_calls_limit: number
}
