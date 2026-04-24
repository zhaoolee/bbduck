const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp'])
const IMAGE_MIME_TYPES = new Set(['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'])

export function isImageFile(file) {
  if (!file) return false
  if (typeof file.type === 'string' && IMAGE_MIME_TYPES.has(file.type.toLowerCase())) return true
  const suffix = typeof file.name === 'string' ? file.name.split('.').pop()?.toLowerCase() : ''
  return IMAGE_EXTENSIONS.has(suffix ?? '')
}

export function extractImageFiles(fileList) {
  return Array.from(fileList ?? []).filter(isImageFile)
}

function readEntryFile(fileEntry) {
  return new Promise((resolve, reject) => {
    fileEntry.file(resolve, reject)
  })
}

function readEntryBatch(directoryReader) {
  return new Promise((resolve, reject) => {
    directoryReader.readEntries(resolve, reject)
  })
}

export async function readAllDirectoryEntries(directoryEntry) {
  const directoryReader = directoryEntry.createReader()
  const entries = []

  while (true) {
    const batch = await readEntryBatch(directoryReader)
    if (!batch || batch.length === 0) break
    entries.push(...batch)
  }

  return entries
}

async function readImageFilesFromEntry(entry) {
  if (!entry) return []

  if (entry.isFile) {
    const file = await readEntryFile(entry)
    return isImageFile(file) ? [file] : []
  }

  if (!entry.isDirectory) {
    return []
  }

  const entries = await readAllDirectoryEntries(entry)
  const files = []

  for (const childEntry of entries) {
    files.push(...await readImageFilesFromEntry(childEntry))
  }

  return files
}

function materializeDataTransferItem(item) {
  if (!item || item.kind !== 'file') return null

  const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null
  if (entry) {
    return { kind: 'entry', value: entry }
  }

  const file = typeof item.getAsFile === 'function' ? item.getAsFile() : null
  if (file) {
    return { kind: 'file', value: file }
  }

  return null
}

export async function extractImageFilesFromDataTransfer(dataTransfer) {
  const materializedItems = Array.from(dataTransfer?.items ?? [])
    .map(materializeDataTransferItem)
    .filter(Boolean)

  if (materializedItems.length === 0) {
    return extractImageFiles(dataTransfer?.files ?? [])
  }

  const files = []

  for (const item of materializedItems) {
    if (item.kind === 'entry') {
      files.push(...await readImageFilesFromEntry(item.value))
      continue
    }

    if (isImageFile(item.value)) {
      files.push(item.value)
    }
  }

  return files
}
