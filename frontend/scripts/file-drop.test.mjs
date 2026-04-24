import assert from 'node:assert/strict'

import {
  extractImageFiles,
  extractImageFilesFromDataTransfer,
  isImageFile,
  readAllDirectoryEntries,
} from '../src/fileDrop.js'

function createMockFile(name, type = '') {
  return { name, type }
}

function createFileEntry(file) {
  return {
    isFile: true,
    isDirectory: false,
    file(successCallback) {
      successCallback(file)
    },
  }
}

function createDirectoryEntry(entryBatches) {
  return {
    isFile: false,
    isDirectory: true,
    createReader() {
      let batchIndex = 0
      return {
        readEntries(successCallback) {
          successCallback(entryBatches[batchIndex] ?? [])
          batchIndex += 1
        },
      }
    },
  }
}

{
  assert.equal(isImageFile(createMockFile('cover.JPG')), true)
  assert.equal(isImageFile(createMockFile('vector.svg', 'image/svg+xml')), false)
  assert.equal(isImageFile(createMockFile('typed.webp', 'image/webp')), true)
  assert.equal(isImageFile(createMockFile('readme.md', 'text/markdown')), false)
  assert.deepEqual(
    extractImageFiles([
      createMockFile('cover.JPG'),
      createMockFile('notes.txt'),
      createMockFile('preview.webp'),
    ]).map((file) => file.name),
    ['cover.JPG', 'preview.webp'],
  )
}

{
  const directoryEntry = createDirectoryEntry([
    [createFileEntry(createMockFile('first.jpg')), createFileEntry(createMockFile('second.png'))],
    [createFileEntry(createMockFile('third.webp'))],
    [],
  ])

  const entries = await readAllDirectoryEntries(directoryEntry)
  assert.deepEqual(
    entries.map((entry) => entry.isFile),
    [true, true, true],
  )
}

{
  const nestedDirectory = createDirectoryEntry([
    [createFileEntry(createMockFile('inside.png')), createFileEntry(createMockFile('ignore.txt'))],
    [],
  ])
  const rootDirectory = createDirectoryEntry([
    [
      createFileEntry(createMockFile('cover.jpg')),
      nestedDirectory,
      createFileEntry(createMockFile('loop.gif')),
    ],
    [],
  ])

  const files = await extractImageFilesFromDataTransfer({
    items: [
      {
        kind: 'file',
        webkitGetAsEntry() {
          return rootDirectory
        },
      },
    ],
    files: [],
    types: ['Files'],
  })

  assert.deepEqual(files.map((file) => file.name), ['cover.jpg', 'inside.png', 'loop.gif'])
}

{
  const mixedDirectory = createDirectoryEntry([
    [createFileEntry(createMockFile('folder-1.jpg')), createFileEntry(createMockFile('folder-2.txt'))],
    [createFileEntry(createMockFile('folder-3.webp'))],
    [],
  ])

  const files = await extractImageFilesFromDataTransfer({
    items: [
      {
        kind: 'file',
        webkitGetAsEntry() {
          return createFileEntry(createMockFile('loose.png'))
        },
      },
      {
        kind: 'file',
        webkitGetAsEntry() {
          return mixedDirectory
        },
      },
      {
        kind: 'file',
        webkitGetAsEntry() {
          return createFileEntry(createMockFile('notes.txt'))
        },
      },
    ],
    files: [],
    types: ['Files'],
  })

  assert.deepEqual(files.map((file) => file.name), ['loose.png', 'folder-1.jpg', 'folder-3.webp'])
}

{
  let protectedMode = false

  function createProtectedFileEntry(file) {
    return {
      isFile: true,
      isDirectory: false,
      file(successCallback) {
        queueMicrotask(() => {
          protectedMode = true
          successCallback(file)
        })
      },
    }
  }

  function createProtectedDirectoryEntry(entryBatches) {
    return {
      isFile: false,
      isDirectory: true,
      createReader() {
        let batchIndex = 0
        return {
          readEntries(successCallback) {
            queueMicrotask(() => {
              protectedMode = true
              successCallback(entryBatches[batchIndex] ?? [])
              batchIndex += 1
            })
          },
        }
      },
    }
  }

  const protectedFolderA = createProtectedDirectoryEntry([
    [createProtectedFileEntry(createMockFile('folder-a-1.jpg'))],
    [],
  ])
  const protectedFolderB = createProtectedDirectoryEntry([
    [createProtectedFileEntry(createMockFile('folder-b-1.webp'))],
    [],
  ])

  const files = await extractImageFilesFromDataTransfer({
    items: [
      {
        kind: 'file',
        webkitGetAsEntry() {
          return protectedFolderA
        },
      },
      {
        kind: 'file',
        webkitGetAsEntry() {
          return protectedMode ? null : createProtectedFileEntry(createMockFile('loose-1.png'))
        },
      },
      {
        kind: 'file',
        webkitGetAsEntry() {
          return protectedMode ? null : protectedFolderB
        },
      },
      {
        kind: 'file',
        webkitGetAsEntry() {
          return protectedMode ? null : createProtectedFileEntry(createMockFile('loose-2.gif'))
        },
      },
    ],
    files: [
      createMockFile('loose-1.png'),
      createMockFile('loose-2.gif'),
      createMockFile('fallback-only.jpg'),
    ],
    types: ['Files'],
  })

  assert.deepEqual(
    files.map((file) => file.name),
    ['folder-a-1.jpg', 'loose-1.png', 'folder-b-1.webp', 'loose-2.gif'],
  )
}

{
  const files = await extractImageFilesFromDataTransfer({
    items: [
      { kind: 'file' },
      { kind: 'string' },
    ],
    files: [
      createMockFile('fallback.jpg'),
      createMockFile('skip.txt'),
      createMockFile('fallback.webp'),
    ],
    types: ['Files'],
  })

  assert.deepEqual(files.map((file) => file.name), ['fallback.jpg', 'fallback.webp'])
}

console.log('File drop test passed')
