/**
 * V3 Notify 抽象：业务代码不要直接 $q.notify()
 */
import { Notify } from 'quasar'

export type V3NotifyTone = 'info' | 'positive' | 'warning' | 'negative'

export interface V3NotifyOptions {
  message: string
  caption?: string
  timeout?: number
  tone?: V3NotifyTone
}

export function useV3Notify() {
  function notify(options: V3NotifyOptions): void {
    const tone = options.tone ?? 'info'
    Notify.create({
      message: options.message,
      caption: options.caption,
      timeout: options.timeout ?? 2800,
      color: tone === 'positive' ? 'positive' : tone === 'warning' ? 'warning' : tone === 'negative' ? 'negative' : 'info',
      icon: undefined,
      classes: 'v3-notify',
    })
  }

  /** 成功提示 — 语义 success，不绑定 A 股涨跌 */
  const success = (message: string, caption?: string) => notify({ message, caption, tone: 'positive' })
  const info = (message: string, caption?: string) => notify({ message, caption, tone: 'info' })
  const warning = (message: string, caption?: string) => notify({ message, caption, tone: 'warning' })
  /** 失败提示 — 语义 danger，不绑定 A 股涨跌 */
  const error = (message: string, caption?: string) => notify({ message, caption, tone: 'negative' })

  return { notify, success, info, warning, error }
}
