<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Refresh, Search } from '@element-plus/icons-vue'
import {
  adminCancelLabReservationApi,
  adminCancelLibraryReservationApi,
  cancelLabReservationApi,
  cancelLibraryReservationApi,
  getAllLabReservationsApi,
  getAllLibraryReservationsApi,
  getMyLabReservationsApi,
  getMyLibraryReservationsApi,
  reviewLabReservationApi,
  reviewLibraryReservationApi
} from '@/api/reservation'
import type { LabReservation, LibraryReservation } from '@/schemas/reservation'
import { useUser } from '@/utils/user'
import {
  RESERVATION_CANCELLABLE_STATUSES,
  RESERVATION_STATUS_APPROVED,
  RESERVATION_STATUS_OPTIONS,
  RESERVATION_STATUS_PENDING,
  reservationStatusMeta
} from '@/utils/dict'
import { formatDateTime, formatReserveRange } from '@/utils/format'

/**
 * 预约管理
 *
 * 学生：我的预约（只查自己的，接口里 user_id 由后端从 JWT 取）
 * 管理员：预约审批（查全部 + 通过 / 驳回 / 代取消）
 *
 * 两类预约的表结构不同（实验室 vs 自习室座位），所以用「页签 + 两套表格」，
 * 但筛选、分页、审核/取消弹窗是共用的。
 */
type AffairType = 'lab' | 'library'

const route = useRoute()
const router = useRouter()
const { isAdmin } = useUser()

const activeType = ref<AffairType>('lab')
const statusFilter = ref<number | null>(null)
const keyword = ref('')
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)

const labRows = ref<LabReservation[]>([])
const libraryRows = ref<LibraryReservation[]>([])
const total = ref(0)

// 审核弹窗（管理员）
const reviewVisible = ref(false)
const reviewSaving = ref(false)
const reviewTarget = ref<LabReservation | LibraryReservation | null>(null)
const reviewForm = reactive<{ status: 1 | 2; remark: string }>({ status: 1, remark: '' })

// 取消弹窗（学生 / 管理员）
const cancelVisible = ref(false)
const cancelSaving = ref(false)
const cancelTarget = ref<LabReservation | LibraryReservation | null>(null)
const cancelRemark = ref('')

const pageTitle = computed(() => (isAdmin.value ? '预约审批' : '我的预约'))
const pageDesc = computed(() =>
  isAdmin.value
    ? '审核学生提交的实验室 / 座位预约，也可以代取消（例如临时检修）'
    : '查看审核进度、取消还没开始的预约'
)

const rows = computed(() => (activeType.value === 'lab' ? labRows.value : libraryRows.value))

function isCancellable(status: number): boolean {
  return RESERVATION_CANCELLABLE_STATUSES.includes(status)
}

