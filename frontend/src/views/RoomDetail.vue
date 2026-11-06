<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Check,
  Clock,
  Delete,
  Edit,
  Lightning,
  Plus,
  Refresh,
  School,
  User
} from '@element-plus/icons-vue'
import {
  addSeatsApi,
  deleteSeatApi,
  getRoomDetailApi,
  getRoomSeatsApi,
  updateSeatApi
} from '@/api/room'
import { createLibraryReservationApi } from '@/api/reservation'
import type { RoomInfo, SeatInfo } from '@/schemas/room'
import { useUser } from '@/utils/user'
import { equipmentStatusMeta, labStatusMeta } from '@/utils/dict'
import {
  clockText,
  formatDateOnly,
  formatOpenTime,
  formatTime,
  hoursBetween,
  joinDateTime,
  seatCountText
} from '@/utils/format'
import RoomFormDialog from '@/components/RoomFormDialog.vue'

/**
 * 自习室详情 + 选座预约
 *
 * 座位图来自 `/api/rooms/{id}/seats?start_time&end_time`：
 * 后端用 NOT EXISTS 在数据库里算出「这个时段这个座位是否被预约成功占用」，
 * 前端直接按 available 上色即可，不需要再自己算冲突。
 *
 * 预约限制（后端同样会校验，这里提前提示）：同一天、最长 4 小时、最多提前 3 天。
 */
const route = useRoute()
const router = useRouter()
const { isAdmin, displayName } = useUser()

const roomId = computed(() => Number(route.params.id))

const room = ref<RoomInfo | null>(null)
const seats = ref<SeatInfo[]>([])
const day = ref(formatDateOnly(new Date()))
const start = ref('')
const end = ref('')
const selectedSeatId = ref<number | null>(null)

const loading = ref(false)
const seatLoading = ref(false)
const submitting = ref(false)

const roomDialogVisible = ref(false)

// 批量加座位
const batchVisible = ref(false)
const batchSaving = ref(false)
const batchForm = reactive({
  prefix: '',
  startNo: 1,
  count: 4,
  area: '中间区',
  has_power: true
})

// 编辑单个座位
const seatDialogVisible = ref(false)
const seatSaving = ref(false)
const editingSeat = ref<SeatInfo | null>(null)
const seatForm = reactive({
  code: '',
  area: '',
  has_power: false,
  status: 1
})

const isClosed = computed(() => room.value?.status === 0)
const loadError = ref(false)
const availableCount = computed(() => seats.value.filter((seat) => seat.available).length)
const selectedSeat = computed(
  () => seats.value.find((seat) => seat.id === selectedSeatId.value) ?? null
)
const duration = computed(() =>
  start.value && end.value
    ? hoursBetween(`${day.value}T${start.value}:00`, `${day.value}T${end.value}:00`)
    : 0
)

/** 批量新增座位的编号预览 */
const batchCodes = computed(() => {
  const prefix = batchForm.prefix || `SEAT-${String(roomId.value).padStart(2, '0')}-`
  return Array.from({ length: batchForm.count }, (_, index) =>
    `${prefix}${String(batchForm.startNo + index).padStart(3, '0')}`
  )
})

function toMinutes(clock: string): number {
  return Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5))
}

function toClock(minutes: number): string {
  const hour = Math.floor(minutes / 60)
  const minute = minutes % 60
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

/** 默认时段：从开放时间（或下一个整点）开始，给 2 小时；当天已过开放时间就顺延到明天 */
function computeDefaultRange(): { start: string; end: string } {
  const open = clockText(room.value?.open_time) || '08:00'
  const close = clockText(room.value?.close_time) || '22:00'
  const openMin = toMinutes(open)
  const closeMin = toMinutes(close)

  let startMin = openMin
  if (day.value === formatDateOnly(new Date())) {
    const now = new Date()
    startMin = Math.max(openMin, now.getHours() * 60 + 60)
  }

  if (startMin >= closeMin) {
    const tomorrow = new Date()
    tomorrow.setDate(tomorrow.getDate() + 1)
    day.value = formatDateOnly(tomorrow)
    startMin = openMin
  }

  const endMin = Math.min(startMin + 120, closeMin)
  return {
    start: toClock(startMin),
    end: toClock(Math.max(endMin, startMin + 60))
  }
}

function disabledDate(date: Date): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const max = new Date(today)
  max.setDate(max.getDate() + 3)
  return date.getTime() < today.getTime() || date.getTime() > max.getTime()
}

