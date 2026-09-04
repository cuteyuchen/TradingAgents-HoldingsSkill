import fs from 'node:fs'
import path from 'node:path'
import { test as base, expect, type Page, type TestInfo } from '@playwright/test'

export interface AcceptanceFacts {
  users: {
    a: { email: string; password: string }
    b: { email: string; password: string }
  }
  portfolios: {
    action: number
    states: number
    freshness: number
    user_b: number
  }
  runs: {
    action: number
    no_action: number
    blocked: number
    data_gap: number
    veto: number
    freshness: number
  }
  shadow: { account_id: number | null; observation_id: number | null }
  research: { backtest_id: number }
  governance: { active_version_id: number | null; proposal_id: number | null; approved_version_id: number | null }
  trade_date: string
  now_utc: string
}

const expectedHttpErrors = new WeakMap<Page, Set<number>>()

export const test = base.extend<{
  facts: AcceptanceFacts
  acceptancePage: Page
}>({
  facts: async ({}, use) => {
    const filename = process.env.PLAYWRIGHT_FACTS_FILE
    if (!filename) throw new Error('PLAYWRIGHT_FACTS_FILE is required for acceptance tests')
    const facts = JSON.parse(fs.readFileSync(filename, 'utf8')) as AcceptanceFacts
    await use(facts)
  },
  acceptancePage: async ({ page }, use, testInfo) => {
    const errors: string[] = []
    const allowedStatuses = new Set<number>()
    expectedHttpErrors.set(page, allowedStatuses)
    await page.addInitScript(() => {
      window.addEventListener('unhandledrejection', (event) => {
        console.error('__acceptance_unhandled_rejection__', String(event.reason))
      })
    })
    page.on('console', (message) => {
      if (message.type() !== 'error') return
      const match = message.text().match(/status of (\d+)/)
      const status = match ? Number(match[1]) : null
      if (status !== null && allowedStatuses.has(status) && message.text().startsWith('Failed to load resource')) return
      errors.push(`console.error: ${message.text()}`)
    })
    page.on('response', (response) => {
      if (response.status() === 401 && !allowedStatuses.has(401)) {
        errors.push(`http response 401: ${response.url()}`)
      }
    })
    page.on('pageerror', (error) => {
      errors.push(`pageerror: ${error.message}`)
    })

    await use(page)

    if (errors.length) {
      await attachText(testInfo, 'browser-console-errors.txt', errors.join('\n'))
      throw new Error(`浏览器存在未处理错误:\n${errors.join('\n')}`)
    }
    expectedHttpErrors.delete(page)
  },
})

export { expect }

export async function login(page: Page, user: AcceptanceFacts['users']['a']): Promise<void> {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: '进入投资驾驶舱' })).toBeVisible()
  await page.locator('input[type="email"]').fill(user.email)
  await page.locator('input[type="password"]').fill(user.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/dashboard(?:\?.*)?$/)
  await expect(page.getByRole('heading', { name: /今天/ })).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('header.topbar')).toBeVisible()
  await expect(page.locator('.shared-loading')).toHaveCount(0, { timeout: 20_000 })
}

export function allowExpectedHttpError(page: Page, status: number): void {
  expectedHttpErrors.get(page)?.add(status)
}

export async function selectPortfolio(page: Page, label: string): Promise<void> {
  const control = page.locator('.global-portfolio-select .n-base-selection')
  await expect(control).toBeVisible({ timeout: 20_000 })
  await control.click()
  const option = page.locator('.n-base-select-option').filter({ hasText: label }).last()
  await expect(option).toBeVisible()
  await option.click()
  await expect(control).toContainText(label)
}

export async function openPage(page: Page, route: string, heading: string | RegExp): Promise<void> {
  const aliases: Record<string, string> = {
    '/upload': '/holdings',
    '/reports': '/analysis',
    '/shadow': '/simulation',
    '/research': '/history',
    '/governance': '/settings',
    '/system': '/settings',
  }
  const path = route.split('?')[0]
  const expectedPath = aliases[path] || path
  await page.goto(route)
  await expect(page).toHaveURL(new RegExp(`${expectedPath.replaceAll('/', '\\/')}`))
  const headingLocator = typeof heading === 'string'
    ? page.getByRole('heading', { name: heading, exact: true })
    : page.getByRole('heading', { name: heading })
  await expect(headingLocator).toBeVisible()
}

export async function captureScreenshot(page: Page, name: string): Promise<string> {
  const root = path.resolve(process.env.PLAYWRIGHT_ARTIFACT_DIR || 'output/playwright/acceptance')
  const directory = path.join(root, 'screenshots')
  fs.mkdirSync(directory, { recursive: true })
  const filename = path.join(directory, `${name}-${page.viewportSize()?.width || 'desktop'}.png`)
  await page.screenshot({ path: filename, fullPage: true })
  return filename
}

export function validPngBytes(): Buffer {
  return Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  )
}

export async function attachText(testInfo: TestInfo, name: string, content: string): Promise<void> {
  await testInfo.attach(name, { body: Buffer.from(content, 'utf8'), contentType: 'text/plain' })
}
