/**
 * V3 Dialog 抽象：小确认用 QDialog，大型详情用 V3DetailDrawer
 */
import { Dialog } from 'quasar'

export interface V3ConfirmOptions {
  title: string
  message: string
  okLabel?: string
  cancelLabel?: string
  persistent?: boolean
  html?: boolean
}

export function useV3Dialog() {
  async function confirm(options: V3ConfirmOptions): Promise<boolean> {
    return new Promise((resolve) => {
      Dialog.create({
        title: options.title,
        message: options.message,
        html: options.html ?? false,
        persistent: options.persistent ?? false,
        ok: {
          label: options.okLabel ?? '确认',
          flat: true,
          color: 'primary',
        },
        cancel: {
          label: options.cancelLabel ?? '取消',
          flat: true,
          color: 'grey-7',
        },
      })
        .onOk(() => resolve(true))
        .onCancel(() => resolve(false))
        .onDismiss(() => resolve(false))
    })
  }

  function alert(title: string, message: string): void {
    Dialog.create({
      title,
      message,
      ok: { label: '知道了', flat: true, color: 'primary' },
    })
  }

  return { confirm, alert }
}
