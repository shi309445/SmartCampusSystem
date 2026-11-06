<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Clock,
  Delete,
  Edit,
  Location,
  Plus,
  Refresh,
  School,
  Search,
  User
} from '@element-plus/icons-vue'
import { deleteRoomApi, getRoomPageApi } from '@/api/room'
import type { RoomInfo } from '@/schemas/room'
import { useUser } from '@/utils/user'
import { labStatusMeta } from '@/utils/dict'
import { fileUrl, formatOpenTime, seatCountText } from '@/utils/format'
import RoomFormDialog from '@/components/RoomFormDialog.vue'

/**
 * 自习室列表
 *
 * 学生：挑自习室 → 详情页选座；管理员：额外可以新建 / 编辑 / 删除。
 */
const router = useRouter()
const { isAdmin } = useUser()

const loading = ref(false)
const rooms = ref<RoomInfo[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(9)

const filters = reactive<{
  keyword: string
  capacity: number | undefined
  status: number | null
}>({
  keyword: '',
  capacity: undefined,
  status: null
})

const dialogVisible = ref(false)
const editingRoom = ref<RoomInfo | null>(null)

const statusOptions = [
  { label: '全部状态', value: null },
  { label: '开放中', value: 1 },
  { label: '已关闭', value: 0 }
]

const emptyText = computed(() =>
  filters.keyword || filters.status !== null || filters.capacity
    ? '没有符合条件的自习室，试试放宽筛选条件'
    : '还没有自习室数据'
)

async function loadRooms(): Promise<void> {
  loading.value = true
  try {
    const res = await getRoomPageApi({
      page: page.value,
      page_size: pageSize.value,
      keyword: filters.keyword.trim() || undefined,
      capacity: filters.capacity || undefined,
      status: filters.status === null ? undefined : filters.status
    })
    rooms.value = res.data?.data ?? []
    total.value = res.data?.total ?? 0
  } catch {
    rooms.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void loadRooms()
}

function reset(): void {
  filters.keyword = ''
  filters.capacity = undefined
  filters.status = null
  search()
}

function openCreate(): void {
  editingRoom.value = null
  dialogVisible.value = true
}

function openEdit(room: RoomInfo): void {
  editingRoom.value = room
  dialogVisible.value = true
}

function onSaved(): void {
  void loadRooms()
}

async function removeRoom(room: RoomInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除「${room.name}」吗？座位会一并删除，存在预约记录时无法删除。`,
      '删除自习室',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  try {
    const res = await deleteRoomApi(room.id)
    if (res.code === 200) {
      ElMessage.success('自习室已删除')
      if (rooms.value.length === 1 && page.value > 1) page.value -= 1
      void loadRooms()
    }
  } catch {
    // 409 等提示已由响应拦截器统一弹出
  }
}

function goDetail(room: RoomInfo): void {
  void router.push(`/manager/room/${room.id}`)
}

onMounted(loadRooms)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">{{ isAdmin ? '自习室管理' : '自习室座位' }}</h2>
        <div class="page-desc">
          {{ isAdmin ? '维护自习室与座位，控制开放状态' : '自习室按座位预约，先选时段再看空座' }}
        </div>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" @click="loadRooms">刷新</el-button>
        <el-button v-if="isAdmin" type="primary" :icon="Plus" @click="openCreate">
          新建自习室
        </el-button>
      </div>
    </div>

    <section class="panel filters">
      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          class="filters__keyword"
          placeholder="搜索名称或位置，如「三楼」「静音」"
          :prefix-icon="Search"
          clearable
          @keyup.enter="search"
          @clear="search"
        />
        <el-input-number
          v-model="filters.capacity"
          :min="0"
          :max="999"
          controls-position="right"
          placeholder="需要座位数"
          class="filters__number"
        />
        <el-select v-model="filters.status" class="filters__select" placeholder="状态">
          <el-option
            v-for="option in statusOptions"
            :key="String(option.value)"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
        <span class="muted filters__total">共 {{ total }} 间</span>
      </div>
    </section>

    <div v-loading="loading" class="room-grid">
      <article v-for="room in rooms" :key="room.id" class="room-card">
        <div class="room-card__cover" @click="goDetail(room)">
          <el-image v-if="room.img" :src="fileUrl(room.img)" fit="cover" class="room-card__img">
            <template #error>
              <div class="room-card__cover-fallback"><el-icon><School /></el-icon></div>
            </template>
          </el-image>
          <div v-else class="room-card__cover-fallback"><el-icon><School /></el-icon></div>
          <el-tag
            class="room-card__status"
            size="small"
            :type="labStatusMeta(room.status).type"
            effect="dark"
          >
            {{ labStatusMeta(room.status).label }}
          </el-tag>
        </div>

        <div class="room-card__body">
          <div class="room-card__title" :title="room.name" @click="goDetail(room)">
            {{ room.name }}
          </div>
          <div class="room-card__meta">
            <el-icon><Location /></el-icon>
            <span>{{ room.location || '位置未填' }}</span>
          </div>
          <div class="room-card__meta">
            <el-icon><Clock /></el-icon>
            <span>{{ formatOpenTime(room.open_time, room.close_time) }}</span>
            <span class="room-card__dot">·</span>
            <el-icon><User /></el-icon>
            <span>{{ seatCountText(room.capacity) }}</span>
          </div>
          <div class="room-card__desc">{{ room.description || '暂无简介' }}</div>
        </div>

        <div class="room-card__actions">
          <el-button type="primary" plain size="small" @click="goDetail(room)">
            查看座位
          </el-button>
          <template v-if="isAdmin">
            <el-button size="small" :icon="Edit" @click="openEdit(room)">编辑</el-button>
            <el-button size="small" type="danger" plain :icon="Delete" @click="removeRoom(room)">
              删除
            </el-button>
          </template>
        </div>
      </article>

      <el-empty v-if="!rooms.length && !loading" :description="emptyText" class="room-empty" />
    </div>

    <div v-if="total > pageSize" class="panel pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        background
        @current-change="loadRooms"
      />
    </div>

    <RoomFormDialog v-model="dialogVisible" :room="editingRoom" @saved="onSaved" />
  </div>
</template>

<style scoped>
.filters {
  padding: 14px 16px;
  margin-bottom: 16px;
}

.filters__keyword {
  width: 300px;
}

.filters__number {
  width: 150px;
}

.filters__select {
  width: 140px;
}

.filters__total {
  margin-left: auto;
  font-size: 13px;
}

.room-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 16px;
  min-height: 220px;
}

.room-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: transform 0.2s, box-shadow 0.2s;
}

.room-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 22px rgba(0, 0, 0, 0.1);
}

.room-card__cover {
  position: relative;
  height: 130px;
  overflow: hidden;
  cursor: pointer;
  background: linear-gradient(135deg, #e7f8ef, #c8ecd8);
}

.room-card__img {
  width: 100%;
  height: 100%;
}

.room-card__cover-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 46px;
  color: var(--el-color-success);
  opacity: 0.6;
  background: linear-gradient(135deg, #e7f8ef, #c8ecd8);
}

.room-card__status {
  position: absolute;
  top: 10px;
  right: 10px;
}

.room-card__body {
  flex: 1;
  padding: 14px 16px 8px;
}

.room-card__title {
  overflow: hidden;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  text-overflow: ellipsis;
  cursor: pointer;
}

.room-card__title:hover {
  color: var(--el-color-primary);
}

.room-card__meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}

.room-card__dot {
  margin: 0 2px;
}

.room-card__desc {
  display: -webkit-box;
  margin-top: 10px;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.7;
  color: #a8abb2;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.room-card__actions {
  display: flex;
  gap: 8px;
  padding: 10px 16px 14px;
  border-top: 1px solid #f5f7fa;
}

.room-empty {
  grid-column: 1 / -1;
}

@media (max-width: 768px) {
  .filters__keyword,
  .filters__number,
  .filters__select {
    width: 100%;
  }
}
</style>