async function loadRoom(): Promise<void> {
  try {
    const res = await getRoomDetailApi(roomId.value)
    room.value = res.data
    loadError.value = false
  } catch {
    // 自习室不存在（404）或被删除：页面显示「找不到」，不再渲染预约表单
    loadError.value = true
    throw new Error('room-not-found')
  }
}

async function loadSeats(): Promise<void> {
  seatLoading.value = true
  try {
    const hasRange = !!start.value && !!end.value && end.value > start.value
    const res = await getRoomSeatsApi(
      roomId.value,
      hasRange
        ? {
            start_time: joinDateTime(day.value, `${start.value}:00`),
            end_time: joinDateTime(day.value, `${end.value}:00`)
          }
        : {}
    )
    seats.value = res.data?.seats ?? []
    // 之前选中的座位在新时段可能已被占，清掉选择避免提交一个必然冲突的预约
    if (
      selectedSeatId.value !== null &&
      !seats.value.some((seat) => seat.id === selectedSeatId.value && seat.available)
    ) {
      selectedSeatId.value = null
    }
  } catch {
    seats.value = []
  } finally {
    seatLoading.value = false
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    await loadRoom()
    if (!start.value || !end.value) {
      const range = computeDefaultRange()
      start.value = range.start
      end.value = range.end
    }
    batchForm.prefix = `SEAT-${String(roomId.value).padStart(2, '0')}-`
    await loadSeats()
  } catch {
    // loadError 已经置位（404 不渲染下面的表单）
  } finally {
    loading.value = false
  }
}

function onFilterChange(): void {
  selectedSeatId.value = null
  void loadSeats()
}

function onDayChange(): void {
  const range = computeDefaultRange()
  start.value = range.start
  end.value = range.end
  onFilterChange()
}

function pickSeat(seat: SeatInfo): void {
  if (!seat.available) return
  selectedSeatId.value = selectedSeatId.value === seat.id ? null : seat.id
}

