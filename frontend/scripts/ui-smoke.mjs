import { chromium } from 'playwright'

const baseUrl = process.env.UI_BASE_URL || 'http://127.0.0.1:5173'

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage()

try {
  await page.goto(baseUrl, { waitUntil: 'networkidle' })

  const mustNotExistTexts = ['BBDuck Server', '批量拖拽上传']
  for (const text of mustNotExistTexts) {
    const count = await page.getByText(text, { exact: true }).count()
    if (count > 0) {
      throw new Error(`Unexpected legacy element still visible: ${text}`)
    }
  }

  const mustExistTexts = ['下载压缩图', '下载原图']
  for (const text of mustExistTexts) {
    const count = await page.getByRole('link', { name: text }).count()
    if (count === 0) {
      throw new Error(`Expected download link missing: ${text}`)
    }
  }

  const legacyCompressionEffectCount = await page.getByText('压缩效果', { exact: true }).count()
  if (legacyCompressionEffectCount > 0) {
    throw new Error('Legacy standalone compression effect card still visible')
  }

  const queueCountBox = await page.getByText(/\d+ 个队列项/).boundingBox()
  if (!queueCountBox) {
    throw new Error('Expected packaged evaluation queue count before real uploads')
  }
  const preUploadBatchZipButtonCount = await page.getByRole('button', { name: '批量下载压缩图' }).count()
  if (preUploadBatchZipButtonCount > 0) {
    throw new Error('Batch ZIP button should only appear after real uploads')
  }

  const demoCard = page.getByRole('button', { name: /00001\.png/ })
  const demoQueueItemCount = await demoCard.count()
  if (demoQueueItemCount === 0) {
    throw new Error('Expected packaged evaluation queue item missing before real uploads')
  }

  if (await page.getByText('example.png', { exact: true }).count()) {
    throw new Error('example.png should not be visible before real uploads when evaluation images are available')
  }

  const demoCardBox = await demoCard.boundingBox()
  if (!demoCardBox || demoCardBox.width < 900 || demoCardBox.height > 130) {
    throw new Error(`Expected wide and short queue card, got ${JSON.stringify(demoCardBox)}`)
  }

  await page.getByText('压缩前', { exact: true }).waitFor({ timeout: 5000 })
  await demoCard.click()
  const collapsedCompareLabelCount = await page.getByText('压缩前', { exact: true }).count()
  if (collapsedCompareLabelCount > 0) {
    throw new Error('Expected preview to collapse inside queue item')
  }
  await demoCard.click()
  await page.getByText('压缩前', { exact: true }).waitFor({ timeout: 5000 })

  const originalLinkBox = await page.getByRole('link', { name: '下载原图' }).boundingBox()
  const compressedLinkBox = await page.getByRole('link', { name: '下载压缩图' }).boundingBox()
  if (!originalLinkBox || !compressedLinkBox || originalLinkBox.x >= compressedLinkBox.x) {
    throw new Error(`Expected 下载原图 on the left and 下载压缩图 on the right, got ${JSON.stringify({ originalLinkBox, compressedLinkBox })}`)
  }

  const pngBuffer = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGNkaGAgCTCRpnxUw6iGoaQBALsfAKDg6Y6zAAAAAElFTkSuQmCC',
    'base64',
  )

  await page.setInputFiles('input[type="file"]', [
    {
      name: 'alpha.png',
      mimeType: 'image/png',
      buffer: pngBuffer,
    },
    {
      name: 'beta.png',
      mimeType: 'image/png',
      buffer: pngBuffer,
    },
  ])

  await page.getByText('alpha.png', { exact: true }).waitFor({ timeout: 10000 })
  await page.getByRole('button', { name: '下载全部压缩图' }).waitFor({ timeout: 10000 })
  const batchDownloadCount = await page.getByRole('button', { name: '下载全部压缩图' }).count()
  if (batchDownloadCount === 0) {
    throw new Error('Expected batch download button missing after uploads')
  }

  console.log('UI smoke test passed')
} finally {
  await browser.close()
}
