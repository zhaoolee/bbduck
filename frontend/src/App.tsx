import { ChangeEvent, DragEvent, PointerEvent, useEffect, useMemo, useRef, useState } from 'react'

type CompressionItem = {
  file_name: string
  original_size: number
  compressed_size: number
  original_url: string
  compressed_url: string
  mime_type: string
  status: 'completed' | 'skipped'
  algorithm: string
  metrics: {
    compression_ratio: number
    ssim: number
    psnr: number
  }
}

type QueueStatus = 'queued' | 'uploading' | 'processing' | 'completed' | 'skipped' | 'failed'

type QueueItem = {
  id: string
  fileName: string
  status: QueueStatus
  progress: number
  detail: string
  sourceFile?: File
  result?: CompressionItem
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

function getVisualQualityLabel(ssim: number, psnr: number) {
  if (ssim >= 0.995 || psnr >= 50) {
    return {
      title: '几乎看不出变化',
      detail: '肉眼基本感觉不到压缩痕迹',
    }
  }

  if (ssim >= 0.985 || psnr >= 40) {
    return {
      title: '画质非常接近原图',
      detail: '大多数场景下几乎不会注意到差异',
    }
  }

  if (ssim >= 0.96 || psnr >= 32) {
    return {
      title: '轻微变化，但整体清晰',
      detail: '放大细看可能有差别，日常使用通常没问题',
    }
  }

  return {
    title: '压缩更激进',
    detail: '体积更小，但细节损失会更明显',
  }
}

function getCompressionSummary(ratio: number) {
  if (ratio >= 70) return '节省很多空间'
  if (ratio >= 45) return '明显变小'
  if (ratio >= 20) return '体积有明显下降'
  return '体积略有下降'
}

const defaultDemoItem: CompressionItem = {
  file_name: 'example.png',
  original_size: 10_309_200,
  compressed_size: 6_043_598,
  original_url: '/demo-before.png',
  compressed_url: '/demo-after.png',
  mime_type: 'image/png',
  status: 'completed',
  algorithm: 'example-cache-demo',
  metrics: {
    compression_ratio: 41.38,
    ssim: 1,
    psnr: 99,
  },
}

function buildDownloadName(fileName: string, variant: 'original' | 'compressed') {
  const dotIndex = fileName.lastIndexOf('.')
  if (dotIndex <= 0) {
    return variant === 'compressed' ? `${fileName}-compressed` : `${fileName}-original`
  }

  const stem = fileName.slice(0, dotIndex)
  const ext = fileName.slice(dotIndex)
  return variant === 'compressed' ? `${stem}-compressed${ext}` : `${stem}-original${ext}`
}

function getStoredFileName(fileUrl: string) {
  try {
    const parsed = new URL(fileUrl, window.location.origin)
    return parsed.pathname.split('/').pop() ?? ''
  } catch {
    return ''
  }
}

const queueStatusLabel: Record<QueueStatus, string> = {
  queued: '排队中',
  uploading: '上传中',
  processing: '服务端压缩中',
  completed: '已完成',
  skipped: '已最小，无需压缩',
  failed: '上传异常',
}

function createQueueId() {
  if (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function buildQueueItem(file: File, index: number): QueueItem {
  return {
    id: `${file.name}-${file.size}-${index}-${createQueueId()}`,
    fileName: file.name,
    status: 'queued',
    progress: 0,
    detail: '等待压缩开始',
    sourceFile: file,
  }
}

const defaultDemoQueueItem: QueueItem = {
  id: 'demo-queue-item',
  fileName: defaultDemoItem.file_name,
  status: 'completed',
  progress: 100,
  detail: '默认示例图，可直接查看压缩前后效果',
  result: defaultDemoItem,
}

function isImageFile(file: File) {
  if (file.type.startsWith('image/')) return true
  const suffix = file.name.split('.').pop()?.toLowerCase()
  return ['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(suffix ?? '')
}

function extractImageFiles(fileList: FileList | File[]) {
  return Array.from(fileList).filter(isImageFile)
}

function hasFileTransfer(event: DragEvent<HTMLElement> | globalThis.DragEvent) {
  const dataTransfer = event.dataTransfer
  if (!dataTransfer) return false
  if (dataTransfer.files && dataTransfer.files.length > 0) return true
  if (dataTransfer.items && dataTransfer.items.length > 0) {
    return Array.from(dataTransfer.items).some((item) => item.kind === 'file')
  }
  return Array.from(dataTransfer.types ?? []).includes('Files')
}

function clampParallelism(value: number) {
  return Math.min(10, Math.max(1, value))
}

function compressSingleFile(file: File, onProgress: (patch: Partial<QueueItem>) => void): Promise<CompressionItem> {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('files', file)

    const xhr = new XMLHttpRequest()
    let processingTimer: number | undefined
    let uploadCompleted = false

    const stopTimer = () => {
      if (processingTimer !== undefined) {
        window.clearInterval(processingTimer)
      }
    }

    xhr.open('POST', '/api/compress')
    xhr.responseType = 'json'

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return
      const percent = Math.min(35, Math.round((event.loaded / event.total) * 35))
      onProgress({
        status: 'uploading',
        progress: percent,
        detail: `正在上传 ${percent}%`,
      })
    }

    xhr.upload.onload = () => {
      uploadCompleted = true
      let pseudoProgress = 42
      onProgress({
        status: 'processing',
        progress: pseudoProgress,
        detail: '文件已上传，正在服务端压缩…',
      })
      processingTimer = window.setInterval(() => {
        pseudoProgress = Math.min(92, pseudoProgress + 4)
        onProgress({
          status: 'processing',
          progress: pseudoProgress,
          detail: '正在尝试压缩策略并评估画质…',
        })
      }, 320)
    }

    xhr.onerror = () => {
      stopTimer()
      reject(new Error('网络异常，上传失败'))
    }

    xhr.onload = () => {
      stopTimer()
      const payload = xhr.response ?? JSON.parse(xhr.responseText || '{}')
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(payload?.detail ?? '上传失败'))
        return
      }
      const item = payload?.items?.[0]
      if (!item) {
        reject(new Error('服务端未返回压缩结果'))
        return
      }
      onProgress({
        status: item.status === 'skipped' ? 'skipped' : 'completed',
        progress: 100,
        detail: item.status === 'skipped' ? '已最小，无需压缩，可直接查看原图' : uploadCompleted ? '压缩完成，可以查看前后对比' : '上传完成',
        result: item,
      })
      resolve(item)
    }

    xhr.send(formData)
  })
}

