<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { createLabApi, updateLabApi } from '@/api/lab'
import type { LabInfo } from '@/schemas/lab'

/**
 * 新增 / 编辑实验室弹窗（管理员）
 *
 * 只做参数收集与校验；名称查重、开放时间合法性等规则由后端 service 兜底
 * （前端校验是为了少一次往返，不是唯一防线）。
 */
const props = defineProps<{
  modelValue: boolean
  lab?: LabInfo | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [lab: LabInfo]
}>()

interface LabFormModel {
  name: string
  location: string
  maxpeople: number
  open_time: string
  close_time: string
  description: string
  img: string
  status: number
}

const formRef = ref<FormInstance>()
const saving = ref(false)

const form = reactive<LabFormModel>({
  name: '',
  location: '',
  maxpeople: 20,
  open_time: '08:00',
  close_time: '22:00',
  description: '',
  img: '',
  status: 1
})

const isEdit = computed(() => !!props.lab?.id)
const title = computed(() => (isEdit.value ? '编辑实验室' : '新增实验室'))

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

const rules: FormRules<LabFormModel> = {
  name: [
    { required: true, message: '请输入实验室名称', trigger: 'blur' },
    { max: 50, message: '名称最长 50 个字符', trigger: 'blur' }
  ],
  maxpeople: [{ required: true, message: '请输入最大容纳人数', trigger: 'change' }],
  location: [{ max: 100, message: '位置最长 100 个字符', trigger: 'blur' }]
}

/** 打开弹窗时把数据填进去：新增用默认值，编辑回填当前值 */
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const lab = props.lab
    form.name = lab?.name ?? ''
    form.location = lab?.location ?? ''
    form.maxpeople = lab?.maxpeople ?? 20
    form.open_time = lab?.open_time ? lab.open_time.slice(0, 5) : '08:00'
    form.close_time = lab?.close_time ? lab.close_time.slice(0, 5) : '22:00'
    form.description = lab?.description ?? ''
    form.img = lab?.img ?? ''
    form.status = lab?.status ?? 1
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
      maxpeople: form.maxpeople,
      open_time: form.open_time,
      close_time: form.close_time,
      description: form.description.trim() || null,
      img: form.img.trim() || null,
      status: form.status
    }
    const res = isEdit.value
      ? await updateLabApi(props.lab!.id, payload)
      : await createLabApi(payload)
    if (res.code === 200) {
      ElMessage.success(isEdit.value ? '实验室已更新' : '实验室已创建')
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
      <el-form-item label="实验室名称" prop="name">
        <el-input v-model="form.name" maxlength="50" placeholder="例如 物理实验室（A101）" />
      </el-form-item>
      <el-form-item label="位置" prop="location">
        <el-input v-model="form.location" maxlength="100" placeholder="例如 实验楼A座101" />
      </el-form-item>
      <el-form-item label="容纳人数" prop="maxpeople">
        <el-input-number v-model="form.maxpeople" :min="1" :max="999" controls-position="right" />
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
          placeholder="实验内容、注意事项等"
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
</style>
