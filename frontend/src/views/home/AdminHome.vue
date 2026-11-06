<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  Bell,
  Calendar,
  ChatDotRound,
  Clock,
  DataLine,
  OfficeBuilding,
  Refresh,
  School,
  Timer,
  UserFilled
} from '@element-plus/icons-vue'
import { getLabPageApi } from '@/api/lab'
import { getRoomPageApi } from '@/api/room'
import { getAllLabReservationsApi, getAllLibraryReservationsApi } from '@/api/reservation'
import { getUserPageApi, getUserUsageApi } from '@/api/user'
import type { LabInfo } from '@/schemas/lab'
import type { RoomInfo } from '@/schemas/room'
import type { LabReservation, LibraryReservation } from '@/schemas/reservation'
import type { UsageInfo } from '@/schemas/user'
import { useUser } from '@/utils/user'
import { useChatStore } from '@/utils/chatStore'
import { labStatusMeta, reservationStatusMeta } from '@/utils/dict'
import { formatOpenTime, formatReserveRange, todayText } from '@/utils/format'

/**
 * 管理员首页
 *
 * 聚合「待办 + 资源 + 助手用量」三类信息，全部并行请求：
 * 待审核的实验室 / 座位预约一眼可见，点「去审核」带着筛选条件跳到预约审批页。
 */
const router = useRouter()
const { userInfo, displayName } = useUser()
const { openChat } = useChatStore()

const loading = ref(false)
const labPending = ref<LabReservation[]>([])
const libPending = ref<LibraryReservation[]>([])
const labs = ref<LabInfo[]>([])
const rooms = ref<RoomInfo[]>([])
const usage = ref<UsageInfo | null>(null)
const counts = ref({
  labPending: 0,
  libPending: 0,
  labs: 0,
  rooms: 0,
  users: 0
})

const pendingTotal = computed(() => counts.value.labPending + counts.value.libPending)
const tokenPercent = computed(() => {
  if (!usage.value || usage.value.daily_tokens_limit <= 0) return 0
  return Math.min(100, Math.round((usage.value.tokens / usage.value.daily_tokens_limit) * 100))
})

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const [labRes, libRes, labsRes, roomsRes, usersRes, usageRes] = await Promise.allSettled([
      getAllLabReservationsApi({ status: [0], page: 1, page_size: 5 }),
      getAllLibraryReservationsApi({ status: [0], page: 1, page_size: 5 }),
      getLabPageApi({ page: 1, page_size: 5 }),
      getRoomPageApi({ page: 1, page_size: 4 }),
      getUserPageApi({ page: 1, page_size: 1 }),
      userInfo.value ? getUserUsageApi(userInfo.value.id) : Promise.resolve(null)
    ])

    if (labRes.status === 'fulfilled') {
      labPending.value = labRes.value.data?.data ?? []
      counts.value.labPending = labRes.value.data?.total ?? 0
    }
    if (libRes.status === 'fulfilled') {
      libPending.value = libRes.value.data?.data ?? []
      counts.value.libPending = libRes.value.data?.total ?? 0
    }
    if (labsRes.status === 'fulfilled') {
      labs.value = labsRes.value.data?.data ?? []
      counts.value.labs = labsRes.value.data?.total ?? 0
    }
    if (roomsRes.status === 'fulfilled') {
      rooms.value = roomsRes.value.data?.data ?? []
      counts.value.rooms = roomsRes.value.data?.total ?? 0
    }
    if (usersRes.status === 'fulfilled') {
      counts.value.users = usersRes.value.data?.total ?? 0
    }
    if (usageRes.status === 'fulfilled' && usageRes.value) {
      usage.value = usageRes.value.data
    }
  } finally {
    loading.value = false
  }
}

function goReview(type: 'lab' | 'library'): void {
  void router.push({
    path: '/manager/reservations',
    query: { type, status: String(0) }
  })
}