async function submitReservation(): Promise<void> {
  if (!room.value || !selectedSeat.value) {
    ElMessage.warning('请先在座位图上选一个空闲座位')
    return
  }
  if (!start.value || !end.value) {
    ElMessage.warning('请选择预约的开始与结束时间')
    return
  }
  if (end.value <= start.value) {
    ElMessage.warning('结束时间必须晚于开始时间')
    return
  }
  if (duration.value > 4) {
    ElMessage.warning('自习室单次预约最长 4 小时')
    return
  }

  submitting.value = true
  try {
    const res = await createLibraryReservationApi({
      seat_id: selectedSeat.value.id,
      reserve_start_time: joinDateTime(day.value, `${start.value}:00`),
      reserve_end_time: joinDateTime(day.value, `${end.value}:00`)
    })
    if (res.code === 200) {
      ElMessage.success(res.message || '预约申请已提交，请等待管理员审核')
      selectedSeatId.value = null
      await loadSeats()
    }
  } catch {
    // 冲突 / 超出开放时间 / 一人一座等提示已由响应拦截器弹出
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------- 管理员：座位维护
function openBatchDialog(): void {
  batchForm.prefix = `SEAT-${String(roomId.value).padStart(2, '0')}-`
  batchForm.startNo = seats.value.length + 1
  batchForm.count = 4
  batchForm.area = '中间区'
  batchForm.has_power = true
  batchVisible.value = true
}

async function saveBatchSeats(): Promise<void> {
  if (batchForm.count < 1) {
    ElMessage.warning('数量至少为 1')
    return
  }

  batchSaving.value = true
  try {
    const list = batchCodes.value.map((code) => ({
      code,
      area: batchForm.area || null,
      has_power: batchForm.has_power,
      description: `${room.value?.name ?? ''}${batchForm.area}座位`
    }))
    const res = await addSeatsApi(roomId.value, list)
    if (res.code === 200) {
      ElMessage.success(`已新增 ${res.data?.length ?? 0} 个座位`)
      batchVisible.value = false
      await loadSeats()
    }
  } catch {
    // 编号重复 / 超出容量等提示已由响应拦截器弹出
  } finally {
    batchSaving.value = false
  }
}

function openSeatDialog(seat: SeatInfo): void {
  editingSeat.value = seat
  seatForm.code = seat.code
  seatForm.area = seat.area ?? ''
  seatForm.has_power = seat.has_power
  seatForm.status = seat.status
  seatDialogVisible.value = true
}

async function saveSeat(): Promise<void> {
  if (!editingSeat.value) return
  if (!seatForm.code.trim()) {
    ElMessage.warning('座位编号不能为空')
    return
  }

  seatSaving.value = true
  try {
    const res = await updateSeatApi(editingSeat.value.id, {
      code: seatForm.code.trim(),
      area: seatForm.area.trim() || null,
      has_power: seatForm.has_power,
      status: seatForm.status
    })
    if (res.code === 200) {
      ElMessage.success('座位已更新')
      seatDialogVisible.value = false
      await loadSeats()
    }
  } catch {
    // 编号重复等提示已由响应拦截器统一弹出
  } finally {
    seatSaving.value = false
  }
}

async function removeSeat(seat: SeatInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除座位「${seat.code}」吗？`, '删除座位', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }

  try {
    const res = await deleteSeatApi(seat.id)
    if (res.code === 200) {
      ElMessage.success('座位已删除')
      await loadSeats()
    }
  } catch {
    // 提示已由响应拦截器统一弹出
  }
}

function onRoomSaved(): void {
  void loadRoom()
}

onMounted(loadAll)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="detail-head">
      <el-button link :icon="ArrowLeft" @click="router.back()">返回</el-button>
      <div class="detail-head__main">
        <h2 class="detail-head__title">{{ room?.name || '自习室详情' }}</h2>
        <el-tag v-if="room" size="small" :type="labStatusMeta(room.status).type" effect="light">
          {{ labStatusMeta(room.status).label }}
        </el-tag>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
        <el-button v-if="isAdmin" :icon="Plus" @click="openBatchDialog">批量加座位</el-button>
        <el-button v-if="isAdmin" type="primary" :icon="Edit" @click="roomDialogVisible = true">
          编辑自习室
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="isClosed && !loadError"
      class="detail-alert"
      type="warning"
      :closable="false"
      show-icon
      title="自习室当前已关闭"
      description="管理员暂时关闭了这间自习室，暂时不能预约座位。"
    />

    <section v-if="loadError" class="panel not-found">
      <el-empty description="自习室不存在，可能已经被删除了">
        <el-button type="primary" @click="router.push('/manager/room')">
          返回自习室列表
        </el-button>
      </el-empty>
    </section>

    <div v-else class="detail-grid">
      <div class="detail-main">
        <!-- 基本信息和时段筛选 -->
        <section class="panel">
          <div class="panel__body">
            <div class="room-info">
              <div class="info-item">
                <div class="info-item__label">位置</div>
                <div class="info-item__value">{{ room?.location || '未填写' }}</div>
              </div>
              <div class="info-item">
                <div class="info-item__label">座位数上限</div>
                <div class="info-item__value">{{ seatCountText(room?.capacity) }}</div>
              </div>
              <div class="info-item">
                <div class="info-item__label">开放时间</div>
                <div class="info-item__value">
                  {{ formatOpenTime(room?.open_time, room?.close_time) }}
                </div>
              </div>
              <div class="info-item">
                <div class="info-item__label">当前时段空闲</div>
                <div class="info-item__value">
                  {{ availableCount }} / {{ seats.length }} 个座位
                </div>
              </div>
            </div>

            <p class="room-desc">{{ room?.description || '这间自习室还没有填写简介。' }}</p>

            <div class="filters">
              <div class="filter">
                <span class="filter__label">日期</span>
                <el-date-picker
                  v-model="day"
                  type="date"
                  value-format="YYYY-MM-DD"
                  :clearable="false"
                  :disabled-date="disabledDate"
                  size="default"
                  @change="onDayChange"
                />
              </div>
              <div class="filter">
                <span class="filter__label">开始</span>
                <el-time-select
                  v-model="start"
                  start="06:00"
                  end="23:30"
                  step="00:30"
                  :clearable="false"
                  @change="onFilterChange"
                />
              </div>
              <div class="filter">
                <span class="filter__label">结束</span>
                <el-time-select
                  v-model="end"
                  start="06:00"
                  end="23:30"
                  step="00:30"
                  :clearable="false"
                  @change="onFilterChange"
                />
              </div>
              <span class="muted filter__hint">最长 4 小时 · 最多提前 3 天</span>
            </div>
          </div>
        </section>

        <!-- 座位图 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><School /></el-icon>
              座位图
              <span class="muted">
                （{{ day }} {{ formatTime(start) }} - {{ end }}，空闲 {{ availableCount }} 个）
              </span>
            </div>
            <div class="seat-legend">
              <span class="legend"><i class="legend__dot is-free" /> 空闲</span>
              <span class="legend"><i class="legend__dot is-busy" /> 已占</span>
              <span class="legend"><i class="legend__dot is-picked" /> 已选</span>
            </div>
          </div>

          <div v-loading="seatLoading" class="panel__body">
            <div v-if="seats.length" class="seat-grid">
              <button
                v-for="seat in seats"
                :key="seat.id"
                type="button"
                class="seat"
                :class="{
                  'is-free': seat.available,
                  'is-busy': !seat.available,
                  'is-picked': seat.id === selectedSeatId
                }"
                :disabled="!seat.available"
                :title="`${seat.code}${seat.area ? ` · ${seat.area}` : ''}${seat.has_power ? ' · 有电源' : ''}`"
                @click="pickSeat(seat)"
              >
                <span class="seat__code">{{ seat.code }}</span>
                <span class="seat__meta">
                  {{ seat.area || '普通区' }}
                  <el-icon v-if="seat.has_power" class="seat__power"><Lightning /></el-icon>
                </span>
              </button>
            </div>
            <el-empty
              v-else-if="!seatLoading"
              :description="
                isAdmin
                  ? '这间自习室还没有座位，用右上角「批量加座位」添加'
                  : '这间自习室还没有登记座位，暂时不能预约'
              "
              :image-size="80"
            />
          </div>
        </section>

        <!-- 管理员：座位维护 -->
        <section v-if="isAdmin" class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Edit /></el-icon>
              座位维护
              <span class="muted">（{{ seats.length }} 个）</span>
            </div>
            <el-button size="small" type="primary" plain :icon="Plus" @click="openBatchDialog">
              批量加座位
            </el-button>
          </div>
          <div class="panel__table">
            <el-table v-if="seats.length" :data="seats" size="small" stripe max-height="320">
              <el-table-column prop="code" label="编号" width="140" />
              <el-table-column label="区域" width="110">
                <template #default="{ row }">{{ row.area || '—' }}</template>
              </el-table-column>
              <el-table-column label="电源" width="80" align="center">
                <template #default="{ row }">
                  <el-icon v-if="row.has_power" class="has-power"><Lightning /></el-icon>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="equipmentStatusMeta(row.status).type">
                    {{ equipmentStatusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="130" align="center">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openSeatDialog(row)">
                    编辑
                  </el-button>
                  <el-button link type="danger" size="small" @click="removeSeat(row)">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </section>
      </div>

      <!-- 预约面板 -->
      <aside class="detail-side">
        <section class="panel booking">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Check /></el-icon>
              确认预约
            </div>
          </div>
          <div class="panel__body">
            <div class="booking-tip">
              选好座位后点「提交预约」。提交后为「待审核」，管理员通过后才算占座成功。
            </div>

            <div class="booking-block">
              <div class="booking-block__label">已选座位</div>
              <div v-if="selectedSeat" class="selected-seat">
                <span class="selected-seat__code">{{ selectedSeat.code }}</span>
                <span class="selected-seat__meta">
                  {{ selectedSeat.area || '普通区' }}
                  {{ selectedSeat.has_power ? ' · 有电源' : '' }}
                </span>
              </div>
              <div v-else class="selected-seat is-empty">还没有选座位，点左侧座位图</div>
            </div>

            <div class="booking-block">
              <div class="booking-block__label">预约时段</div>
              <div class="booking-summary">
                <div class="booking-summary__row">
                  <span class="muted">日期</span>
                  <span>{{ day }}</span>
                </div>
                <div class="booking-summary__row">
                  <span class="muted">时间</span>
                  <span>{{ formatTime(start) }} ~ {{ end }}</span>
                </div>
                <div class="booking-summary__row">
                  <span class="muted">时长</span>
                  <span :class="{ 'is-over': duration > 4 }">{{ duration }} 小时</span>
                </div>
              </div>
            </div>

            <div class="booking-block">
              <div class="booking-block__label">申请人</div>
              <div class="booking-applicant">
                <el-icon><User /></el-icon>
                {{ displayName }}（提交时由登录态确定，不能代别人预约）
              </div>
            </div>

            <el-button
              class="booking-submit"
              type="primary"
              size="large"
              :icon="Check"
              :loading="submitting"
              :disabled="isClosed || !selectedSeat"
              @click="submitReservation"
            >
              提交预约
            </el-button>

            <div class="booking-help">
              <div><el-icon><Clock /></el-icon> 必须同一天，单次最长 4 小时</div>
              <div><el-icon><School /></el-icon> 一人同一时段只能占一个座位</div>
            </div>
          </div>
        </section>
      </aside>
    </div>

    <!-- 管理员：编辑自习室 -->
    <RoomFormDialog v-model="roomDialogVisible" :room="room" @saved="onRoomSaved" />

    <!-- 管理员：批量加座位 -->
    <el-dialog v-model="batchVisible" title="批量添加座位" width="520px" append-to-body>
      <el-form label-width="96px">
        <el-form-item label="编号前缀">
          <el-input v-model="batchForm.prefix" maxlength="40" />
        </el-form-item>
        <el-form-item label="起始序号">
          <el-input-number v-model="batchForm.startNo" :min="1" :max="999" controls-position="right" />
        </el-form-item>
        <el-form-item label="数量">
          <el-input-number v-model="batchForm.count" :min="1" :max="50" controls-position="right" />
        </el-form-item>
        <el-form-item label="区域">
          <el-input v-model="batchForm.area" maxlength="50" placeholder="靠窗 / 静音区 / 中间区" />
        </el-form-item>
        <el-form-item label="电源插座">
          <el-switch v-model="batchForm.has_power" active-text="有" inactive-text="无" inline-prompt />
        </el-form-item>
        <el-form-item label="编号预览">
          <div class="code-preview">
            <el-tag v-for="code in batchCodes" :key="code" size="small" type="info">{{ code }}</el-tag>
          </div>
        </el-form-item>
      </el-form>
      <div class="dialog-hint">
        座位总数不能超过自习室容量（{{ seatCountText(room?.capacity) }}），编号全库唯一。
      </div>
      <template #footer>
        <el-button @click="batchVisible = false">取消</el-button>
        <el-button type="primary" :loading="batchSaving" @click="saveBatchSeats">
          添加 {{ batchForm.count }} 个
        </el-button>
      </template>
    </el-dialog>

    <!-- 管理员：编辑座位 -->
    <el-dialog v-model="seatDialogVisible" title="编辑座位" width="460px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="编号" required>
          <el-input v-model="seatForm.code" maxlength="50" />
        </el-form-item>
        <el-form-item label="区域">
          <el-input v-model="seatForm.area" maxlength="50" />
        </el-form-item>
        <el-form-item label="电源">
          <el-switch v-model="seatForm.has_power" active-text="有" inactive-text="无" inline-prompt />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="seatForm.status"
            :active-value="1"
            :inactive-value="0"
            active-text="可用"
            inactive-text="停用"
            inline-prompt
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="seatDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="seatSaving" @click="saveSeat">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.detail-head__main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.detail-head__title {
  overflow: hidden;
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.detail-alert {
  margin-bottom: 16px;
}

.not-found {
  padding: 40px 16px;
}

.detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  align-items: start;
}

.detail-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.detail-main .panel + .panel {
  margin-top: 0;
}

.room-info {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
}

.info-item__label {
  font-size: 12px;
  color: #909399;
}

.info-item__value {
  margin-top: 4px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.room-desc {
  margin-top: 16px;
  padding-top: 14px;
  font-size: 13px;
  line-height: 1.8;
  color: #606266;
  border-top: 1px dashed #ebeef5;
}

.filters {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed #ebeef5;
}

.filter {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.filter :deep(.el-date-editor) {
  width: 150px;
}

.filter :deep(.el-select) {
  width: 128px;
}

.filter__label {
  flex: none;
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}

.filter__hint {
  font-size: 12px;
}

.seat-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: #909399;
}

.legend {
  display: flex;
  align-items: center;
  gap: 4px;
}

.legend__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.legend__dot.is-free {
  background-color: var(--el-color-success);
}

.legend__dot.is-busy {
  background-color: #d4d7de;
}

.legend__dot.is-picked {
  background-color: var(--el-color-primary);
}

.seat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 8px;
}

.seat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 9px 6px;
  font-size: 12px;
  background-color: #f7f9fc;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s;
}

.seat__code {
  font-weight: 600;
  color: #303133;
}

.seat__meta {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
  color: #909399;
}

.seat__power {
  color: var(--el-color-warning);
}

.seat.is-free {
  border-color: var(--el-color-success-light-5);
  background-color: var(--el-color-success-light-9);
}

.seat.is-free:hover {
  border-color: var(--el-color-success);
}

.seat.is-busy {
  cursor: not-allowed;
  opacity: 0.6;
}

.seat.is-picked {
  color: #fff;
  border-color: var(--el-color-primary);
  background-color: var(--el-color-primary);
}

.seat.is-picked .seat__code,
.seat.is-picked .seat__meta {
  color: #fff;
}

.panel__table {
  padding: 8px 8px 12px;
}

.has-power {
  color: var(--el-color-warning);
}

.detail-side {
  position: sticky;
  top: 76px;
}

.booking-tip {
  padding: 10px;
  font-size: 12px;
  line-height: 1.7;
  color: #b26a00;
  background-color: var(--el-color-warning-light-9);
  border-radius: 6px;
}

.booking-block {
  margin-top: 16px;
}

.booking-block__label {
  margin-bottom: 8px;
  font-size: 12px;
  color: #909399;
}

.selected-seat {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 12px;
  background-color: var(--el-color-success-light-9);
  border: 1px solid var(--el-color-success-light-5);
  border-radius: 6px;
}

.selected-seat.is-empty {
  color: #909399;
  background-color: #f7f9fc;
  border-color: #ebeef5;
}

.selected-seat__code {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.selected-seat__meta {
  font-size: 12px;
  color: #606266;
}

.booking-summary {
  padding: 10px 12px;
  font-size: 12px;
  background-color: #f7f9fc;
  border-radius: 6px;
}

.booking-summary__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 3px 0;
  color: #303133;
}

.booking-summary__row .is-over {
  font-weight: 600;
  color: var(--el-color-danger);
}

.booking-applicant {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  line-height: 1.7;
  color: #606266;
}

.booking-submit {
  width: 100%;
  margin-top: 18px;
}

.booking-help {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 14px;
  font-size: 12px;
  color: #909399;
}

.booking-help div {
  display: flex;
  align-items: center;
  gap: 5px;
}

.code-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.dialog-hint {
  font-size: 12px;
  line-height: 1.7;
  color: #909399;
}

@media (max-width: 1100px) {
  .detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .detail-side {
    position: static;
  }
}
</style>
