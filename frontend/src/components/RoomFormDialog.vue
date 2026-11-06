<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { createRoomApi, updateRoomApi } from '@/api/room'
import type { RoomInfo } from '@/schemas/room'

/** 新增 / 编辑自习室弹窗（管理员） */
const props = defineProps<{
  modelValue: boolean
  room?: RoomInfo | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [room: RoomInfo]
}>()

interface RoomFormModel {
  name: string
  location: string
  capacity: number
  open_time: string
  close_time: string
  description: string
  img: string
  status: number
}

const formRef = ref<FormInstance>()
const saving = ref(false)

const form = reactive<RoomFormModel>({
  name: '',
  location: '',
  capacity: 60,
  open_time: '08:00',
  close_time: '22:00',
  description: '',
  img: '',
  status: 1
})

const isEdit = computed(() => !!props.room?.id)
const title = computed(() => (isEdit.value ? '编辑自习室' : '新增自习室'))

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

const rules: FormRules<RoomFormModel> = {
  name: [
    { required: true, message: '请输入自习室名称', trigger: 'blur' },
    { max: 50, message: '名称最长 50 个字符', trigger: 'blur' }
  ],
  capacity: [{ required: true, message: '请输入座位数上限', trigger: 'change' }]
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const room = props.room
    form.name = room?.name ?? ''
    form.location = room?.location ?? ''
    form.capacity = room?.capacity ?? 60
    form.open_time = room?.open_time ? room.open_time.slice(0, 5) : '08:00'
    form.close_time = room?.close_time ? room.close_time.slice(0, 5) : '22:00'
    form.description = room?.description ?? ''
    form.img = room?.img ?? ''
    form.status = room?.status ?? 1
    formRef.value?.clearValidate()
  }
)

async function submit(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  if (form.open_time === form.close_time) {
    ElMessage.warning('开放开始时间与结束时间不能相同')
    return
  }

  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      location: form.location.trim() || null,
      capacity: form.capacity,
      open_time: form.open_time,
      close_time: form.close_time,
      description: form.description.trim() || null,
      img: form.img.trim() || null,
      status: form.status
    }
    const res = isEdit.value
      ? await updateRoomApi(props.room!.id, payload)
      : await createRoomApi(payload)
    if (res.code === 200) {
      ElMessage.success(isEdit.value ? '自习室已更新' : '自习室已创建')
      emit('saved', res.data)
      visible.value = false
    }
  } catch {
    // 同名 409 等提示已由响应拦截器统一弹出
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="560px" append-to-body destroy-on-close>
    <el-form ref="formRef" :model="form" :rules="rules" label-width="96px">
      <el-form-item label="自习室名称" prop="name">
        <el-input v-model="form.name" maxlength="50" placeholder="例如 三楼自主学习区" />
      </el-form-item>
      <el-form-item label="位置">
        <el-input v-model="form.location" maxlength="100" placeholder="例如 图书馆3F东侧" />
      </el-form-item>
      <el-form-item label="座位数上限" prop="capacity">
        <el-input-number v-model="form.capacity" :min="1" :max="999" controls-position="right" />
        <span class="form-hint">新增座位时不能超过这个数量</span>
      </el-form-item>
      <el-form-item label="开放时间">
        <div class="time-range">
          <el-time-select
            v-model="form.open_time"
            start="06:00"
            end="23:30"
            step="00:30"
            placeholder="开始"
            class="time-range__item"
          />
          <span class="time-range__sep">至</span>
          <el-time-select
            v-model="form.close_time"
            start="06:00"
            end="23:30"
            step="00:30"
            placeholder="结束"
            class="time-range__item"
          />
        </div>
      </el-form-item>
      <el-form-item label="状态">
        <el-switch
          v-model="form.status"
          :active-value="1"
          :inactive-value="0"
          active-text="开放"
          inactive-text="关闭"
          inline-prompt
        />
      </el-form-item>
      <el-form-item label="封面图">
        <el-input v-model="form.img" maxlength="200" placeholder="图片地址，可留空" />
      </el-form-item>
      <el-form-item label="简介">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          maxlength="2000"
          show-word-limit
          placeholder="区域特点、注意事项等"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">
        {{ isEdit ? '保存' : '创建' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.time-range {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.time-range__item {
  width: 130px;
}

.time-range__sep {
  color: #909399;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}
</style>
