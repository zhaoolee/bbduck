export type FileLike = Pick<File, 'name' | 'type'>

export type FileSystemEntryLike = {
  isFile: boolean
  isDirectory: boolean
}

export type FileSystemFileEntryLike = FileSystemEntryLike & {
  file(successCallback: (file: File) => void, errorCallback?: (error: unknown) => void): void
}

export type FileSystemDirectoryReaderLike = {
  readEntries(
    successCallback: (entries: FileSystemEntryLike[]) => void,
    errorCallback?: (error: unknown) => void,
  ): void
}

export type FileSystemDirectoryEntryLike = FileSystemEntryLike & {
  createReader(): FileSystemDirectoryReaderLike
}

export function isImageFile(file: FileLike | null | undefined): boolean
export function extractImageFiles(fileList: FileList | File[]): File[]
export function readAllDirectoryEntries(directoryEntry: FileSystemDirectoryEntryLike): Promise<FileSystemEntryLike[]>
export function extractImageFilesFromDataTransfer(dataTransfer: DataTransfer | null | undefined): Promise<File[]>