function ComparePreview({
  originalUrl,
  compressedUrl,
  sliderValue,
  isPending,
  queueId,
  onPointerEnter,
  onPointerMove,
}: {
  originalUrl: string
  compressedUrl: string
  sliderValue: number
  isPending: boolean
  queueId: string
  onPointerEnter: (queueId: string, event: PointerEvent<HTMLDivElement>) => void
  onPointerMove: (queueId: string, event: PointerEvent<HTMLDivElement>) => void
}) {
  const [readyToken, setReadyToken] = useState(0)
  const [isReady, setIsReady] = useState(false)

  useEffect(() => {
    let cancelled = false
    setIsReady(false)

    const preload = (src: string) =>
      new Promise<void>((resolve, reject) => {
        const image = new Image()
        image.onload = () => resolve()
        image.onerror = () => reject(new Error(`failed to load ${src}`))
        image.src = src
      })

    Promise.all([preload(originalUrl), preload(compressedUrl)])
      .catch(() => undefined)
      .finally(() => {
        if (cancelled) return
        setReadyToken((current) => current + 1)
        setIsReady(true)
      })

    return () => {
      cancelled = true
    }
  }, [originalUrl, compressedUrl])

  const progressMaskStyle = useMemo(
    () => ({ clipPath: `inset(0 ${100 - sliderValue}% 0 0)` }),
    [sliderValue],
  )

  return (
    <div
      className={`compare-stage queue-compare-stage ${isPending ? 'is-pending' : ''}`}
      onPointerEnter={(event) => onPointerEnter(queueId, event)}
      onPointerMove={(event) => onPointerMove(queueId, event)}
    >
      {isReady ? (
        <>
          <img key={`compressed-${readyToken}`} className="base-image" src={compressedUrl} alt="compressed preview" />
          <div className="overlay-image" style={progressMaskStyle}>
            <img key={`original-${readyToken}`} src={originalUrl} alt="original preview" />
          </div>
        </>
      ) : null}
      <div className="compare-divider" style={{ left: `${sliderValue}%` }} />
      <span className="corner-label left">压缩前</span>
      <span className="corner-label right">压缩后</span>
    </div>
  )
}

