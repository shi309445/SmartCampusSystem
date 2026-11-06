/**
 * 全局字典：把后端的数字状态码统一映射成文案与标签颜色
 *
 * 取值来源：
 *   app/services/laboratory/table.py   实验室 / 设备 / 预约状态
 *   app/services/library/table.py      自习室 / 座位 / 预约状态
 *   app/schemas/table.py               用户角色 / 用户状态
 *
 * 所有页面都从这里拿文案，不再各自写 `status === 0 ? '待审核' : ...`，
 * 以后后端加状态（比如「已签到」）只改这一处。
 */

export type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'

/** 实验室 / 自习室状态：0-关闭, 1-开放 */
export const LAB_STATUS_CLOSED = 0
export const LAB_STATUS_OPEN = 1

/** 设备 / 座位状态：0-不可用, 1-可用 */
export const EQUIPMENT_STATUS_DISABLED = 0
export const EQUIPMENT_STATUS_AVAILABLE = 1

/** 预约状态：0-待审核, 1-预约成功, 2-预约失败, 3-已取消 */
export const RESERVATION_STATUS_PENDING = 0
export const RESERVATION_STATUS_APPROVED = 1
export const RESERVATION_STATUS_REJECTED = 2
export const RESERVATION_STATUS_CANCELLED = 3

/** 「已处理」页签需要查询的状态集合 */
export const RESERVATION_HANDLED_STATUSES: number[] = [
  RESERVATION_STATUS_APPROVED,
  RESERVATION_STATUS_REJECTED,
  RESERVATION_STATUS_CANCELLED
]

/** 还能被取消的状态：待审核 / 已通过（已驳回、已取消是终态） */
export const RESERVATION_CANCELLABLE_STATUSES: number[] = [
  RESERVATION_STATUS_PENDING,
  RESERVATION_STATUS_APPROVED
]

/** 用户状态：0-禁用, 1-正常 */
export const USER_STATUS_DISABLED = 0
export const USER_STATUS_NORMAL = 1

/** 用户角色 */
export type UserRole = 'student' | 'admin'

interface StatusMeta {
  label: string
  type: TagType
}

const LAB_STATUS_MAP: Record<number, StatusMeta> = {
  [LAB_STATUS_CLOSED]: { label: '已关闭', type: 'info' },
  [LAB_STATUS_OPEN]: { label: '开放中', type: 'success' }
}

const EQUIPMENT_STATUS_MAP: Record<number, StatusMeta> = {
  [EQUIPMENT_STATUS_DISABLED]: { label: '不可用', type: 'danger' },
  [EQUIPMENT_STATUS_AVAILABLE]: { label: '可用', type: 'success' }
}

const RESERVATION_STATUS_MAP: Record<number, StatusMeta> = {
  [RESERVATION_STATUS_PENDING]: { label: '待审核', type: 'warning' },
  [RESERVATION_STATUS_APPROVED]: { label: '预约成功', type: 'success' },
  [RESERVATION_STATUS_REJECTED]: { label: '预约失败', type: 'danger' },
  [RESERVATION_STATUS_CANCELLED]: { label: '已取消', type: 'info' }
}

const USER_STATUS_MAP: Record<number, StatusMeta> = {
  [USER_STATUS_DISABLED]: { label: '已禁用', type: 'danger' },
  [USER_STATUS_NORMAL]: { label: '正常', type: 'success' }
}

const USER_ROLE_MAP: Record<string, StatusMeta> = {
  student: { label: '学生', type: 'primary' },
  admin: { label: '管理员', type: 'warning' }
}

const UNKNOWN: StatusMeta = { label: '未知', type: 'info' }

function pick(map: Record<string, StatusMeta>, key: string | number): StatusMeta {
  return map[String(key)] ?? UNKNOWN
}

/** 实验室 / 自习室状态文案 / 标签色 */
export function labStatusMeta(status: number): StatusMeta {
  return pick(LAB_STATUS_MAP, status)
}

/** 设备 / 座位状态文案 / 标签色 */
export function equipmentStatusMeta(status: number): StatusMeta {
  return pick(EQUIPMENT_STATUS_MAP, status)
}

/** 预约状态文案 / 标签色 */
export function reservationStatusMeta(status: number): StatusMeta {
  return pick(RESERVATION_STATUS_MAP, status)
}

/** 用户状态文案 / 标签色 */
export function userStatusMeta(status: number): StatusMeta {
  return pick(USER_STATUS_MAP, status)
}

/** 用户角色文案 / 标签色 */
export function userRoleMeta(role: string): StatusMeta {
  return pick(USER_ROLE_MAP, role)
}

/** 状态筛选下拉框的选项（按状态码升序） */
export const RESERVATION_STATUS_OPTIONS = Object.entries(RESERVATION_STATUS_MAP).map(
  ([value, meta]) => ({ value: Number(value), label: meta.label })
)

/** 界面里给状态圆点 / 图标用的 class 后缀 */
export const TAG_COLOR_CLASS: Record<TagType, string> = {
  primary: 'is-primary',
  success: 'is-success',
  info: 'is-info',
  warning: 'is-warning',
  danger: 'is-danger'
}

export function colorClass(type: TagType): string {
  return TAG_COLOR_CLASS[type]
}

/** 助手返回的写操作名 → 中文（确认卡片标题用） */
export const TOOL_NAME_LABELS: Record<string, string> = {
  make_reservation: '预约申请',
  cancel_my_reservation: '取消预约'
}

export function toolNameLabel(name: string): string {
  return TOOL_NAME_LABELS[name] ?? name
}

/** 决策层阶段 → 中文（助手气泡下方的小标签，排障时有用） */
export const DECISION_STAGE_LABELS: Record<string, string> = {
  rule_hit: '规则命中',
  intent_denied: '未放行检索',
  offtopic_short_circuit: '非校园话题',
  force_rag: '强制检索'
}

export function decisionStageLabel(stage: string | null | undefined): string {
  if (!stage) return ''
  return DECISION_STAGE_LABELS[stage] ?? stage
}