function go(path: string): void {
  void router.push(path)
}

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">系统首页</h2>
        <div class="page-desc">预约审批、场馆与用户概览（数据来自后端实时接口）</div>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
    </div>

    <section class="welcome">
      <div class="welcome__avatar"><el-icon><UserFilled /></el-icon></div>
      <div class="welcome__main">
        <div class="welcome__title">你好，{{ displayName }}</div>
        <div class="welcome__meta">
          <el-tag size="small" effect="light">管理员</el-tag>
          <span>{{ todayText() }}</span>
          <span class="welcome__dot">·</span>
          <span>今日待处理 {{ pendingTotal }} 条预约</span>
        </div>
      </div>
      <div class="welcome__actions">
        <el-button type="primary" :icon="Calendar" @click="goReview('lab')">去审批</el-button>
        <el-button :icon="ChatDotRound" plain @click="openChat">问助手</el-button>
      </div>
    </section>

    <section class="stats">
      <div class="stat is-warning" @click="goReview('lab')">
        <div class="stat__icon is-warning"><el-icon><Timer /></el-icon></div>
        <div>
          <div class="stat__value">{{ counts.labPending }}</div>
          <div class="stat__label">待审核实验室</div>
        </div>
        <el-icon class="stat__arrow"><ArrowRight /></el-icon>
      </div>
      <div class="stat is-warning" @click="goReview('library')">
        <div class="stat__icon is-warning"><el-icon><Timer /></el-icon></div>
        <div>
          <div class="stat__value">{{ counts.libPending }}</div>
          <div class="stat__label">待审核座位</div>
        </div>
        <el-icon class="stat__arrow"><ArrowRight /></el-icon>
      </div>
      <div class="stat" @click="go('/manager/lab')">
        <div class="stat__icon"><el-icon><OfficeBuilding /></el-icon></div>
        <div>
          <div class="stat__value">{{ counts.labs }}</div>
          <div class="stat__label">实验室总数</div>
        </div>
      </div>
      <div class="stat" @click="go('/manager/room')">
        <div class="stat__icon is-success"><el-icon><School /></el-icon></div>
        <div>
          <div class="stat__value">{{ counts.rooms }}</div>
          <div class="stat__label">自习室总数</div>
        </div>
      </div>
      <div class="stat" @click="go('/manager/users')">
        <div class="stat__icon is-info"><el-icon><UserFilled /></el-icon></div>
        <div>
          <div class="stat__value">{{ counts.users }}</div>
          <div class="stat__label">注册用户</div>
        </div>
      </div>
    </section>

    <div class="admin-grid">
      <div class="admin-main">
        <!-- 待审核实验室 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Bell /></el-icon>
              实验室预约待审核
              <el-tag v-if="counts.labPending" size="small" type="warning" effect="plain">
                {{ counts.labPending }}
              </el-tag>
            </div>
            <el-button link type="primary" :icon="ArrowRight" @click="goReview('lab')">
              去审批
            </el-button>
          </div>
          <div v-loading="loading" class="panel__table">
            <el-table v-if="labPending.length" :data="labPending" size="small" stripe>
              <el-table-column prop="user_name" label="申请人" width="90" />
              <el-table-column prop="lab_name" label="实验室" min-width="150" show-overflow-tooltip />
              <el-table-column label="预约时段" min-width="190">
                <template #default="{ row }">
                  {{ formatReserveRange(row.reserve_start_time, row.reserve_end_time) }}
                </template>
              </el-table-column>
              <el-table-column label="人数" width="70" align="center">
                <template #default="{ row }">{{ row.people_num ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="reservationStatusMeta(row.status).type">
                    {{ reservationStatusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else-if="!loading" description="没有待审核的实验室预约" :image-size="72" />
          </div>
        </section>

        <!-- 待审核座位 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><Bell /></el-icon>
              座位预约待审核
              <el-tag v-if="counts.libPending" size="small" type="warning" effect="plain">
                {{ counts.libPending }}
              </el-tag>
            </div>
            <el-button link type="primary" :icon="ArrowRight" @click="goReview('library')">
              去审批
            </el-button>
          </div>
          <div v-loading="loading" class="panel__table">
            <el-table v-if="libPending.length" :data="libPending" size="small" stripe>
              <el-table-column prop="user_name" label="申请人" width="90" />
              <el-table-column label="自习室 / 座位" min-width="170" show-overflow-tooltip>
                <template #default="{ row }">{{ row.room_name }} · {{ row.seat_code }}</template>
              </el-table-column>
              <el-table-column label="预约时段" min-width="190">
                <template #default="{ row }">
                  {{ formatReserveRange(row.reserve_start_time, row.reserve_end_time) }}
                </template>
              </el-table-column>
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="reservationStatusMeta(row.status).type">
                    {{ reservationStatusMeta(row.status).label }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else-if="!loading" description="没有待审核的座位预约" :image-size="72" />
          </div>
        </section>
      </div>

      <div class="admin-side">
        <!-- 助手用量 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><DataLine /></el-icon>
              今日助手用量
            </div>
          </div>
          <div class="panel__body">
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
                配额 {{ usage.daily_tokens_limit.toLocaleString() }} tokens · 已用 {{ tokenPercent }}%
              </div>

              <div class="usage-row usage-row--mt">
                <span class="usage-label">调用次数</span>
                <span class="usage-value">{{ usage.calls }} / {{ usage.daily_calls_limit }}</span>
              </div>
              <div class="usage-hint">日期 {{ usage.date }}（按北京时间自然日）</div>
            </template>
            <el-empty v-else description="暂无用量的统计数据" :image-size="64" />
          </div>
        </section>

        <!-- 场馆状态 -->
        <section class="panel">
          <div class="panel__header">
            <div class="panel__title">
              <el-icon><OfficeBuilding /></el-icon>
              场馆状态
            </div>
          </div>
          <div class="venue-list">
            <button
              v-for="lab in labs"
              :key="`lab-${lab.id}`"
              type="button"
              class="venue-item"
              @click="go(`/manager/lab/${lab.id}`)"
            >
              <el-icon class="venue-item__icon"><OfficeBuilding /></el-icon>
              <span class="venue-item__name">{{ lab.name }}</span>
              <el-tag size="small" :type="labStatusMeta(lab.status).type" effect="plain">
                {{ labStatusMeta(lab.status).label }}
              </el-tag>
            </button>
            <button
              v-for="room in rooms"
              :key="`room-${room.id}`"
              type="button"
              class="venue-item"
              @click="go(`/manager/room/${room.id}`)"
            >
              <el-icon class="venue-item__icon"><School /></el-icon>
              <span class="venue-item__name">{{ room.name }}</span>
              <el-tag size="small" :type="labStatusMeta(room.status).type" effect="plain">
                {{ labStatusMeta(room.status).label }}
              </el-tag>
            </button>
            <div v-if="!labs.length && !rooms.length && !loading" class="venue-empty">
              还没有场馆数据，先在「实验室管理」里创建
            </div>
          </div>
          <div class="venue-foot">
            <el-icon><Clock /></el-icon>
            实验室开放示例：{{ formatOpenTime(labs[0]?.open_time, labs[0]?.close_time) }}
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.welcome {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 22px;
  margin-bottom: 16px;
  color: #fff;
  background: linear-gradient(120deg, #b7791f, #e0a52c 55%, #f5c462);
  border-radius: 10px;
  box-shadow: 0 10px 24px rgba(224, 165, 44, 0.22);
}

.welcome__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  flex: none;
  font-size: 26px;
  border-radius: 12px;
  background-color: rgba(255, 255, 255, 0.22);
}

.welcome__main {
  flex: 1;
  min-width: 0;
}

.welcome__title {
  font-size: 20px;
  font-weight: 700;
}

.welcome__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 13px;
  opacity: 0.94;
}

.welcome__meta :deep(.el-tag) {
  color: #fff;
  border-color: rgba(255, 255, 255, 0.5);
  background-color: rgba(255, 255, 255, 0.18);
}

.welcome__dot {
  opacity: 0.6;
}

.welcome__actions {
  display: flex;
  gap: 8px;
  flex: none;
}

.welcome__actions :deep(.el-button) {
  border-color: rgba(255, 255, 255, 0.6);
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(165px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background-color: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  cursor: pointer;
  transition: box-shadow 0.2s;
}

.stat:hover {
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
}

.stat__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex: none;
  font-size: 20px;
  color: var(--el-color-primary);
  background-color: var(--el-color-primary-light-9);
  border-radius: 8px;
}

.stat__icon.is-warning {
  color: var(--el-color-warning);
  background-color: var(--el-color-warning-light-9);
}

.stat__icon.is-success {
  color: var(--el-color-success);
  background-color: var(--el-color-success-light-9);
}

.stat__icon.is-info {
  color: var(--el-color-info);
  background-color: var(--el-color-info-light-9);
}

.stat__value {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
  color: #303133;
}

.stat__label {
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.stat__arrow {
  margin-left: auto;
  color: #c0c4cc;
}

.admin-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.admin-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.admin-main .panel + .panel,
.admin-side .panel + .panel {
  margin-top: 0;
}

.panel__table {
  min-height: 130px;
  padding: 8px 8px 12px;
}

.admin-side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.usage-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}

.usage-row--mt {
  margin-top: 18px;
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

.venue-list {
  padding: 8px;
}

.venue-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 8px;
  font-size: 13px;
  text-align: left;
  background: none;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.venue-item:hover {
  background-color: #f5f7fa;
}

.venue-item__icon {
  flex: none;
  color: var(--el-color-primary);
}

.venue-item__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #303133;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.venue-empty {
  padding: 18px 10px;
  font-size: 12px;
  color: #909399;
  text-align: center;
}

.venue-foot {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 16px;
  font-size: 12px;
  color: #909399;
  border-top: 1px dashed #ebeef5;
}

@media (max-width: 1200px) {
  .admin-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