async function loadList(): Promise<void> {
  loading.value = true
  try {
    const params = {
      status: statusFilter.value === null ? undefined : [statusFilter.value],
      keyword: keyword.value.trim() || undefined,
      page: page.value,
      page_size: pageSize.value
    }
    if (activeType.value === 'lab') {
      const res = isAdmin.value
        ? await getAllLabReservationsApi(params)
        : await getMyLabReservationsApi(params)
      labRows.value = res.data?.data ?? []
      total.value = res.data?.total ?? 0
    } else {
      const res = isAdmin.value
        ? await getAllLibraryReservationsApi(params)
        : await getMyLibraryReservationsApi(params)
      libraryRows.value = res.data?.data ?? []
      total.value = res.data?.total ?? 0
    }
  } catch {
    labRows.value = []
    libraryRows.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void loadList()
}

function reset(): void {
  statusFilter.value = null
  keyword.value = ''
  search()
}

function onTabChange(): void {
  page.value = 1
  void loadList()
}

function goDetail(row: LabReservation | LibraryReservation): void {
  if ('lab_id' in row) {
    void router.push(`/manager/lab/${row.lab_id}`)
  } else {
    void router.push(`/manager/room/${row.room_id}`)
  }
}

// ---------------------------------------------------------------- 审核
function openReview(row: LabReservation | LibraryReservation, status: 1 | 2): void {
  reviewTarget.value = row
  reviewForm.status = status
  reviewForm.remark = ''
  reviewVisible.value = true
}

async function submitReview(): Promise<void> {
  const target = reviewTarget.value
  if (!target) return

  reviewSaving.value = true
  try {
    const isLab = 'lab_id' in target
    const res = isLab
      ? await reviewLabReservationApi(target.id, reviewForm.status, reviewForm.remark.trim() || undefined)
      : await reviewLibraryReservationApi(
          target.id,
          reviewForm.status,
          reviewForm.remark.trim() || undefined
        )
    if (res.code === 200) {
      ElMessage.success(reviewForm.status === RESERVATION_STATUS_APPROVED ? '已通过' : '已驳回')
      reviewVisible.value = false
      await loadList()
    }
  } catch {
    // 时段冲突 / 重复审核等提示已由响应拦截器弹出
  } finally {
    reviewSaving.value = false
  }
}

// ---------------------------------------------------------------- 取消
function openCancel(row: LabReservation | LibraryReservation): void {
  cancelTarget.value = row
  cancelRemark.value = ''
  cancelVisible.value = true
}

async function submitCancel(): Promise<void> {
  const target = cancelTarget.value
  if (!target) return

  cancelSaving.value = true
  try {
    const isLab = 'lab_id' in target
    const remark = cancelRemark.value.trim() || undefined
    const res = isLab
      ? isAdmin.value
        ? await adminCancelLabReservationApi(target.id, remark)
        : await cancelLabReservationApi(target.id, remark)
      : isAdmin.value
        ? await adminCancelLibraryReservationApi(target.id, remark)
        : await cancelLibraryReservationApi(target.id, remark)
    if (res.code === 200) {
      ElMessage.success('预约已取消')
      cancelVisible.value = false
      await loadList()
    }
  } catch {
    // 已取消 / 已驳回等提示已由响应拦截器弹出
  } finally {
    cancelSaving.value = false
  }
}

function targetText(row: LabReservation | LibraryReservation): string {
  return 'lab_id' in row ? row.lab_name : `${row.room_name} · ${row.seat_code}`
}

async function quickCancel(row: LabReservation | LibraryReservation): Promise<void> {
  const target = targetText(row)
  try {
    await ElMessageBox.confirm(`确定取消「${target}」的预约吗？取消后时段会被释放。`, '取消预约', {
      type: 'warning',
      confirmButtonText: '取消预约',
      cancelButtonText: '再想想'
    })
  } catch {
    return
  }

  cancelTarget.value = row
  cancelRemark.value = isAdmin.value ? '管理员代取消' : '学生本人取消'
  await submitCancel()
}

function onPageChange(current: number): void {
  page.value = current
  void loadList()
}

onMounted(() => {
  const type = route.query.type
  if (type === 'lab' || type === 'library') activeType.value = type
  const status = route.query.status
  if (typeof status === 'string' && status !== '') {
    const parsed = Number(status)
    if (!Number.isNaN(parsed)) statusFilter.value = parsed
  }
  void loadList()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">{{ pageTitle }}</h2>
        <div class="page-desc">{{ pageDesc }}</div>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadList">刷新</el-button>
    </div>

    <!-- 页签 + 筛选 -->
    <section class="panel">
      <div class="tabs">
        <button
          type="button"
          class="tab"
          :class="{ 'is-active': activeType === 'lab' }"
          @click="activeType = 'lab'; onTabChange()"
        >
          实验室预约
        </button>
        <button
          type="button"
          class="tab"
          :class="{ 'is-active': activeType === 'library' }"
          @click="activeType = 'library'; onTabChange()"
        >
          自习室座位
        </button>
      </div>

      <div class="toolbar filters">
        <el-input
          v-model="keyword"
          class="filters__keyword"
          :placeholder="activeType === 'lab' ? '搜索单号 / 实验室 / 申请人' : '搜索单号 / 自习室 / 座位号'"
          :prefix-icon="Search"
          clearable
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="statusFilter" class="filters__select" placeholder="全部状态" clearable>
          <el-option
            v-for="option in RESERVATION_STATUS_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
        <span class="muted filters__total">共 {{ total }} 条</span>
      </div>
    </section>

    <section class="panel table-panel">
      <div v-loading="loading" class="table-wrap">
        <!-- 实验室预约 -->
        <el-table
          v-if="activeType === 'lab'"
          :data="labRows"
          stripe
          empty-text="没有符合条件的实验室预约"
        >
          <el-table-column prop="reservation_no" label="预约单号" width="185" show-overflow-tooltip />
          <el-table-column prop="lab_name" label="实验室" min-width="160" show-overflow-tooltip />
          <el-table-column v-if="isAdmin" prop="user_name" label="申请人" width="100" />
          <el-table-column label="预约时段" min-width="210">
            <template #default="{ row }">
              {{ formatReserveRange(row.reserve_start_time, row.reserve_end_time) }}
            </template>
          </el-table-column>
          <el-table-column label="人数" width="70" align="center">
            <template #default="{ row }">{{ row.people_num ?? '—' }}</template>
          </el-table-column>
          <el-table-column prop="purpose" label="用途" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.purpose || '—' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="reservationStatusMeta(row.status).type">
                {{ reservationStatusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="remark" label="处理说明" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.remark || '—' }}</template>
          </el-table-column>
          <el-table-column v-if="isAdmin" label="处理人" width="120" show-overflow-tooltip>
            <template #default="{ row }">
              <div v-if="row.handler_name">
                {{ row.handler_name }}
                <div class="handler-time">{{ formatDateTime(row.handle_time) }}</div>
              </div>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="225" align="center" fixed="right">
            <template #default="{ row }">
              <template v-if="isAdmin && row.status === RESERVATION_STATUS_PENDING">
                <el-button link type="success" size="small" :icon="Check" @click="openReview(row, 1)">
                  通过
                </el-button>
                <el-button link type="danger" size="small" :icon="Close" @click="openReview(row, 2)">
                  驳回
                </el-button>
              </template>
              <el-button
                v-if="isCancellable(row.status)"
                link
                type="warning"
                size="small"
                @click="quickCancel(row)"
              >
                取消
              </el-button>
              <el-button link type="primary" size="small" @click="goDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 自习室座位预约 -->
        <el-table
          v-else
          :data="libraryRows"
          stripe
          empty-text="没有符合条件的座位预约"
        >
          <el-table-column prop="reservation_no" label="预约单号" width="185" show-overflow-tooltip />
          <el-table-column label="自习室 / 座位" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.room_name }} · {{ row.seat_code }}</template>
          </el-table-column>
          <el-table-column v-if="isAdmin" prop="user_name" label="申请人" width="100" />
          <el-table-column label="预约时段" min-width="210">
            <template #default="{ row }">
              {{ formatReserveRange(row.reserve_start_time, row.reserve_end_time) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="reservationStatusMeta(row.status).type">
                {{ reservationStatusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="remark" label="处理说明" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.remark || '—' }}</template>
          </el-table-column>
          <el-table-column v-if="isAdmin" label="处理人" width="120" show-overflow-tooltip>
            <template #default="{ row }">
              <div v-if="row.handler_name">
                {{ row.handler_name }}
                <div class="handler-time">{{ formatDateTime(row.handle_time) }}</div>
              </div>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="225" align="center" fixed="right">
            <template #default="{ row }">
              <template v-if="isAdmin && row.status === RESERVATION_STATUS_PENDING">
                <el-button link type="success" size="small" :icon="Check" @click="openReview(row, 1)">
                  通过
                </el-button>
                <el-button link type="danger" size="small" :icon="Close" @click="openReview(row, 2)">
                  驳回
                </el-button>
              </template>
              <el-button
                v-if="isCancellable(row.status)"
                link
                type="warning"
                size="small"
                @click="quickCancel(row)"
              >
                取消
              </el-button>
              <el-button link type="primary" size="small" @click="goDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="total > pageSize" class="pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="onPageChange"
        />
      </div>
    </section>

    <!-- 审核弹窗 -->
    <el-dialog
      v-model="reviewVisible"
      :title="reviewForm.status === RESERVATION_STATUS_APPROVED ? '通过预约' : '驳回预约'"
      width="460px"
      append-to-body
    >
      <div v-if="reviewTarget" class="dialog-summary">
        <div class="dialog-summary__row">
          <span class="muted">预约对象</span>
          <span>{{ targetText(reviewTarget) }}</span>
        </div>
        <div class="dialog-summary__row">
          <span class="muted">申请人</span>
          <span>{{ reviewTarget.user_name }}</span>
        </div>
        <div class="dialog-summary__row">
          <span class="muted">预约时段</span>
          <span>
            {{ formatReserveRange(reviewTarget.reserve_start_time, reviewTarget.reserve_end_time) }}
          </span>
        </div>
      </div>
      <el-form label-position="top">
        <el-form-item label="处理说明（驳回时建议写清原因）">
          <el-input
            v-model="reviewForm.remark"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            :placeholder="
              reviewForm.status === RESERVATION_STATUS_APPROVED
                ? '例如：已核对设备清单，准予使用'
                : '例如：该时段已安排课程实验，请改约其它时段'
            "
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button
          :type="reviewForm.status === RESERVATION_STATUS_APPROVED ? 'primary' : 'danger'"
          :loading="reviewSaving"
          @click="submitReview"
        >
          {{ reviewForm.status === RESERVATION_STATUS_APPROVED ? '确认通过' : '确认驳回' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 取消弹窗 -->
    <el-dialog v-model="cancelVisible" title="取消预约" width="440px" append-to-body>
      <div v-if="cancelTarget" class="dialog-summary">
        <div class="dialog-summary__row">
          <span class="muted">预约对象</span>
          <span>{{ targetText(cancelTarget) }}</span>
        </div>
        <div class="dialog-summary__row">
          <span class="muted">预约时段</span>
          <span>
            {{ formatReserveRange(cancelTarget.reserve_start_time, cancelTarget.reserve_end_time) }}
          </span>
        </div>
      </div>
      <el-form label-position="top">
        <el-form-item label="取消原因（可选）">
          <el-input
            v-model="cancelRemark"
            type="textarea"
            :rows="2"
            maxlength="1000"
            placeholder="例如：课程时间冲突"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelVisible = false">再想想</el-button>
        <el-button type="warning" :loading="cancelSaving" @click="submitCancel">
          确认取消
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 4px;
  padding: 10px 16px 0;
  border-bottom: 1px solid #ebeef5;
}

.tab {
  position: relative;
  padding: 10px 16px;
  font-size: 14px;
  color: #606266;
  background: none;
  border: none;
  cursor: pointer;
  transition: color 0.2s;
}

.tab:hover {
  color: var(--el-color-primary);
}

.tab.is-active {
  font-weight: 600;
  color: var(--el-color-primary);
}

.tab.is-active::after {
  position: absolute;
  right: 12px;
  bottom: -1px;
  left: 12px;
  height: 2px;
  content: '';
  background-color: var(--el-color-primary);
  border-radius: 1px;
}

.filters {
  padding: 14px 16px;
}

.filters__keyword {
  width: 300px;
}

.filters__select {
  width: 150px;
}

.filters__total {
  margin-left: auto;
  font-size: 13px;
}

.table-panel {
  margin-top: 16px;
  padding: 8px 8px 4px;
}

.table-wrap {
  min-height: 240px;
}

.handler-time {
  font-size: 11px;
  color: #909399;
}

.dialog-summary {
  padding: 10px 12px;
  margin-bottom: 14px;
  font-size: 12px;
  background-color: #f7f9fc;
  border-radius: 6px;
}

.dialog-summary__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 3px 0;
  color: #303133;
}

.dialog-summary__row span:last-child {
  text-align: right;
}

@media (max-width: 768px) {
  .filters__keyword,
  .filters__select {
    width: 100%;
  }
}
</style>
