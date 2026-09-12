import { createApp } from 'vue'
import naive from 'naive-ui'

import App from './App.vue'
import router from './router'
import { installQuasar } from './plugins/quasar'
import { bootstrapV3Theme } from './v3/composables/useV3Theme'
import './styles.css'

// 尽早应用主题，避免首帧闪烁
bootstrapV3Theme()

const app = createApp(App)
// Legacy Naive UI（迁移期允许与 Quasar 共存于 root）
app.use(naive)
// V3 Quasar（新页面唯一主组件框架）
installQuasar(app)
app.use(router)
app.mount('#app')
