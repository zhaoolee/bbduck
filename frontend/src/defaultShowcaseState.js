/**
 * @typedef {'loading' | 'ready' | 'unavailable'} EvaluationStatus
 */

/**
 * @template T
 * @param {{
 *   queueItems: T[],
 *   defaultQueueItems: T[],
 *   defaultDemoQueueItem: T,
 *   evaluationStatus: EvaluationStatus,
 * }} params
 */
export function resolveDefaultShowcaseState({
  queueItems,
  defaultQueueItems,
  defaultDemoQueueItem,
  evaluationStatus,
}) {
  if (queueItems.length > 0) {
    return {
      visibleQueueItems: queueItems,
      showEvaluationLoading: false,
      usesFallbackDemo: false,
    }
  }

  if (defaultQueueItems.length > 0) {
    return {
      visibleQueueItems: defaultQueueItems,
      showEvaluationLoading: false,
      usesFallbackDemo: false,
    }
  }

  if (evaluationStatus === 'loading') {
    return {
      visibleQueueItems: [],
      showEvaluationLoading: true,
      usesFallbackDemo: false,
    }
  }

  return {
    visibleQueueItems: [defaultDemoQueueItem],
    showEvaluationLoading: false,
    usesFallbackDemo: true,
  }
}
