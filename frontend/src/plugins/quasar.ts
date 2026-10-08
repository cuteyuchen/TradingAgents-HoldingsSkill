/**
 * Quasar 安装插件：只注册真正需要的插件，避免全量 import。
 * 组件通过 @quasar/vite-plugin 自动按需注册。
 */
import type { App } from 'vue'
import { Dark, Dialog, Loading, Notify, Quasar } from 'quasar'

// 仅引入 Quasar 核心 CSS（按需组件由 vite-plugin 处理）
import 'quasar/src/css/index.sass'
import '../v3/styles/v3-quasar-overrides.scss'
import '../v3/styles/v3-tokens.css'
import '../v3/styles/v3-base.css'

export function installQuasar(app: App): void {
  app.use(Quasar, {
    plugins: { Notify, Dialog, Loading, Dark },
    config: {
      notify: {
        position: 'top-right',
        timeout: 2800,
        classes: 'v3-notify',
      },
      loading: {
        /* 默认不阻塞整页，页面级 loading 优先 skeleton */
        delay: 200,
      },
    },
  })
}
