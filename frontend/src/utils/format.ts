/** 展示层格式化工具：时间、容量、图片地址、日期区间 */

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']

function pad(num: number): string {
  return String(num).padStart(2, '0')
}

/** 把后端返回的 "08:00:00" 显示为 "08:00" */
export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 5)
}

/**
 * 从完整时间串里取出时分："2026-10-29T09:00:00" / "09:00:00" / "09:00" -> "09:00"
 *
 * 时段接口（/api/labs/{id}/schedule）返回的是完整 datetime，
 * 不能直接用 formatTime（它按「只有时间」的格式切前 5 位）。
 */
export function timeOfDay(value: string | null | undefined): string {
  if (!value) return ''
  const iso = value.match(/T(\d{2}:\d{2})/)
  if (iso?.[1]) return iso[1]
  const clock = value.match(/^(\d{2}:\d{2})/)
  if (clock?.[1]) return clock[1]
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 5)
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 开放时间区间，如 "08:00 - 22:00"；未设置时返回 "全天" */
export function formatOpenTime(
  openTime: string | null | undefined,
  closeTime: string | null | undefined
): string {
  if (!openTime || !closeTime) return '全天'
  return `${formatTime(openTime)} - ${formatTime(closeTime)}`
}

/** 把后端返回的 ISO 时间显示为 "2026-01-01 10:00" */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/** 只显示日期部分 "2026-01-01" */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return String(value)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 预约时段，如 "2026-01-01 10:00 ~ 12:00"（同一天不重复日期） */
export function formatReserveRange(
  start: string | null | undefined,
  end: string | null | undefined
): string {
  if (!start || !end) return '—'
  const startText = formatDateTime(start)
  const endText = formatDateTime(end)
  if (startText.slice(0, 10) === endText.slice(0, 10)) {
    return `${startText} ~ ${endText.slice(11)}`
  }
  return `${startText} ~ ${endText}`
}

/** 可容纳人数：0 视为未设置 */
export function capacityText(maxpeople: number | null | undefined): string {
  if (maxpeople === null || maxpeople === undefined || maxpeople <= 0) return '未设置'
  return `${maxpeople} 人`
}

/** 座位数上限展示 */
export function seatCountText(capacity: number | null | undefined): string {
  if (capacity === null || capacity === undefined || capacity <= 0) return '未设置'
  return `${capacity} 个座位`
}

/** 拼接后端静态资源地址（/static/xxx.png -> http://127.0.0.1:8000/static/xxx.png） */
export function fileUrl(path: string | null | undefined): string {
  if (!path) return ''
  if (/^https?:\/\//i.test(path)) return path
  const base = import.meta.env.VITE_API_BASE_URL ?? ''
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`
}

/** "2026-01-01" + "10:00:00" -> "2026-01-01T10:00:00"（后端按本地 naive 时间解析） */
export function joinDateTime(date: string, time: string): string {
  const normalized = time.length === 5 ? `${time}:00` : time
  return `${date}T${normalized}`
}

/** 把 Date 转成 el-date-picker 使用的 "YYYY-MM-DD" */
export function formatDateOnly(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 把 Date 转成 el-time-select / 后端使用的 "HH:mm:00" */
export function formatClock(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

/** "HH:mm:00" -> "HH:mm"（给 el-time-select 的 v-model 用） */
export function clockText(value: string | null | undefined): string {
  return value ? value.slice(0, 5) : ''
}

/** 解析后端 / 助手返回的时间字符串，非法时返回 null */
export function parseDateTime(value: unknown): Date | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 生成 [今天, 今天 + days] 的可选日期区间，用于预约日期选择器 */
export function reserveDateRange(days = 7): [Date, Date] {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  end.setDate(end.getDate() + days)
  end.setHours(23, 59, 59, 999)
  return [start, end]
}

/** 今天，形如 "2026-01-01 星期三" */
export function todayText(): string {
  const now = new Date()
  return `${formatDateOnly(now)} ${WEEKDAYS[now.getDay()] ?? ''}`
}

/** 日期标签："今天 10-28" / "明天 10-29" / "10-30 星期五" */
export function dayLabel(day: string | null | undefined): string {
  if (!day) return '—'
  const date = new Date(`${day.slice(0, 10)}T00:00:00`)
  if (Number.isNaN(date.getTime())) return day
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((date.getTime() - today.getTime()) / 86400000)
  const monthDay = `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  if (diff === 0) return `今天 ${monthDay}`
  if (diff === 1) return `明天 ${monthDay}`
  if (diff === 2) return `后天 ${monthDay}`
  return `${monthDay} ${WEEKDAYS[date.getDay()] ?? ''}`
}

/** 时段标签："10:00 - 11:00" */
export function slotLabel(start: string, end: string): string {
  return `${formatTime(start)} - ${formatTime(end)}`
}

/** 相差小时数（用于校验自习室单次最长 4 小时） */
export function hoursBetween(start: string, end: string): number {
  const startDate = new Date(start)
  const endDate = new Date(end)
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) return 0
  return (endDate.getTime() - startDate.getTime()) / 3600000
}
