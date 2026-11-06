<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Clock,
  Delete,
  Edit,
  Location,
  OfficeBuilding,
  Plus,
  Refresh,
  Search,
  User
} from '@element-plus/icons-vue'
import { deleteLabApi, getLabPageApi } from '@/api/lab'
import type { LabInfo } from '@/schemas/lab'
import { useUser } from '@/utils/user'
import { labStatusMeta } from '@/utils/dict'
import { capacityText, fileUrl, formatOpenTime } from '@/utils/format'
import LabFormDialog from '@/components/LabFormDialog.vue'

/**
 * 实验室列表
 *
 * 学生看到的是「浏览 + 去预约」，管理员多出「新建 / 编辑 / 删除」。
 * 列表接口分页（page_size=9 正好铺满 3 × 3）。
 */
const router = useRouter()
const { isAdmin } = useUser()

const loading = ref(false)
const labs = ref<LabInfo[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(9)

const filters = reactive<{ keyword: string; peopleNum: number | undefined; status: number | null }>({
  keyword: '',
  peopleNum: undefined,
  status: null
})

const dialogVisible = ref(false)
const editingLab = ref<LabInfo | null>(null)

const statusOptions = [
  { label: '全部状态', value: null },
  { label: '开放中', value: 1 },
  { label: '已关闭', value: 0 }
]

const emptyText = computed(() =>
  filters.keyword || filters.status !== null || filters.peopleNum
    ? '没有符合条件的实验室，试试放宽筛选条件'
    : '还没有实验室数据'
)

async function loadLabs(): Promise<void> {
  loading.value = true
  try {
    const res = await getLabPageApi({
      page: page.value,
      page_size: pageSize.value,
      keyword: filters.keyword.trim() || undefined,
      people_num: filters.peopleNum || undefined,
      status: filters.status === null ? undefined : filters.status
    })
    labs.value = res.data?.data ?? []
    total.value = res.data?.total ?? 0
  } catch {
    labs.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function search(): void {
  page.value = 1
  void loadLabs()
}

function reset(): void {
  filters.keyword = ''
  filters.peopleNum = undefined
  filters.status = null
  search()
}

function openCreate(): void {
  editingLab.value = null
  dialogVisible.value = true
}

function openEdit(lab: LabInfo): void {
  editingLab.value = lab
  dialogVisible.value = true
}

function onSaved(): void {
  void loadLabs()
}

async function removeLab(lab: LabInfo): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除「${lab.name}」吗？实验室下的设备会一并删除，存在预约记录时无法删除。`,
      '删除实验室',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }

  try {
    const res = await deleteLabApi(lab.id)
    if (res.code === 200) {
      ElMessage.success('实验室已删除')
      if (labs.value.length === 1 && page.value > 1) page.value -= 1
      void loadLabs()
    }
  } catch {
    // 409（存在预约）等提示已由响应拦截器统一弹出
  }
}

function goDetail(lab: LabInfo): void {
  void router.push(`/manager/lab/${lab.id}`)
}

onMounted(loadLabs)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">{{ isAdmin ? '实验室管理' : '实验室预约' }}</h2>
        <div class="page-desc">
          {{ isAdmin ? '维护实验室信息、设备与开放状态' : '挑一间开放中的实验室，按小时预约空闲时段' }}
        </div>
      </div>
      <div class="toolbar">
        <el-button :icon="Refresh" :loading="loading" @click="loadLabs">刷新</el-button>
        <el-button v-if="isAdmin" type="primary" :icon="Plus" @click="openCreate">
          新建实验室
        </el-button>
      </div>
    </div>

    <!-- 筛选 -->
    <section class="panel filters">
      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          class="filters__keyword"
          placeholder="搜索名称或位置，如「物理」「A101」"
          :prefix-icon="Search"
          clearable
          @keyup.enter="search"
          @clear="search"
        />
        <el-input-number
          v-model="filters.peopleNum"
          :min="0"
          :max="999"
          controls-position="right"
          placeholder="需要容纳人数"
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

    <!-- 列表 -->
    <div v-loading="loading" class="lab-grid">
      <article v-for="lab in labs" :key="lab.id" class="lab-card">
        <div class="lab-card__cover" @click="goDetail(lab)">
          <el-image
            v-if="lab.img"
            :src="fileUrl(lab.img)"
            fit="cover"
            class="lab-card__img"
          >
            <template #error>
              <div class="lab-card__cover-fallback">
                <el-icon><OfficeBuilding /></el-icon>
              </div>
            </template>
          </el-image>
          <div v-else class="lab-card__cover-fallback">
            <el-icon><OfficeBuilding /></el-icon>
          </div>
          <el-tag
            class="lab-card__status"
            size="small"
            :type="labStatusMeta(lab.status).type"
            effect="dark"
          >
            {{ labStatusMeta(lab.status).label }}
          </el-tag>
        </div>

        <div class="lab-card__body">
          <div class="lab-card__title" :title="lab.name" @click="goDetail(lab)">
            {{ lab.name }}
          </div>
          <div class="lab-card__meta">
            <el-icon><Location /></el-icon>
            <span>{{ lab.location || '位置未填' }}</span>
          </div>
          <div class="lab-card__meta">
            <el-icon><Clock /></el-icon>
            <span>{{ formatOpenTime(lab.open_time, lab.close_time) }}</span>
            <span class="lab-card__dot">·</span>
            <el-icon><User /></el-icon>
            <span>{{ capacityText(lab.maxpeople) }}</span>
          </div>
          <div class="lab-card__desc">{{ lab.description || '暂无简介' }}</div>
        </div>

        <div class="lab-card__actions">
          <el-button type="primary" plain size="small" @click="goDetail(lab)">
            查看详情
          </el-button>
          <template v-if="isAdmin">
            <el-button size="small" :icon="Edit" @click="openEdit(lab)">编辑</el-button>
            <el-button size="small" type="danger" plain :icon="Delete" @click="removeLab(lab)">
              删除
            </el-button>
          </template>
        </div>
      </article>

      <el-empty v-if="!labs.length && !loading" :description="emptyText" class="lab-empty" />
    </div>

    <div v-if="total > pageSize" class="panel pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        background
        @current-change="loadLabs"
      />
    </div>

    <LabFormDialog v-model="dialogVisible" :lab="editingLab" @saved="onSaved" />
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

.lab-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 16px;
  min-height: 220px;
}

.lab-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: transform 0.2s, box-shadow 0.2s;
}

.lab-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 22px rgba(0, 0, 0, 0.1);
}

.lab-card__cover {
  position: relative;
  height: 130px;
  overflow: hidden;
  cursor: pointer;
  background: linear-gradient(135deg, #e8f0ff, #cddcff);
}

.lab-card__img {
  width: 100%;
  height: 100%;
}

.lab-card__cover-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 46px;
  color: var(--el-color-primary);
  opacity: 0.55;
  background: linear-gradient(135deg, #e8f0ff, #cddcff);
}

.lab-card__status {
  position: absolute;
  top: 10px;
  right: 10px;
}

.lab-card__body {
  flex: 1;
  padding: 14px 16px 8px;
}

.lab-card__title {
  overflow: hidden;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  text-overflow: ellipsis;
  cursor: pointer;
}

.lab-card__title:hover {
  color: var(--el-color-primary);
}

.lab-card__meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}

.lab-card__dot {
  margin: 0 2px;
}

.lab-card__desc {
  display: -webkit-box;
  margin-top: 10px;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.7;
  color: #a8abb2;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.lab-card__actions {
  display: flex;
  gap: 8px;
  padding: 10px 16px 14px;
  border-top: 1px solid #f5f7fa;
}

.lab-empty {
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
