export type EvaluationStatus = 'loading' | 'ready' | 'unavailable'

export function resolveDefaultShowcaseState<T>(params: {
  queueItems: T[]
  defaultQueueItems: T[]
  defaultDemoQueueItem: T
  evaluationStatus: EvaluationStatus
}): {
  visibleQueueItems: T[]
  showEvaluationLoading: boolean
  usesFallbackDemo: boolean
}
