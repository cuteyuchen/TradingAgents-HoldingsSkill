/** 截图事实时间按北京时间录入，不把上传时间或 OCR 推断当作人工确认。 */
export function observationTimeToIso(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) return null
  const local = value.length === 16 ? `${value}:00` : value
  const time = Date.parse(`${local}+08:00`)
  if (!Number.isFinite(time)) return null
  // Date.parse 会归一化少数非法日期；必须回算核对，不能静默把二月三十日变成三月。
  if (new Date(time + 8 * 60 * 60 * 1000).toISOString().slice(0, 19) !== local) return null
  return new Date(time).toISOString()
}
