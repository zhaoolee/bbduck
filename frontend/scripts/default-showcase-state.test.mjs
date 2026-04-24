import assert from 'node:assert/strict'

import { resolveDefaultShowcaseState } from '../src/defaultShowcaseState.js'

const defaultDemoQueueItem = { id: 'demo-queue-item', fileName: 'example.png' }
const evaluationQueueItem = { id: 'evaluation-0-00001.png', fileName: '00001.png' }
const uploadQueueItem = { id: 'upload-0-demo.png', fileName: 'demo.png' }

{
  const state = resolveDefaultShowcaseState({
    queueItems: [],
    defaultQueueItems: [],
    defaultDemoQueueItem,
    evaluationStatus: 'loading',
  })

  assert.equal(state.showEvaluationLoading, true)
  assert.equal(state.usesFallbackDemo, false)
  assert.deepEqual(state.visibleQueueItems, [])
}

{
  const state = resolveDefaultShowcaseState({
    queueItems: [],
    defaultQueueItems: [evaluationQueueItem],
    defaultDemoQueueItem,
    evaluationStatus: 'loaded',
  })

  assert.equal(state.showEvaluationLoading, false)
  assert.equal(state.usesFallbackDemo, false)
  assert.deepEqual(state.visibleQueueItems, [evaluationQueueItem])
}

{
  const state = resolveDefaultShowcaseState({
    queueItems: [],
    defaultQueueItems: [],
    defaultDemoQueueItem,
    evaluationStatus: 'failed',
  })

  assert.equal(state.showEvaluationLoading, false)
  assert.equal(state.usesFallbackDemo, true)
  assert.deepEqual(state.visibleQueueItems, [defaultDemoQueueItem])
}

{
  const state = resolveDefaultShowcaseState({
    queueItems: [uploadQueueItem],
    defaultQueueItems: [],
    defaultDemoQueueItem,
    evaluationStatus: 'loading',
  })

  assert.equal(state.showEvaluationLoading, false)
  assert.equal(state.usesFallbackDemo, false)
  assert.deepEqual(state.visibleQueueItems, [uploadQueueItem])
}

console.log('Default showcase state test passed')
