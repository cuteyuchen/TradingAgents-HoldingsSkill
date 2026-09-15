/**
 * 扩展 vue-router RouteMeta，声明 UI 系统边界。
 */
import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    /** legacy = Naive UI 页面；v3 = Quasar + V3 Design System */
    uiSystem?: 'legacy' | 'v3'
    /** 公开路由（登录等） */
    public?: boolean
    /** Foundation showcase，仅 development / flag 开启 */
    foundation?: boolean
  }
}

export {}
