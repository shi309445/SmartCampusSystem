<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Calendar,
  Check,
  Clock,
  Cpu,
  Delete,
  Edit,
  Location,
  OfficeBuilding,
  Plus,
  Refresh,
  User
} from '@element-plus/icons-vue'
import {
  addEquipmentsApi,
  deleteEquipmentApi,
  getLabDetailApi,
  getLabEquipmentsApi,
  getLabScheduleApi,
  updateEquipmentApi
} from '@/api/lab'
import { createLabReservationApi } from '@/api/reservation'
import type { EquipmentInfo, LabInfo, TimeSlot } from '@/schemas/lab'
import type { LabReservation } from '@/schemas/reservation'
import { useUser } from '@/utils/user'
import { equipmentStatusMeta, labStatusMeta, reservationStatusMeta } from '@/utils/dict'
import {
  capacityText,
  formatDateOnly,
  formatOpenTime,
  formatReserveRange,
  joinDateTime,
  timeOfDay
} from '@/utils/format'
import LabFormDialog from '@/components/LabFormDialog.vue'

/**
 * 实验室详情 + 预约
 *
 * 页面分两块：
 *   左：实验室信息、当天排期与可预约时段、设备清单（管理员可维护设备）
 *   右：预约表单（日期 + 起止时间 + 用途 + 人数）
 *
 * 时段接口 `/api/labs/{id}/schedule` 已经按整点切好并标了 available，
 * 点时段 = 快速回填起止时间；用户也可以自己用时间下拉微调。
 * 提交后是「待审核」，页面会提示等待管理员审核，而不是直接说预约成功。
 */
const route = useRoute()
const router = useRouter()
const { isAdmin, displayName } = useUser()

const labId = computed(() => Number(route.params.id))

const lab = ref<LabInfo | null>(null)
const schedule = ref<LabReservation[]>([])
const slots = ref<TimeSlot[]>([])
const equipments = ref<EquipmentInfo[]>([])
const day = ref(formatDateOnly(new Date()))
const loadError = ref(false)

const loading = ref(false)
const scheduleLoading = ref(false)
const equipmentLoading = ref(false)
const submitting = ref(false)

const form = reactive({
  start: '',
  end: '',
  purpose: '',
  people_num: 1
})

const labDialogVisible = ref(false)

// 设备弹窗（管理员）
const equipmentVisible = ref(false)
const equipmentSaving = ref(false)
const editingEquipment = ref<EquipmentInfo | null>(null)
const equipmentForm = reactive({
  name: '',
  code: '',
  model: '',
  description: '',
  status: 1
})

const isClosed = computed(() => lab.value?.status === 0)
const freeSlots = computed(() => slots.value.filter((slot) => slot.available))
const freeCount = computed(() => freeSlots.value.length)

/** 选中的时段是否是当前这一格（点过之后高亮） */
function isPicked(slot: TimeSlot): boolean {
  return timeOfDay(slot.start_time) === form.start && timeOfDay(slot.end_time) === form.end
}

async function loadLab(): Promise<void> {
  try {
    const res = await getLabDetailApi(labId.value)
    lab.value = res.data
    loadError.value = false
  } catch {
    loadError.value = true
    throw new Error('lab-not-found')
  }
  if (!form.people_num) form.people_num = 1
}

async function loadSchedule(): Promise<void> {
  scheduleLoading.value = true
  try {
    const res = await getLabScheduleApi(labId.value, day.value)
    schedule.value = res.data?.schedule ?? []
    slots.value = res.data?.slots ?? []
  } catch {
    schedule.value = []
    slots.value = []
  } finally {
    scheduleLoading.value = false
  }
}

async function loadEquipments(): Promise<void> {
  equipmentLoading.value = true
  try {
    const res = await getLabEquipmentsApi(labId.value)
    equipments.value = res.data ?? []
  } catch {
    equipments.value = []
  } finally {
    equipmentLoading.value = false
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    await loadLab()
    await Promise.allSettled([loadSchedule(), loadEquipments()])
  } catch {
    // loadError 已置位：页面显示「实验室不存在」
  } finally {
    loading.value = false
  }
}

