<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  Calendar,
  ChatDotRound,
  CircleCheckFilled,
  Clock,
  Location,
  OfficeBuilding,
  Refresh,
  School,
  Timer,
  UserFilled
} from '@element-plus/icons-vue'
import { getLabPageApi } from '@/api/lab'
import { getRoomPageApi } from '@/api/room'
import {
  getMyLabReservationsApi,
  getMyLibraryReservationsApi,
  getMyReservationSummaryApi
} from '@/api/reservation'
import type { LabInfo } from '@/schemas/lab'
import type { RoomInfo } from '@/schemas/room'
import type {
  LabReservation,
  LibraryReservation,
  ReservationSummary
} from '@/schemas/reservation'
import { useUser } from '@/utils/user'
import { useChatStore } from '@/utils/chatStore'
import { reservationStatusMeta, labStatusMeta } from '@/utils/dict'
import {
  capacityText,
  formatOpenTime,
  formatReserveRange,
  seatCountText,
  todayText
} from '@/utils/format'

/**
 * 学生首页
 *
 * 一眼看清三件事：我还有哪些预约、哪里能约、有问题直接问助手。
 * 数据来自 4 个接口（概览 / 两类我的预约 / 场馆列表），全部并行请求，
 * 单个失败不影响其它区块（Promise.allSettled）。
 */
interface HomeReservation {
  key: string
  type: 'lab' | 'library'
  no: string
  target: string
  range: string
  start: string
  status: number
  targetId: number
}

const router = useRouter()
const { displayName, isAdmin } = useUser()
const { openChat } = useChatStore()

const loading = ref(false)
const summary = ref<ReservationSummary | null>(null)
const labReservations = ref<LabReservation[]>([])
const libraryReservations = ref<LibraryReservation[]>([])
const labs = ref<LabInfo[]>([])
const rooms = ref<RoomInfo[]>([])
const labTotal = ref(0)
const roomTotal = ref(0)

const quickEntries = [
  { path: '/manager/lab', label: '预约实验室', desc: '按小时看空闲时段', icon: OfficeBuilding },
  { path: '/manager/room', label: '自习室选座', desc: '按时段挑空座位', icon: School },
  { path: '/manager/reservations', label: '我的预约', desc: '查看审核进度 / 取消', icon: Calendar }
]

/** 两类预约合并展示：待审核优先，其次已通过，再按开始时间排；最多 6 条 */
const recentReservations = computed<HomeReservation[]>(() => {
  const items: HomeReservation[] = [
    ...labReservations.value.map((item) => ({
      key: `lab-${item.id}`,
      type: 'lab' as const,
      no: item.reservation_no,
      target: item.lab_name,
      range: formatReserveRange(item.reserve_start_time, item.reserve_end_time),
      start: item.reserve_start_time,
      status: item.status,
      targetId: item.lab_id
    })),
    ...libraryReservations.value.map((item) => ({
      key: `lib-${item.id}`,
      type: 'library' as const,
      no: item.reservation_no,
      target: `${item.room_name} · ${item.seat_code}`,
      range: formatReserveRange(item.reserve_start_time, item.reserve_end_time),
      start: item.reserve_start_time,
      status: item.status,
      targetId: item.room_id
    }))
  ]
  const priority = (status: number) => (status === 0 ? 0 : status === 1 ? 1 : 2)
  return items
    .sort(
      (a, b) => priority(a.status) - priority(b.status) || (a.start < b.start ? -1 : 1)
    )
    .slice(0, 6)
})

const pendingTotal = computed(() => summary.value?.pending_total ?? 0)
const approvedTotal = computed(
  () => (summary.value?.lab.approved ?? 0) + (summary.value?.library.approved ?? 0)
)
const labCount = computed(() => summary.value?.lab.total ?? 0)
const libraryCount = computed(() => summary.value?.library.total ?? 0)

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const [summaryRes, labRes, libRes, labsRes, roomsRes] = await Promise.allSettled([
      getMyReservationSummaryApi(),
      getMyLabReservationsApi({ page: 1, page_size: 5 }),
      getMyLibraryReservationsApi({ page: 1, page_size: 5 }),
      getLabPageApi({ page: 1, page_size: 6, status: 1 }),
      getRoomPageApi({ page: 1, page_size: 4, status: 1 })
    ])

    if (summaryRes.status === 'fulfilled') summary.value = summaryRes.value.data
    if (labRes.status === 'fulfilled') labReservations.value = labRes.value.data?.data ?? []
    if (libRes.status === 'fulfilled') {
      libraryReservations.value = libRes.value.data?.data ?? []
    }
    if (labsRes.status === 'fulfilled') {
      labs.value = labsRes.value.data?.data ?? []
      labTotal.value = labsRes.value.data?.total ?? 0
    }
    if (roomsRes.status === 'fulfilled') {
      rooms.value = roomsRes.value.data?.data ?? []
      roomTotal.value = roomsRes.value.data?.total ?? 0
    }
  } finally {
    loading.value = false
  }
}

