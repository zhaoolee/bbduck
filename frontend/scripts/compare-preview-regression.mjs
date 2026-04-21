import assert from 'node:assert/strict'
import { chromium } from 'playwright'

async function run() {
  const browser = await chromium.launch({ headless: true })

  try {
    const healthyPage = await browser.newPage()
    await healthyPage.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
    await healthyPage.waitForSelector('.compare-stage .base-image')
    const initialImageCount = await healthyPage.locator('.compare-stage img').count()
    assert.ok(initialImageCount >= 2, '默认对比区应至少渲染两张图片')
    await healthyPage.close()

    const brokenPage = await browser.newPage()
    await brokenPage.route('**/demo-after.png', async (route) => {
      await route.abort('failed')
    })
    await brokenPage.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
    await brokenPage.waitForSelector('.compare-stage-status.is-error', { timeout: 5000 })
    const errorText = await brokenPage.locator('.compare-stage-status.is-error').innerText()
    assert.match(errorText, /预览加载失败/, '预览失败时应给出明确提示，而不是空白区域')
    await brokenPage.close()

    console.log('compare-preview regression: ok')
  } finally {
    await browser.close()
  }
}

run().catch((error) => {
  console.error(error)
  process.exit(1)
})