export default function App() {
  const [items, setItems] = useState<CompressionItem[]>([])
  const [queueItems, setQueueItems] = useState<QueueItem[]>([])
  const [pending, setPending] = useState(false)
  const [expandedQueueId, setExpandedQueueId] = useState(defaultDemoQueueItem.id)
  const [compareSliders, setCompareSliders] = useState<Record<string, number>>({ [defaultDemoQueueItem.id]: 50 })
  const [selectedConcurrency, setSelectedConcurrency] = useState(3)
  const [dragActive, setDragActive] = useState(false)
  const [downloadingZip, setDownloadingZip] = useState(false)
  const [appendNotice, setAppendNotice] = useState('')
  const dragDepthRef = useRef(0)
  const queueSectionRef = useRef<HTMLElement | null>(null)
  const queueItemsRef = useRef<QueueItem[]>([])
  const activeWorkersRef = useRef(0)
  const reservedQueueIdsRef = useRef(new Set<string>())
  const appendNoticeTimerRef = useRef<number | undefined>(undefined)

  const hasUploads = items.length > 0
  const visibleQueueItems = queueItems.length > 0 ? queueItems : [defaultDemoQueueItem]
  const completedQueueCount = visibleQueueItems.filter((item) => item.status === 'completed' || item.status === 'skipped').length
  const currentQueueItem = visibleQueueItems.find((item) => item.status === 'uploading' || item.status === 'processing') ?? visibleQueueItems[0]
  const overallQueueProgress = visibleQueueItems.length
    ? Math.round(visibleQueueItems.reduce((sum, item) => sum + item.progress, 0) / visibleQueueItems.length)
    : 0

  useEffect(() => {
    const preventBrowserOpen = (event: globalThis.DragEvent) => {
      if (!hasFileTransfer(event)) return
      event.preventDefault()
    }

    const handleWindowDrop = (event: globalThis.DragEvent) => {
      if (!hasFileTransfer(event)) return
      event.preventDefault()
      dragDepthRef.current = 0
      setDragActive(false)
      if (event.dataTransfer?.files?.length) {
        void submitFiles(event.dataTransfer.files)
      }
    }

    window.addEventListener('dragover', preventBrowserOpen)
    window.addEventListener('drop', handleWindowDrop)
    return () => {
      window.removeEventListener('dragover', preventBrowserOpen)
      window.removeEventListener('drop', handleWindowDrop)
    }
  }, [])

  useEffect(() => {
    queueItemsRef.current = queueItems
  }, [queueItems])

  function patchQueueItem(queueId: string, patch: Partial<QueueItem>) {
    setQueueItems((current) =>
      current.map((item) => (item.id === queueId ? { ...item, ...patch } : item)),
    )
  }

  function appendCompletedItem(result: CompressionItem) {
    setItems((current) => [...current, result])
  }

  function maybeFinishQueue() {
    const hasPendingWork = queueItemsRef.current.some((item) => item.status === 'queued' || item.status === 'uploading' || item.status === 'processing')
    if (!hasPendingWork && activeWorkersRef.current === 0) {
      setPending(false)
    }
  }

  function startQueueWorker(queueItem: QueueItem) {
    const file = queueItem.sourceFile
    if (!file) {
      reservedQueueIdsRef.current.delete(queueItem.id)
      return
    }

    activeWorkersRef.current += 1
    patchQueueItem(queueItem.id, {
      status: 'uploading',
      progress: 5,
      detail: '准备上传',
    })

    void compressSingleFile(file, (patch) => patchQueueItem(queueItem.id, patch))
      .then((result) => {
        appendCompletedItem(result)
        setCompareSliders((current) => ({ ...current, [queueItem.id]: current[queueItem.id] ?? 50 }))
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : '上传失败'
        patchQueueItem(queueItem.id, {
          status: 'failed',
          progress: 100,
          detail: message,
        })
      })
      .finally(() => {
        activeWorkersRef.current = Math.max(0, activeWorkersRef.current - 1)
        reservedQueueIdsRef.current.delete(queueItem.id)
        window.setTimeout(() => {
          pumpQueue()
          maybeFinishQueue()
        }, 0)
      })
  }

  function pumpQueue() {
    const maxWorkers = clampParallelism(selectedConcurrency)
    while (activeWorkersRef.current < maxWorkers) {
      const nextItem = queueItemsRef.current.find(
        (item) => item.status === 'queued' && !reservedQueueIdsRef.current.has(item.id),
      )
      if (!nextItem) break
      reservedQueueIdsRef.current.add(nextItem.id)
      startQueueWorker(nextItem)
    }
    maybeFinishQueue()
  }

  useEffect(() => {
    if (!queueItems.length) return
    pumpQueue()
  }, [queueItems, selectedConcurrency])

  async function submitFiles(fileList: FileList | File[]) {
    const files = extractImageFiles(fileList)
    if (!files.length) {
      alert('没有检测到可压缩的图片文件，请拖入 jpg、png、webp 或 gif 图片。')
      return
    }

    const nextQueue = files.map((file, index) => buildQueueItem(file, index))
    const isAppending = queueItemsRef.current.length > 0 || activeWorkersRef.current > 0
    setPending(true)
    setExpandedQueueId((current) => (current === defaultDemoQueueItem.id || !current ? (nextQueue[0]?.id ?? current) : current))
    setQueueItems((current) => [...current, ...nextQueue])
    setCompareSliders((current) => ({ ...current }))
    if (appendNoticeTimerRef.current !== undefined) {
      window.clearTimeout(appendNoticeTimerRef.current)
    }
    setAppendNotice(isAppending ? `已追加 ${files.length} 张图片到队列` : `已加入 ${files.length} 张图片，开始压缩`)
    appendNoticeTimerRef.current = window.setTimeout(() => {
      setAppendNotice('')
      appendNoticeTimerRef.current = undefined
    }, 2400)
    window.requestAnimationFrame(() => {
      queueSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) {
      void submitFiles(event.target.files)
    }
    event.target.value = ''
  }

  function onPageDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event)) return
    event.preventDefault()
    dragDepthRef.current += 1
    setDragActive(true)
  }

  function onPageDragOver(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event)) return
    event.preventDefault()
    setDragActive(true)
  }

  function onPageDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event)) return
    event.preventDefault()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) {
      setDragActive(false)
    }
  }

  function updateCompareSlider(queueId: string, clientX: number, bounds: DOMRect) {
    const next = ((clientX - bounds.left) / bounds.width) * 100
    const clamped = Math.min(100, Math.max(0, next))
    setCompareSliders((current) => ({ ...current, [queueId]: clamped }))
  }

  function onComparePointerMove(queueId: string, event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    updateCompareSlider(queueId, event.clientX, bounds)
  }

  function onComparePointerEnter(queueId: string, event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    updateCompareSlider(queueId, event.clientX, bounds)
  }

  function toggleQueueItem(queueId: string, hasPreview: boolean) {
    if (!hasPreview) return
    setExpandedQueueId((current) => (current === queueId ? '' : queueId))
    setCompareSliders((current) => ({ ...current, [queueId]: current[queueId] ?? 50 }))
  }

  async function downloadAllCompressedImages() {
    const files = items
      .map((item) => {
        const storedName = getStoredFileName(item.compressed_url)
        if (!storedName) return null
        return {
          stored_name: storedName,
          download_name: buildDownloadName(item.file_name, 'compressed'),
        }
      })
      .filter((item): item is { stored_name: string; download_name: string } => item !== null)

    if (!files.length) {
      alert('当前没有可批量下载的压缩结果。')
      return
    }

    setDownloadingZip(true)
    try {
      const response = await fetch('/api/download/outputs.zip', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ files }),
      })

      if (!response.ok) {
        let message = '批量下载失败'
        try {
          const payload = await response.json()
          if (payload?.detail) message = payload.detail
        } catch {
          // ignore non-json errors
        }
        throw new Error(message)
      }

      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = 'bbduck-compressed-images.zip'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(objectUrl)
    } catch (error) {
      const message = error instanceof Error ? error.message : '批量下载失败'
      alert(message)
    } finally {
      setDownloadingZip(false)
    }
  }

  return (
    <div
      className={`page-shell ${dragActive ? 'is-drag-active' : ''}`}
      onDragEnter={onPageDragEnter}
      onDragOver={onPageDragOver}
      onDragLeave={onPageDragLeave}
    >
      {dragActive ? (
        <div className="global-drop-hint" aria-hidden="true">
          <strong>释放鼠标，开始批量压缩</strong>
          <span>支持自动识别多张 JPG / PNG / WEBP / GIF 图片并加入队列</span>
        </div>
      ) : null}
      <header className="hero-block">
        <h1>BB鸭 图片压缩神器，让你的硬盘更耐用</h1>
        <p className="hero-copy">支持 JPG、PNG、WebP、GIF 批量拖拽上传。</p>

        <div className="hero-actions">
          <label className="primary-button">
            {pending ? '继续添加图片到队列' : '上传图片开始压缩'}
            <input type="file" accept=".jpg,.jpeg,.png,.webp,.gif" multiple onChange={onInputChange} />
          </label>
        </div>

        {appendNotice ? <p className="hero-append-notice">{appendNotice}</p> : null}

        {queueItems.length > 0 ? (
          <div className="hero-queue-status" aria-live="polite">
            <div className="hero-queue-status-head">
              <strong>{pending ? '正在处理压缩队列' : '本轮压缩已完成'}</strong>
              <span>
                {completedQueueCount}/{queueItems.length} 已完成 · 总进度 {overallQueueProgress}%
              </span>
            </div>
            <div className="hero-queue-progress">
              <div className="hero-queue-progress-bar" style={{ width: `${overallQueueProgress}%` }} />
            </div>
            {currentQueueItem ? (
              <p>
                当前文件：{currentQueueItem.fileName} · {queueStatusLabel[currentQueueItem.status]} · {currentQueueItem.detail}
              </p>
            ) : null}
          </div>
        ) : null}
      </header>

      <section className="result-section" ref={queueSectionRef}>
        <div className="section-heading section-heading-actions-only">
          {hasUploads ? (
            <button type="button" className="secondary-button" onClick={() => void downloadAllCompressedImages()} disabled={downloadingZip}>
              {downloadingZip ? '正在打包下载…' : '下载全部压缩图'}
            </button>
          ) : null}
          <span>
            {pending
              ? `进行中 · ${completedQueueCount}/${visibleQueueItems.length} 已完成`
              : `${visibleQueueItems.length} 个队列项`}
          </span>
        </div>

        <div className="result-list">
          {visibleQueueItems.map((queueItem) => {
            const result = queueItem.result
            const isExpanded = Boolean(result) && expandedQueueId === queueItem.id
            const sliderValue = compareSliders[queueItem.id] ?? 50
            const visualQuality = result ? getVisualQualityLabel(result.metrics.ssim, result.metrics.psnr) : null
            return (
              <article key={queueItem.id} className={`result-card is-${queueItem.status} ${isExpanded ? 'is-expanded' : ''}`}>
                <button
                  type="button"
                  className="result-card-toggle"
                  onClick={() => toggleQueueItem(queueItem.id, Boolean(result))}
                >
                  <div className="result-card-head">
                    <strong>{queueItem.fileName}</strong>
                    <span>{queueStatusLabel[queueItem.status]}</span>
                  </div>

                  <div className="queue-progress">
                    <div className="queue-progress-bar" style={{ width: `${queueItem.progress}%` }} />
                  </div>
                </button>

                {isExpanded && result ? (
                  <div className="result-card-expanded">
                    <div className="queue-progress-meta">
                      <span>{queueItem.detail}</span>
                      <span>{queueItem.progress}%</span>
                    </div>

                    <ComparePreview
                      originalUrl={result.original_url}
                      compressedUrl={result.compressed_url}
                      sliderValue={sliderValue}
                      isPending={pending}
                      queueId={queueItem.id}
                      onPointerEnter={onComparePointerEnter}
                      onPointerMove={onComparePointerMove}
                    />

                    <div className="stats-grid queue-stats-grid">
                      <article>
                        <span>体积变化</span>
                        <small>原图体积 {formatSize(result.original_size)} · 压缩后体积 {formatSize(result.compressed_size)} · 压缩率 {result.metrics.compression_ratio}%</small>
                      </article>
                      <article>
                        <span>画质观感</span>
                        <small>{visualQuality?.detail} · SSIM {result.metrics.ssim}（越大越像原图） · PSNR {result.metrics.psnr}（越大越清晰）</small>
                      </article>
                    </div>

                    <div className="download-actions queue-download-actions">
                      <a className="secondary-button is-ghost" href={result.original_url} download={buildDownloadName(result.file_name, 'original')}>
                        下载原图
                      </a>
                      <a className="secondary-button" href={result.compressed_url} download={buildDownloadName(result.file_name, 'compressed')}>
                        下载压缩图
                      </a>
                    </div>
                  </div>
                ) : null}
              </article>
            )
          })}
        </div>
      </section>

      <section className="page-footer-settings">
        <div className="parallelism-control">
          <label htmlFor="parallelism-select">并行数</label>
          <select
            id="parallelism-select"
            value={selectedConcurrency}
            disabled={pending}
            onChange={(event) => setSelectedConcurrency(clampParallelism(Number(event.target.value)))}
          >
            {Array.from({ length: 10 }, (_, index) => index + 1).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
      </section>
    </div>
  )
}