function go(path: string): void {
  void router.push(path)
}

function goReservation(item: HomeReservation): void {
  void router.push(
    item.type === 'lab' ? `/manager/lab/${item.targetId}` : `/manager/room/${item.targetId}`
  )
}

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">学生首页</h2>
        <div class="page-desc">浏览实验室与自习室、提交预约、查看审核进度</div>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
    </div>

    <!-- 欢迎横幅 -->
    <section class="welcome">
      <div class="welcome__avatar"><el-icon><UserFilled /></el-icon></div>
      <div class="welcome__main">
        <div class="welcome__title">你好，{{ displayName }}</div>
        <div class="welcome__meta">
          <el-tag size="small" effect="light">学生</el-tag>
          <span>{{ todayText() }}</span>
          <span class="welcome__dot">·</span>
          <span>今天有 {{ pendingTotal }} 条预约在等审核</span>
        </div>
      </div>
      <div class="welcome__actions">
        <el-button type="primary" :icon="OfficeBuilding" @click="go('/manager/lab')">
          去预约实验室
        </el-button>
        <el-button :icon="ChatDotRound" plain @click="openChat">问问助手</el-button>
      </div>
    </section>

    <!-- 概览 -->
    <section class="stats">
      <div class="stat">
        <div class="stat__icon is-warning"><el-icon><Timer /></el-icon></div>
        <div>
          <div class="stat__value">{{ pendingTotal }}</div>
          <div class="stat__label">待审核</div>
        </div>
      </div>
      <div class="stat">
        <div class="stat__icon is-success"><el-icon><CircleCheckFilled /></el-icon></div>
        <div>
          <div class="stat__value">{{ approvedTotal }}</div>
          <div class="stat__label">预约成功</div>
        </div>
      </div>
      <div class="stat">
        <div class="stat__icon"><el-icon><OfficeBuilding /></el-icon></div>
        <div>
          <div class="stat__value">{{ labCount }}</div>
          <div class="stat__label">实验室预约</div>
        </div>
      </div>
      <div class="stat">
        <div class="stat__icon is-info"><el-icon><School /></el-icon></div>
        <div>
          <div class="stat__value">{{ libraryCount }}</div>
          <div class="stat__label">座位预约</div>
        </div>
      </div>
      <div class="stat">
        <div class="stat__icon is-success"><el-icon><School /></el-icon></div>
        <div>
          <div class="stat__value">{{ roomTotal }}</div>
          <div class="stat__label">开放自习室</div>
        </div>
      </div>
    </section>

    <div class="home-grid">
      <!-- 左：快捷入口 -->
      <section class="panel quick">
        <div class="panel__header">
          <div class="panel__title">快捷入口</div>
        </div>
        <div class="quick__list">
          <button
            v-for="item in quickEntries"
            :key="item.path"
            type="button"
            class="quick__item"
            @click="go(item.path)"
          >
            <span class="quick__icon"><el-icon><component :is="item.icon" /></el-icon></span>
            <span class="quick__text">
              <span class="quick__label">{{ item.label }}</span>
              <span class="quick__desc">{{ item.desc }}</span>
            </span>
            <el-icon class="quick__arrow"><ArrowRight /></el-icon>
          </button>
        </div>
        <div v-if="isAdmin" class="quick__hint">
          你是管理员，去 <el-link type="primary" @click="go('/manager/home')">系统首页</el-link>
          管理预约与用户。
        </div>
      </section>

      <!-- 右：我的预约 -->
      <section class="panel">
        <div class="panel__header">
          <div class="panel__title">
            <el-icon><Calendar /></el-icon>
            我的预约
          </div>
          <el-button link type="primary" :icon="ArrowRight" @click="go('/manager/reservations')">
            查看全部
          </el-button>
        </div>
        <div v-loading="loading" class="recent">
          <el-table
            v-if="recentReservations.length"
            :data="recentReservations"
            style="width: 100%"
            stripe
          >
            <el-table-column label="类型" width="92">
              <template #default="{ row }">
                <el-tag size="small" :type="row.type === 'lab' ? 'primary' : 'success'" effect="plain">
                  {{ row.type === 'lab' ? '实验室' : '自习室' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="target" label="预约对象" min-width="170" show-overflow-tooltip />
            <el-table-column prop="range" label="预约时段" min-width="200" />
            <el-table-column label="状态" width="110" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="reservationStatusMeta(row.status).type">
                  {{ reservationStatusMeta(row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="goReservation(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else-if="!loading" description="还没有预约记录，去约一间实验室吧" :image-size="88" />
        </div>
      </section>
    </div>

    <!-- 可预约的场馆 -->
    <div class="venues">
      <section class="panel">
        <div class="panel__header">
          <div class="panel__title">
            <el-icon><OfficeBuilding /></el-icon>
            开放中的实验室
            <span class="muted">（{{ labTotal }} 间）</span>
          </div>
          <el-button link type="primary" :icon="ArrowRight" @click="go('/manager/lab')">
            全部实验室
          </el-button>
        </div>
        <div v-loading="loading" class="venue-grid">
          <article
            v-for="lab in labs"
            :key="lab.id"
            class="venue"
            @click="go(`/manager/lab/${lab.id}`)"
          >
            <div class="venue__head">
              <span class="venue__name" :title="lab.name">{{ lab.name }}</span>
              <el-tag size="small" :type="labStatusMeta(lab.status).type" effect="plain">
                {{ labStatusMeta(lab.status).label }}
              </el-tag>
            </div>
            <div class="venue__meta">
              <el-icon><Location /></el-icon>
              <span>{{ lab.location || '位置未填' }}</span>
            </div>
            <div class="venue__meta">
              <el-icon><Clock /></el-icon>
              <span>{{ formatOpenTime(lab.open_time, lab.close_time) }}</span>
              <span class="venue__dot">·</span>
              <span>{{ capacityText(lab.maxpeople) }}</span>
            </div>
          </article>
          <el-empty v-if="!labs.length && !loading" description="当前没有开放中的实验室" :image-size="80" />
        </div>
      </section>

      <section class="panel">
        <div class="panel__header">
          <div class="panel__title">
            <el-icon><School /></el-icon>
            图书馆自习室
            <span class="muted">（{{ roomTotal }} 间）</span>
          </div>
          <el-button link type="primary" :icon="ArrowRight" @click="go('/manager/room')">
            全部自习室
          </el-button>
        </div>
        <div v-loading="loading" class="venue-grid">
          <article
            v-for="room in rooms"
            :key="room.id"
            class="venue"
            @click="go(`/manager/room/${room.id}`)"
          >
            <div class="venue__head">
              <span class="venue__name" :title="room.name">{{ room.name }}</span>
              <el-tag size="small" :type="labStatusMeta(room.status).type" effect="plain">
                {{ labStatusMeta(room.status).label }}
              </el-tag>
            </div>
            <div class="venue__meta">
              <el-icon><Location /></el-icon>
              <span>{{ room.location || '位置未填' }}</span>
            </div>
            <div class="venue__meta">
              <el-icon><Clock /></el-icon>
              <span>{{ formatOpenTime(room.open_time, room.close_time) }}</span>
              <span class="venue__dot">·</span>
              <span>{{ seatCountText(room.capacity) }}</span>
            </div>
          </article>
          <el-empty v-if="!rooms.length && !loading" description="当前没有开放中的自习室" :image-size="80" />
        </div>
      </section>
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
  background: linear-gradient(120deg, #1f4fd8, #4080ff 58%, #6f9dff);
  border-radius: 10px;
  box-shadow: 0 10px 24px rgba(64, 128, 255, 0.22);
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
  background-color: rgba(255, 255, 255, 0.2);
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
  opacity: 0.92;
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
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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

.stat__icon.is-success {
  color: var(--el-color-success);
  background-color: var(--el-color-success-light-9);
}

.stat__icon.is-warning {
  color: var(--el-color-warning);
  background-color: var(--el-color-warning-light-9);
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

.home-grid {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  margin-bottom: 16px;
}

.quick__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
}

.quick__item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  text-align: left;
  background-color: #f7f9fc;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.2s, background-color 0.2s;
}

.quick__item:hover {
  background-color: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
}

.quick__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: none;
  font-size: 17px;
  color: var(--el-color-primary);
  background-color: #fff;
  border-radius: 8px;
}

.quick__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.quick__label {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.quick__desc {
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.quick__arrow {
  margin-left: auto;
  color: #c0c4cc;
}

.quick__hint {
  padding: 12px 16px 16px;
  font-size: 12px;
  line-height: 1.7;
  color: #909399;
  border-top: 1px dashed #ebeef5;
}

.recent {
  min-height: 200px;
  padding: 8px 8px 16px;
}

.venues {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
  align-items: start;
}

.venues .panel + .panel {
  margin-top: 0;
}

.venue-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 12px;
  padding: 16px;
  min-height: 150px;
}

.venue {
  padding: 12px;
  cursor: pointer;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.venue:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 4px 12px rgba(64, 128, 255, 0.12);
}

.venue__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.venue__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.venue__meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.venue__dot {
  margin: 0 2px;
}

@media (max-width: 992px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .welcome {
    flex-wrap: wrap;
  }
}
</style>
