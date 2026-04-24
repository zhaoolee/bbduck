import { ChangeEvent, DragEvent, PointerEvent, useEffect, useMemo, useRef, useState } from 'react'

import { resolveDefaultShowcaseState } from './defaultShowcaseState.js'
import { extractImageFiles, extractImageFilesFromDataTransfer } from './fileDrop.js'

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
  logs: string[]
  sourceFile?: File
  result?: CompressionItem
}

type EvaluationStatus = 'loading' | 'ready' | 'unavailable'

function formatSpendTimeMs(spendTimeMs?: number) {
  if (spendTimeMs === undefined || Number.isNaN(spendTimeMs) || spendTimeMs < 0) return ''
  return ` · 耗时 ${spendTimeMs} ms`
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

function formatCompactSize(size: number) {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`
  return `${(size / 1024 / 1024).toFixed(1)}MB`
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

function getStoredFileInfo(fileUrl: string) {
  try {
    const parsed = new URL(fileUrl, window.location.origin)
    const kind = parsed.searchParams.get('kind') === 'upload' ? 'upload' : 'output'
    return {
      storedName: decodeURIComponent(parsed.pathname.split('/').pop() ?? ''),
      kind,
    }
  } catch {
    return {
      storedName: '',
      kind: 'output' as const,
    }
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
    logs: [],
    sourceFile: file,
  }
}

function buildShowcaseQueueItem(result: CompressionItem, options?: { id?: string; detail?: string; log?: string }): QueueItem {
  const detail = options?.detail ?? '默认示例图，可直接查看压缩前后效果'
  const log = options?.log ?? '示例结果已就绪，可拖动查看压缩前后对比'
  return {
    id: options?.id ?? `showcase-${result.file_name}`,
    fileName: result.file_name,
    status: 'completed',
    progress: 100,
    detail,
    logs: [log],
    result,
  }
}

function buildEvaluationQueueItem(result: CompressionItem, index: number): QueueItem {
  return buildShowcaseQueueItem(result, {
    id: `evaluation-${index}-${result.file_name}`,
    detail: '默认展示图已就绪，可点击展开查看',
    log: '默认展示图已就绪，可点击展开查看压缩前后对比',
  })
}

const defaultDemoQueueItem: QueueItem = buildShowcaseQueueItem(defaultDemoItem, {
  id: 'demo-queue-item',
})

function hasFileTransfer(event: DragEvent<HTMLElement> | globalThis.DragEvent) {
  const dataTransfer = event.dataTransfer
  if (!dataTransfer) return false
  if (dataTransfer.files && dataTransfer.files.length > 0) return true
  if (dataTransfer.items && dataTransfer.items.length > 0) {
    return Array.from(dataTransfer.items).some((item) => item.kind === 'file')
  }
  return Array.from(dataTransfer.types ?? []).includes('Files')
}

type CompressionStreamEvent =
  | { type: 'log'; stage?: string; message: string; spend_time_ms?: number }
  | { type: 'error'; message: string; detail?: string }
  | { type: 'result'; item: CompressionItem }

function compressSingleFile(file: File, onProgress: (patch: Partial<QueueItem>) => void): Promise<CompressionItem> {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('files', file)
    formData.append('parallelism', '1')

    const xhr = new XMLHttpRequest()
    let processingTimer: number | undefined
    let uploadCompleted = false
    let pseudoProgress = 42
    let finalItem: CompressionItem | null = null
    let streamBuffer = ''
    let parsedLength = 0
    const logLines: string[] = []

    const pushLog = (message: string, spendTimeMs?: number) => {
      const formattedMessage = `${message}${formatSpendTimeMs(spendTimeMs)}`
      const nextLogs = [...logLines, formattedMessage].slice(-12)
      logLines.splice(0, logLines.length, ...nextLogs)
      onProgress({
        status: 'processing',
        progress: pseudoProgress,
        detail: formattedMessage,
        logs: [...logLines],
      })
    }

    const stopTimer = () => {
      if (processingTimer !== undefined) {
        window.clearInterval(processingTimer)
      }
    }

    const applyEvent = (event: CompressionStreamEvent) => {
      if (event.type === 'log') {
        pushLog(event.message, event.spend_time_ms)
        return
      }
      if (event.type === 'error') {
        stopTimer()
        const message = event.detail ? `${event.message}：${event.detail}` : event.message
        reject(new Error(message))
        return
      }
      finalItem = event.item
    }

    const parseStreamChunk = () => {
      const rawChunk = xhr.responseText.slice(parsedLength)
      if (!rawChunk) return
      parsedLength = xhr.responseText.length
      streamBuffer += rawChunk
      const lines = streamBuffer.split('\n')
      streamBuffer = lines.pop() ?? ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        const event = JSON.parse(trimmed) as CompressionStreamEvent
        applyEvent(event)
      }
    }

    xhr.open('POST', '/api/compress/stream')
    xhr.responseType = 'text'

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return
      const percent = Math.min(35, Math.round((event.loaded / event.total) * 35))
      onProgress({
        status: 'uploading',
        progress: percent,
        detail: `正在上传 ${percent}%`,
        logs: [`正在上传 ${percent}%`],
      })
    }

    xhr.upload.onload = () => {
      uploadCompleted = true
      pseudoProgress = 42
      onProgress({
        status: 'processing',
        progress: pseudoProgress,
        detail: '文件已上传，正在等待服务端返回详细日志…',
        logs: ['文件已上传，正在等待服务端返回详细日志…'],
      })
      processingTimer = window.setInterval(() => {
        pseudoProgress = Math.min(92, pseudoProgress + 3)
        onProgress({
          status: 'processing',
          progress: pseudoProgress,
          detail: logLines[logLines.length - 1] ?? '正在尝试压缩策略并评估画质…',
          logs: [...logLines],
        })
      }, 360)
    }

    xhr.onprogress = () => {
      parseStreamChunk()
    }

    xhr.onerror = () => {
      stopTimer()
      reject(new Error('网络异常，上传失败'))
    }

    xhr.onload = () => {
      stopTimer()
      parseStreamChunk()
      if (xhr.status < 200 || xhr.status >= 300) {
        try {
          const payload = JSON.parse(xhr.responseText || '{}')
          reject(new Error(payload?.detail ?? '上传失败'))
        } catch {
          reject(new Error('上传失败'))
        }
        return
      }
      if (!finalItem) {
        reject(new Error('服务端未返回压缩结果'))
        return
      }
      onProgress({
        status: finalItem.status === 'skipped' ? 'skipped' : 'completed',
        progress: 100,
        detail: finalItem.status === 'skipped' ? '已最小，无需压缩，可直接查看原图' : uploadCompleted ? '压缩完成，可以查看前后对比' : '上传完成',
        logs: [...logLines],
        result: finalItem,
      })
      resolve(finalItem)
    }

    xhr.send(formData)
  })
}

type PreviewLoadState = 'loading' | 'loaded' | 'failed'

function ComparePreview({
  originalUrl,
  compressedUrl,
  mimeType,
  sliderValue,
  isPending,
  queueId,
  onPointerEnter,
  onPointerMove,
}: {
  originalUrl: string
  compressedUrl: string
  mimeType: string
  sliderValue: number
  isPending: boolean
  queueId: string
  onPointerEnter: (queueId: string, event: PointerEvent<HTMLDivElement>) => void
  onPointerMove: (queueId: string, event: PointerEvent<HTMLDivElement>) => void
}) {
  const shouldSyncGifPreview = mimeType === 'image/gif'
  const [readyToken, setReadyToken] = useState(0)
  const [isReady, setIsReady] = useState(!shouldSyncGifPreview)
  const [originalState, setOriginalState] = useState<PreviewLoadState>('loading')
  const [compressedState, setCompressedState] = useState<PreviewLoadState>('loading')

  useEffect(() => {
    let cancelled = false
    let fallbackTimer: number | undefined

    setReadyToken((current) => current + 1)
    setOriginalState('loading')
    setCompressedState('loading')

    if (!shouldSyncGifPreview) {
      setIsReady(true)
      return () => {
        cancelled = true
      }
    }

    setIsReady(false)

    const preload = (src: string) =>
      new Promise<void>((resolve, reject) => {
        const image = new Image()
        image.onload = () => resolve()
        image.onerror = () => reject(new Error(`failed to load ${src}`))
        image.src = src
      })

    fallbackTimer = window.setTimeout(() => {
      if (cancelled) return
      setIsReady(true)
    }, 8000)

    Promise.all([preload(originalUrl), preload(compressedUrl)])
      .catch(() => undefined)
      .finally(() => {
        if (cancelled) return
        if (fallbackTimer !== undefined) {
          window.clearTimeout(fallbackTimer)
        }
        setIsReady(true)
      })

    return () => {
      cancelled = true
      if (fallbackTimer !== undefined) {
        window.clearTimeout(fallbackTimer)
      }
    }
  }, [originalUrl, compressedUrl, shouldSyncGifPreview])

  const progressMaskStyle = useMemo(
    () => ({ clipPath: `inset(0 ${100 - sliderValue}% 0 0)` }),
    [sliderValue],
  )
  const hasPreviewError = originalState === 'failed' || compressedState === 'failed'
  const isPreviewLoading = !hasPreviewError && (!isReady || originalState === 'loading' || compressedState === 'loading')

  return (
    <div
      className={`compare-stage queue-compare-stage ${isPending ? 'is-pending' : ''}`}
      onPointerEnter={(event) => onPointerEnter(queueId, event)}
      onPointerMove={(event) => onPointerMove(queueId, event)}
    >
      {isReady ? (
        <>
          <img
            key={`compressed-${readyToken}`}
            className="base-image"
            src={compressedUrl}
            alt="compressed preview"
            loading="eager"
            decoding="async"
            onLoad={() => setCompressedState('loaded')}
            onError={() => setCompressedState('failed')}
          />
          <div className="overlay-image" style={progressMaskStyle}>
            <img
              key={`original-${readyToken}`}
              src={originalUrl}
              alt="original preview"
              loading="eager"
              decoding="async"
              onLoad={() => setOriginalState('loaded')}
              onError={() => setOriginalState('failed')}
            />
          </div>
        </>
      ) : null}
      {hasPreviewError ? (
        <div className="compare-stage-status is-error">
          <strong>预览加载失败</strong>
          <span>图片已压缩完成，可先用下方按钮下载查看。</span>
        </div>
      ) : isPreviewLoading ? (
        <div className="compare-stage-status is-loading">
          <strong>正在加载对比预览</strong>
          <span>图片已压缩完成，预览加载后会自动显示。</span>
        </div>
      ) : null}
      <div className="compare-divider" style={{ left: `${sliderValue}%` }} />
      <span className="corner-label left">压缩前</span>
      <span className="corner-label right">压缩后</span>
    </div>
  )
}

const DEFAULT_QUEUE_CONCURRENCY = 2
const MIN_QUEUE_CONCURRENCY = 1
const MAX_QUEUE_CONCURRENCY = 6
const SHOW_COMPRESSION_LOGS_STORAGE_KEY = 'bbduck-show-compression-logs'
const QUEUE_CONCURRENCY_STORAGE_KEY = 'bbduck-queue-concurrency'

function clampQueueConcurrency(value: number) {
  if (!Number.isFinite(value)) return DEFAULT_QUEUE_CONCURRENCY
  return Math.min(MAX_QUEUE_CONCURRENCY, Math.max(MIN_QUEUE_CONCURRENCY, Math.round(value)))
}

export default function App() {
  const [items, setItems] = useState<CompressionItem[]>([])
  const [defaultQueueItems, setDefaultQueueItems] = useState<QueueItem[]>([])
  const [queueItems, setQueueItems] = useState<QueueItem[]>([])
  const [evaluationStatus, setEvaluationStatus] = useState<EvaluationStatus>('loading')
  const [pending, setPending] = useState(false)
  const [expandedQueueId, setExpandedQueueId] = useState('')
  const [compareSliders, setCompareSliders] = useState<Record<string, number>>({})
  const [dragActive, setDragActive] = useState(false)
  const [downloadingZip, setDownloadingZip] = useState(false)
  const [appendNotice, setAppendNotice] = useState('')
  const [showCompressionLogs, setShowCompressionLogs] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem(SHOW_COMPRESSION_LOGS_STORAGE_KEY) === 'true'
  })
  const [queueConcurrency, setQueueConcurrency] = useState(() => {
    if (typeof window === 'undefined') return DEFAULT_QUEUE_CONCURRENCY
    return clampQueueConcurrency(Number(window.localStorage.getItem(QUEUE_CONCURRENCY_STORAGE_KEY) ?? DEFAULT_QUEUE_CONCURRENCY))
  })
  const [settingsOpen, setSettingsOpen] = useState(false)
  const dragDepthRef = useRef(0)
  const queueSectionRef = useRef<HTMLElement | null>(null)
  const queueItemsRef = useRef<QueueItem[]>([])
  const activeWorkersRef = useRef(0)
  const reservedQueueIdsRef = useRef(new Set<string>())
  const appendNoticeTimerRef = useRef<number | undefined>(undefined)

  const hasUploads = items.length > 0
  const { visibleQueueItems, showEvaluationLoading } = resolveDefaultShowcaseState({
    queueItems,
    defaultQueueItems,
    defaultDemoQueueItem,
    evaluationStatus,
  })
  const completedQueueCount = visibleQueueItems.filter((item) => item.status === 'completed' || item.status === 'skipped').length
  const currentQueueItem = visibleQueueItems.find((item) => item.status === 'uploading' || item.status === 'processing') ?? visibleQueueItems[0]
  const overallQueueProgress = visibleQueueItems.length
    ? Math.round(visibleQueueItems.reduce((sum, item) => sum + item.progress, 0) / visibleQueueItems.length)
    : 0
  const summaryResults = visibleQueueItems
    .map((item) => item.result)
    .filter((item): item is CompressionItem => item !== undefined)
  const totalOriginalSize = summaryResults.reduce((sum, item) => sum + item.original_size, 0)
  const totalCompressedSize = summaryResults.reduce((sum, item) => sum + item.compressed_size, 0)
  const totalSavedPercent = totalOriginalSize > 0
    ? Math.round((1 - totalCompressedSize / totalOriginalSize) * 100)
    : 0
  const completionSummary = summaryResults.length > 0
    ? `共压缩 ${summaryResults.length} 个文件, 节省空间 ${totalSavedPercent}% (${formatCompactSize(totalOriginalSize)} → ${formatCompactSize(totalCompressedSize)})`
    : showEvaluationLoading
      ? '正在加载默认示例'
      : ''

  useEffect(() => {
    window.localStorage.setItem(SHOW_COMPRESSION_LOGS_STORAGE_KEY, String(showCompressionLogs))
  }, [showCompressionLogs])

  useEffect(() => {
    window.localStorage.setItem(QUEUE_CONCURRENCY_STORAGE_KEY, String(queueConcurrency))
  }, [queueConcurrency])

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
      if (event.dataTransfer) {
        void handleDataTransferDrop(event.dataTransfer)
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
    let cancelled = false

    const selectFallbackDemo = () => {
      setExpandedQueueId((current) => {
        if (queueItemsRef.current.length > 0) return current
        if (!current || current === defaultDemoQueueItem.id || current.startsWith('evaluation-')) {
          return defaultDemoQueueItem.id
        }
        return current
      })
    }

    async function loadEvaluationImages() {
      setEvaluationStatus('loading')
      try {
        const response = await fetch('/api/evaluation-images')
        if (!response.ok) {
          if (!cancelled) {
            setDefaultQueueItems([])
            setEvaluationStatus('unavailable')
            selectFallbackDemo()
          }
          return
        }

        const payload = await response.json() as { items?: CompressionItem[] }
        if (cancelled) return
        if (!Array.isArray(payload.items) || payload.items.length === 0) {
          setDefaultQueueItems([])
          setEvaluationStatus('unavailable')
          selectFallbackDemo()
          return
        }

        const nextDefaultQueueItems = payload.items.map(buildEvaluationQueueItem)
        setDefaultQueueItems(nextDefaultQueueItems)
        setEvaluationStatus('ready')
        setCompareSliders((current) => {
          const next = { ...current }
          for (const item of nextDefaultQueueItems) {
            next[item.id] = next[item.id] ?? 50
          }
          return next
        })
        setExpandedQueueId((current) => {
          if (queueItemsRef.current.length > 0) return current
          if (!current || current === defaultDemoQueueItem.id || current.startsWith('evaluation-')) {
            return nextDefaultQueueItems[0]?.id ?? current
          }
          return current
        })
      } catch {
        if (cancelled) return
        setDefaultQueueItems([])
        setEvaluationStatus('unavailable')
        selectFallbackDemo()
      }
    }

    void loadEvaluationImages()
    return () => {
      cancelled = true
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
      logs: ['准备上传'],
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
          logs: [...(queueItemsRef.current.find((item) => item.id === queueItem.id)?.logs ?? []), message].slice(-12),
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
    const maxWorkers = queueConcurrency
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
  }, [queueItems, queueConcurrency])

  async function submitFiles(fileList: FileList | File[]) {
    const files = extractImageFiles(fileList)
    if (!files.length) {
      alert('没有检测到可压缩的图片文件，请拖入 jpg、png、webp 或 gif 图片。')
      return
    }

    const nextQueue = files.map((file, index) => buildQueueItem(file, index))
    const isAppending = queueItemsRef.current.length > 0 || activeWorkersRef.current > 0
    setPending(true)
    setExpandedQueueId((current) => (queueItemsRef.current.length === 0 ? (nextQueue[0]?.id ?? current) : current))
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

  async function handleDataTransferDrop(dataTransfer: DataTransfer) {
    try {
      const files = await extractImageFilesFromDataTransfer(dataTransfer)
      await submitFiles(files)
    } catch (error) {
      const message = error instanceof Error ? error.message : '读取拖拽内容失败'
      alert(message)
    }
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
        const fileUrl = item.status === 'skipped' ? item.original_url : item.compressed_url
        const { storedName, kind } = getStoredFileInfo(fileUrl)
        if (!storedName) return null
        return {
          stored_name: storedName,
          download_name: buildDownloadName(item.file_name, item.status === 'skipped' ? 'original' : 'compressed'),
          kind,
        }
      })
      .filter((item): item is { stored_name: string; download_name: string; kind: 'output' | 'upload' } => item !== null)

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

      <header className="page-topbar">
        <div className="page-topbar-inner">
          <div className="page-topbar-brand">
            <img className="page-topbar-logo" src="/bbduck-logo.png" alt="BB鸭 logo" />
            <div className="page-topbar-copy">
              <strong className="page-topbar-title">BB鸭 给图片减减肥 让画质顶呱呱</strong>
            </div>
          </div>

          <div className="page-topbar-actions">
            <button
              type="button"
              className="page-topbar-settings"
              aria-label="打开设置"
              onClick={() => setSettingsOpen(true)}
            >
              设置
            </button>
          </div>
        </div>
      </header>

      {settingsOpen ? (
        <div className="settings-modal-backdrop" onClick={() => setSettingsOpen(false)}>
          <div className="settings-modal" role="dialog" aria-modal="true" aria-label="设置" onClick={(event) => event.stopPropagation()}>
            <div className="settings-modal-head">
              <button type="button" className="settings-modal-close" aria-label="关闭设置" onClick={() => setSettingsOpen(false)}>×</button>
            </div>

            <div className="settings-grid">
              <div className="settings-row">
                <div className="settings-row-copy">
                  <span>显示压缩日志</span>
                </div>
                <label className="settings-switch" aria-label="显示压缩日志">
                  <input
                    type="checkbox"
                    checked={showCompressionLogs}
                    onChange={(event) => setShowCompressionLogs(event.target.checked)}
                  />
                  <span className="settings-switch-slider" aria-hidden="true" />
                </label>
              </div>

              <div className="settings-row">
                <div className="settings-row-copy">
                  <span>并行数</span>
                </div>
                <label className="settings-number-control" htmlFor="queue-concurrency-input">
                  <input
                    id="queue-concurrency-input"
                    type="number"
                    min={MIN_QUEUE_CONCURRENCY}
                    max={MAX_QUEUE_CONCURRENCY}
                    value={queueConcurrency}
                    onChange={(event) => {
                      const rawValue = event.target.value
                      setQueueConcurrency(clampQueueConcurrency(Number(rawValue)))
                    }}
                    onBlur={(event) => {
                      const rawValue = event.target.value
                      setQueueConcurrency(clampQueueConcurrency(Number(rawValue)))
                    }}
                  />
                </label>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <section className="upload-dropzone-section">
        <label className={`upload-dropzone ${dragActive ? 'is-drag-active' : ''}`}>
          <input type="file" accept=".jpg,.jpeg,.png,.webp,.gif" multiple onChange={onInputChange} />
          <strong>点击或拖拽到当前区域开始压缩</strong>
          <span>{pending ? '当前已有任务进行中，新的图片会继续追加到队列' : '支持批量上传 JPG、PNG、WebP、GIF，文件会自动加入压缩队列'}</span>
        </label>
        {appendNotice ? <p className="hero-append-notice">{appendNotice}</p> : null}
      </section>

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
              : showEvaluationLoading
                ? '正在加载默认示例'
                : `${visibleQueueItems.length} 个队列项`}
          </span>
        </div>

        <div className="result-list">
          {showEvaluationLoading ? (
            <article className="empty-state result-list-status" aria-live="polite">
              <strong>正在加载默认示例</strong>
              <span>首页评测图准备完成后会自动展示。</span>
            </article>
          ) : null}
          {visibleQueueItems.map((queueItem) => {
            const result = queueItem.result
            const isExpanded = Boolean(result) && expandedQueueId === queueItem.id
            const shouldShowLogs = showCompressionLogs && queueItem.logs.length > 0 && (isExpanded || queueItem.status === 'processing' || queueItem.status === 'uploading' || queueItem.status === 'failed')
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

                {shouldShowLogs ? (
                  <div className="queue-log-panel" aria-live="polite">
                    <div className="queue-log-panel-head">
                      <strong>压缩日志</strong>
                      <span>{queueItem.logs.length} 条</span>
                    </div>
                    <ul>
                      {queueItem.logs.map((line, index) => (
                        <li key={`${queueItem.id}-log-${index}`}>{line}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {isExpanded && result ? (
                  <div className="result-card-expanded">
                    <div className="queue-progress-meta">
                      <span>{queueItem.detail}</span>
                      <span>{queueItem.progress}%</span>
                    </div>

                    <ComparePreview
                      originalUrl={result.original_url}
                      compressedUrl={result.compressed_url}
                      mimeType={result.mime_type}
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

      <div className="page-bottom-progress" aria-live="polite">
        <div className="page-bottom-progress-inner">
          <div className="page-bottom-progress-head">
            <strong>{pending ? '正在处理压缩队列' : showEvaluationLoading ? '默认示例准备中' : '压缩完成'}</strong>
            <span>
              {pending
                ? (queueItems.length > 0
                  ? `${completedQueueCount}/${queueItems.length} 已完成 · 总进度 ${overallQueueProgress}%`
                  : `${visibleQueueItems.length} 个队列项`)
                : completionSummary}
            </span>
          </div>
          <div className="hero-queue-progress page-bottom-progress-bar-shell">
            <div className="hero-queue-progress-bar" style={{ width: `${overallQueueProgress}%` }} />
          </div>
          {pending && currentQueueItem ? (
            <p className="page-bottom-progress-detail">
              当前文件：{currentQueueItem.fileName} · {queueStatusLabel[currentQueueItem.status]} · {currentQueueItem.detail}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  )
}
