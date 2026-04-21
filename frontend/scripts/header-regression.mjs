import assert from 'node:assert/strict'
import { chromium } from 'playwright'

async function run() {
  const browser = await chromium.launch({ headless: true })

  try {
    const page = await browser.newPage()
    await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
    await page.waitForSelector('.page-shell')

    const topbar = page.locator('.page-topbar')
    await topbar.waitFor({ state: 'visible', timeout: 5000 })

    const titleText = await topbar.locator('.page-topbar-title').innerText()
    assert.match(titleText, /BB鸭 给图片减减肥 让画质顶呱呱/, '顶部 header 应展示品牌标题')

    const subtitleCount = await topbar.locator('.page-topbar-subtitle').count()
    assert.equal(subtitleCount, 0, '顶部 header 不应再显示副标题文案')

    const topbarRadius = await topbar.evaluate((node) => getComputedStyle(node).borderRadius)
    assert.ok(topbarRadius === '0px' || topbarRadius === '0px 0px 0px 0px', '顶部 header 应改为矩形样式')

    const logoCount = await topbar.locator('.page-topbar-logo').count()
    assert.equal(logoCount, 1, '顶部 header 应展示 logo')

    const settingsButton = topbar.locator('.page-topbar-settings')
    await settingsButton.waitFor({ state: 'visible', timeout: 5000 })

    const heroHeadingCount = await page.locator('.hero-block h1').count()
    assert.equal(heroHeadingCount, 0, '品牌标题应移动到顶部 header，而不是继续留在 hero 区')

    const uploadZone = page.locator('.upload-dropzone')
    await uploadZone.waitFor({ state: 'visible', timeout: 5000 })
    const uploadZoneText = await uploadZone.innerText()
    assert.match(uploadZoneText, /点击或拖拽到当前区域开始压缩/, 'header 下方应有独立上传区域提示文案')

    const defaultLogPanelCount = await page.locator('.queue-log-panel').count()
    assert.equal(defaultLogPanelCount, 0, '默认不勾选时不应显示压缩日志')

    await settingsButton.click()
    const settingsModal = page.locator('.settings-modal')
    await settingsModal.waitFor({ state: 'visible', timeout: 5000 })
    const checkbox = settingsModal.locator('input[type="checkbox"]')
    const checkboxCheckedBefore = await checkbox.isChecked()
    assert.equal(checkboxCheckedBefore, false, '显示压缩日志默认应为未勾选')

    const queueStrategyInput = settingsModal.locator('input[type="number"]')
    await queueStrategyInput.waitFor({ state: 'visible', timeout: 5000 })
    assert.equal(await queueStrategyInput.inputValue(), '2', '队列策略默认值应为 2')
    assert.equal(await queueStrategyInput.getAttribute('min'), '1', '队列策略最小值应为 1')
    assert.equal(await queueStrategyInput.getAttribute('max'), '6', '队列策略最大值应为 6')
    await queueStrategyInput.fill('4')
    await queueStrategyInput.blur()
    assert.equal(await queueStrategyInput.inputValue(), '4', '队列策略应允许修改到 1~6 范围内的值')

    await checkbox.check()
    await page.waitForTimeout(150)
    const logPanelCountAfterCheck = await page.locator('.queue-log-panel').count()
    assert.ok(logPanelCountAfterCheck >= 1, '勾选后应显示压缩日志')

    const footerStrategyCount = await page.locator('.page-footer-settings .parallelism-control').count()
    assert.equal(footerStrategyCount, 0, '队列策略不应继续显示在页面底部，而应移入右上角齿轮面板')

    await page.reload({ waitUntil: 'networkidle' })
    const persistedLogPanelCount = await page.locator('.queue-log-panel').count()
    assert.ok(persistedLogPanelCount >= 1, 'localStorage 应记住日志显示开关')

    await settingsButton.click()
    await settingsModal.waitFor({ state: 'visible', timeout: 5000 })
    const persistedStrategyValue = await settingsModal.locator('input[type="number"]').inputValue()
    assert.equal(persistedStrategyValue, '4', '队列策略修改值刷新后也应保留')

    const persistedValue = await page.evaluate(() => ({
      showLogs: window.localStorage.getItem('bbduck-show-compression-logs'),
      queueConcurrency: window.localStorage.getItem('bbduck-queue-concurrency'),
    }))
    assert.equal(persistedValue.showLogs, 'true', '日志显示开关应写入 localStorage')
    assert.equal(persistedValue.queueConcurrency, '4', '队列策略也应写入 localStorage')

    const fixedProgress = page.locator('.page-bottom-progress')
    await fixedProgress.waitFor({ state: 'visible', timeout: 5000 })
    const progressPosition = await fixedProgress.evaluate((node) => getComputedStyle(node).position)
    assert.equal(progressPosition, 'fixed', '进度条区域应固定在页面底部')

    const shellPaddingBottom = await page.locator('.page-shell').evaluate((node) => parseFloat(getComputedStyle(node).paddingBottom))
    assert.ok(shellPaddingBottom >= 120, '主文档流应预留足够底部空间，避免 fixed 进度条遮挡队列区')

    const progressTitle = await fixedProgress.locator('.page-bottom-progress-head strong').innerText()
    assert.match(progressTitle, /压缩完成/, '完成状态下底部进度区应显示“压缩完成”')

    const progressSummary = await fixedProgress.locator('.page-bottom-progress-head span').innerText()
    assert.match(progressSummary, /共压缩\s+1\s+个文件,\s*节省空间\s+\d+%\s*\([\d.]+(?:KB|MB)\s*→\s*[\d.]+(?:KB|MB)\)/, '完成状态下应显示节省空间汇总文案')

    console.log('header regression: ok')
  } finally {
    await browser.close()
  }
}

run().catch((error) => {
  console.error(error)
  process.exit(1)
})