function onDayChange(): void {
  form.start = ''
  form.end = ''
  void loadSchedule()
}

/** 点时段：直接回填起止时间（后端给的时段本身就是整点，正好是合法预约区间） */
function pickSlot(slot: TimeSlot): void {
  if (!slot.available) return
  form.start = timeOfDay(slot.start_time)
  form.end = timeOfDay(slot.end_time)
}

function disabledDate(date: Date): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const max = new Date(today)
  max.setDate(max.getDate() + 7)
  return date.getTime() < today.getTime() || date.getTime() > max.getTime()
}

async function submitReservation(): Promise<void> {
  if (!lab.value) return
  if (isClosed.value) {
    ElMessage.warning('实验室当前已关闭，暂时不能预约')
    return
  }
  if (!form.start || !form.end) {
    ElMessage.warning('请选择预约的开始与结束时间')
    return
  }
  if (form.end <= form.start) {
    ElMessage.warning('结束时间必须晚于开始时间')
    return
  }
  if (form.people_num > lab.value.maxpeople) {
    ElMessage.warning(`使用人数不能超过实验室容量（${lab.value.maxpeople} 人）`)
    return
  }

  submitting.value = true
  try {
    const res = await createLabReservationApi({
      lab_id: lab.value.id,
      reserve_start_time: joinDateTime(day.value, `${form.start}:00`),
      reserve_end_time: joinDateTime(day.value, `${form.end}:00`),
      purpose: form.purpose.trim() || null,
      people_num: form.people_num || null
    })
    if (res.code === 200) {
      ElMessage.success(res.message || '预约申请已提交，请等待管理员审核')
      form.start = ''
      form.end = ''
      form.purpose = ''
      await loadSchedule()
    }
  } catch {
    // 冲突 / 超出开放时间等提示已由响应拦截器弹出（后端 message 很具体）
  } finally {
    submitting.value = false
  }
}

function openEquipmentDialog(equipment?: EquipmentInfo): void {
  editingEquipment.value = equipment ?? null
  equipmentForm.name = equipment?.name ?? ''
  equipmentForm.code = equipment?.code ?? ''
  equipmentForm.model = equipment?.model ?? ''
  equipmentForm.description = equipment?.description ?? ''
  equipmentForm.status = equipment?.status ?? 1
  equipmentVisible.value = true
}

async function saveEquipment(): Promise<void> {
  if (!equipmentForm.name.trim() || !equipmentForm.code.trim()) {
    ElMessage.warning('设备名称与编号不能为空')
    return
  }

  equipmentSaving.value = true
  try {
    const payload = {
      name: equipmentForm.name.trim(),
      code: equipmentForm.code.trim(),
      model: equipmentForm.model.trim() || null,
      description: equipmentForm.description.trim() || null,
      status: equipmentForm.status
    }
    const res = editingEquipment.value
      ? await updateEquipmentApi(editingEquipment.value.id, payload)
      : await addEquipmentsApi(labId.value, [payload])
    if (res.code === 200) {
      ElMessage.success(editingEquipment.value ? '设备已更新' : '设备已添加')
      equipmentVisible.value = false
      await loadEquipments()
    }
  } catch {
    // 编号重复等提示已由响应拦截器统一弹出
  } finally {
    equipmentSaving.value = false
  }
}

