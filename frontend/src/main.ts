import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import '@/assets/css/global.css'

const app = createApp(App)

app.use(router)
// 组件库统一用中文：日期选择器、分页、空状态文案都是中文
app.use(ElementPlus, { locale: zhCn })

// 图标全量注册，模板里可以直接 <el-icon><OfficeBuilding /></el-icon>
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.mount('#app')
