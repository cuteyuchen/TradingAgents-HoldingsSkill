/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

interface ImportMetaEnv {
  /** 构建时读取根目录 VERSION；与 API 和镜像版本一致。 */
  readonly VITE_APP_VERSION: string
  /** Foundation showcase 开关；DEV 下默认开启 */
  readonly VITE_ENABLE_V3_FOUNDATION?: string
  readonly VITE_BACKEND_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