async function removeEquipment(equipment: EquipmentInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除设备「${equipment.name}」吗？`, '删除设备', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }

  try {
    const res = await deleteEquipmentApi(equipment.id)
    if (res.code === 200) {
      ElMessage.success('设备已删除')
      await loadEquipments()
    }
  } catch {
    // 提示已由响应拦截器统一弹出
  }
}

function onLabSaved(): void {
  void loadLab()
}

onMounted(loadAll)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="detail-head">
      <el-button link :icon="ArrowLeft" @click="router.back()">返回</el-button>
      <div class="detail-head__main">
        <h2 class="detail-head__title">{{ lab?.name || '实验室详情' }}</h2>
        <el-tag v-if="lab" size="small" :type="labStatusMeta(lab.status).type" effect="light">
          {{ labStatusMeta(lab.status).label }}
        </el-tag>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
        <el-button v-if="isAdmin" type="primary" :icon="Edit" @click="labDialogVisible = true">
          编辑实验室
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="isClosed && !loadError"
      class="detail-alert"
      type="warning"
      :closable="false"
      show-icon
      title="实验室当前已关闭"
      description="管理员暂时关闭了这间实验室，你可以看看其它实验室，或直接问智能助手。"
    />

    <section v-if="loadError" class="panel not-found">
      <el-empty description="实验室不存在，可能已经被删除了">
        <el-button type="primary" @click="router.push('/manager/lab')">
          返回实验室列表
        </el-button>
      </el-empty>
    </section>

    <div v-else class="detail-grid">
      <div class="detail-main">
        <!-- 基本信息 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><OfficeBuilding /></el-icon>
              基本信息
            </div>
          </div>
          <div class="panel__body">
            <div class="info-grid">
              <div class="info-item">
                <div class="info-item__label"><el-icon><Location /></el-icon> 位置</div>
                <div class="info-item__value">{{ lab?.location || '未填写' }}</div>
              </div>
              <div class="info-item">
                <div class="info-item__label"><el-icon><User /></el-icon> 可容纳</div>
                <div class="info-item__value">{{ capacityText(lab?.maxpeople) }}</div>
              </div>
              <div class="info-item">
                <div class="info-item__label"><el-icon><Clock /></el-icon> 开放时间</div>
                <div class="info-item__value">
                  {{ formatOpenTime(lab?.open_time, lab?.close_time) }}
                </div>
              </div>
              <div class="info-item">
                <div class="info-item__label"><el-icon><Cpu /></el-icon> 设备数量</div>
                <div class="info-item__value">{{ equipments.length }} 台</div>
              </div>
            </div>
            <p class="detail-desc">{{ lab?.description || '这间实验室还没有填写简介。' }}</p>
          </div>
        </section>

        <!-- 当天排期与可预约时段 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Calendar /></el-icon>
              当天排期与可预约时段
              <span class="muted">（{{ freeCount }} 个空闲）</span>
            </div>
            <el-date-picker
              v-model="day"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择日期"
              :clearable="false"
              :disabled-date="disabledDate"
              size="small"
              class="detail-date"
              @change="onDayChange"
            />
          </div>

          <div v-loading="scheduleLoading" class="panel__body">
            <div class="slot-legend">
              <span class="legend"><i class="legend__dot is-free" /> 可预约</span>
              <span class="legend"><i class="legend__dot is-busy" /> 已占用 / 已过期</span>
              <span class="muted">点一个空闲时段即可回填右侧时间</span>
            </div>

            <div v-if="slots.length" class="slot-grid">
              <button
                v-for="slot in slots"
                :key="slot.start_time"
                type="button"
                class="slot"
                :class="{
                  'is-free': slot.available,
                  'is-busy': !slot.available,
                  'is-picked': isPicked(slot)
                }"
                :disabled="!slot.available"
                @click="pickSlot(slot)"
              >
                <span class="slot__time">{{ timeOfDay(slot.start_time) }} - {{ timeOfDay(slot.end_time) }}</span>
                <span class="slot__state">
                  {{ slot.available ? '空闲' : '已占' }}
                </span>
              </button>
            </div>
            <el-empty
              v-else-if="!scheduleLoading"
              :description="isClosed ? '实验室已关闭，没有可预约时段' : '当天没有可预约时段（可能已过开放时间）'"
              :image-size="80"
            />

            <div class="schedule-title">当天已有预约（含待审核）</div>
            <el-table v-if="schedule.length" :data="schedule" size="small" stripe>
              <el-table-column label="预约时段" min-width="220">
                <template #default="{ row }">
                  {{ formatReserveRange(row.reserve_start_time, row.reserve_end_time) }}
                </template>
              </el-table-column>
              <el-table-column prop="user_name" label="申请人" width="100" />
              <el-table-column label="人数" width="80" align="center">
                <template #default="{ row }">{{ row.people_num ?? '—' }}</template>
              </el-table-column>
              <el-table-column prop="purpose" label="用途" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">{{ row.purpose || '—' }}</template>
              </el-table-column>
              <el-table-column label="状态" width="100" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="reservationStatusMeta(row.status).type">
                    {{ reservationStatusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="当天还没有预约记录" :image-size="70" />
          </div>
        </section>

        <!-- 设备清单 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Cpu /></el-icon>
              设备清单
              <span class="muted">（{{ equipments.length }} 台）</span>
            </div>
            <el-button
              v-if="isAdmin"
              type="primary"
              plain
              size="small"
              :icon="Plus"
              @click="openEquipmentDialog()"
            >
              添加设备
            </el-button>
          </div>
          <div v-loading="equipmentLoading" class="panel__table">
            <el-table v-if="equipments.length" :data="equipments" size="small" stripe>
              <el-table-column prop="name" label="设备名称" min-width="160" show-overflow-tooltip />
              <el-table-column prop="code" label="编号" width="130" />
              <el-table-column prop="model" label="型号" width="120">
                <template #default="{ row }">{{ row.model || '—' }}</template>
              </el-table-column>
              <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ row.description || '—' }}</template>
              </el-table-column>
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="equipmentStatusMeta(row.status).type">
                    {{ equipmentStatusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column v-if="isAdmin" label="操作" width="130" align="center">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openEquipmentDialog(row)">
                    编辑
                  </el-button>
                  <el-button link type="danger" size="small" @click="removeEquipment(row)">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else-if="!equipmentLoading" description="还没有登记设备" :image-size="70" />
          </div>
        </section>
      </div>

      <!-- 预约表单 -->
      <aside class="detail-side">
        <section class="panel booking">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Calendar /></el-icon>
              预约这间实验室
            </div>
          </div>
          <div class="panel__body">
            <div class="booking-tip">
              提交后状态为「待审核」，管理员通过后才算约上；同一时段只能有一条成功的预约。
            </div>

            <el-form label-position="top" class="booking-form">
              <el-form-item label="预约日期">
                <el-date-picker
                  v-model="day"
                  type="date"
                  value-format="YYYY-MM-DD"
                  :clearable="false"
                  :disabled-date="disabledDate"
                  class="booking-full"
                  @change="onDayChange"
                />
              </el-form-item>

              <div class="booking-row">
                <el-form-item label="开始时间">
                  <el-time-select
                    v-model="form.start"
                    start="06:00"
                    end="23:30"
                    step="00:30"
                    :clearable="false"
                    placeholder="开始"
                    class="booking-full"
                  />
                </el-form-item>
                <el-form-item label="结束时间">
                  <el-time-select
                    v-model="form.end"
                    start="06:00"
                    end="23:30"
                    step="00:30"
                    :clearable="false"
                    placeholder="结束"
                    class="booking-full"
                  />
                </el-form-item>
              </div>

              <el-form-item label="使用人数">
                <el-input-number
                  v-model="form.people_num"
                  :min="1"
                  :max="lab?.maxpeople ?? 1"
                  controls-position="right"
                  class="booking-full"
                />
              </el-form-item>

              <el-form-item label="用途 / 实验内容">
                <el-input
                  v-model="form.purpose"
                  type="textarea"
                  :rows="3"
                  maxlength="500"
                  show-word-limit
                  placeholder="例如：大学物理实验：测量重力加速度"
                />
              </el-form-item>
            </el-form>

            <div v-if="form.start && form.end" class="booking-summary">
              <div class="booking-summary__row">
                <span class="muted">预约时段</span>
                <span>{{ day }} {{ form.start }} ~ {{ form.end }}</span>
              </div>
              <div class="booking-summary__row">
                <span class="muted">申请人</span>
                <span>{{ displayName }}（提交时由登录态确定）</span>
              </div>
            </div>

            <el-button
              class="booking-submit"
              type="primary"
              size="large"
              :icon="Check"
              :loading="submitting"
              :disabled="isClosed"
              @click="submitReservation"
            >
              {{ isClosed ? '实验室已关闭' : '提交预约申请' }}
            </el-button>

            <div class="booking-help">
              <div><el-icon><Clock /></el-icon> 最多提前 7 天预约，必须同一天</div>
              <div><el-icon><User /></el-icon> 人数不能超过 {{ lab?.maxpeople ?? '—' }} 人</div>
            </div>
          </div>
        </section>
      </aside>
    </div>

    <!-- 管理员：编辑实验室 -->
    <LabFormDialog v-model="labDialogVisible" :lab="lab" @saved="onLabSaved" />

    <!-- 管理员：新增 / 编辑设备 -->
    <el-dialog
      v-model="equipmentVisible"
      :title="editingEquipment ? '编辑设备' : '添加设备'"
      width="480px"
      append-to-body
      destroy-on-close
    >
      <el-form label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="equipmentForm.name" maxlength="50" placeholder="例如 分光计" />
        </el-form-item>
        <el-form-item label="编号" required>
          <el-input v-model="equipmentForm.code" maxlength="50" placeholder="例如 EQ-PHY-002" />
        </el-form-item>
        <el-form-item label="型号">
          <el-input v-model="equipmentForm.model" maxlength="100" placeholder="例如 JJY-1" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="equipmentForm.status"
            :active-value="1"
            :inactive-value="0"
            active-text="可用"
            inactive-text="停用"
            inline-prompt
          />
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="equipmentForm.description"
            type="textarea"
            :rows="3"
            maxlength="2000"
            placeholder="用途、注意事项等"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="equipmentVisible = false">取消</el-button>
        <el-button type="primary" :loading="equipmentSaving" @click="saveEquipment">保存</el-button>
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

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 14px;
}

.info-item__label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #909399;
}

.info-item__value {
  margin-top: 4px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.detail-desc {
  margin-top: 16px;
  padding-top: 14px;
  font-size: 13px;
  line-height: 1.8;
  color: #606266;
  border-top: 1px dashed #ebeef5;
}

.detail-date {
  width: 150px;
}

.slot-legend {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  font-size: 12px;
  color: #909399;
}

.legend {
  display: flex;
  align-items: center;
  gap: 5px;
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

.slot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(116px, 1fr));
  gap: 8px;
}

.slot {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 8px 6px;
  font-size: 12px;
  background-color: #f7f9fc;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s;
}

.slot__time {
  font-weight: 600;
  color: #303133;
}

.slot__state {
  font-size: 11px;
  color: #909399;
}

.slot.is-free {
  border-color: var(--el-color-success-light-5);
  background-color: var(--el-color-success-light-9);
}

.slot.is-free .slot__state {
  color: var(--el-color-success);
}

.slot.is-free:hover {
  border-color: var(--el-color-success);
}

.slot.is-busy {
  cursor: not-allowed;
  opacity: 0.62;
}

.slot.is-picked {
  color: #fff;
  border-color: var(--el-color-primary);
  background-color: var(--el-color-primary);
}

.slot.is-picked .slot__time,
.slot.is-picked .slot__state {
  color: #fff;
}

.schedule-title {
  margin: 18px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.panel__table {
  padding: 8px 8px 12px;
  min-height: 90px;
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

.booking-form {
  margin-top: 14px;
}

.booking-full {
  width: 100%;
}

.booking-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.booking-summary {
  padding: 10px 12px;
  margin-bottom: 14px;
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

.booking-submit {
  width: 100%;
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

@media (max-width: 1100px) {
  .detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .detail-side {
    position: static;
  }
}
</style>
