import { api, type Entity } from './api'

export const KNOWLEDGE_FILE_ACCEPT = '.pdf,.docx,.pptx,.txt,.md,.csv,.json,.html,.htm'

const supportedExtensions = new Set(KNOWLEDGE_FILE_ACCEPT.split(','))

export interface KnowledgeFileSelection {
  files: File[]
  skipped: File[]
  rootName: string
}

export interface KnowledgeImportProgress {
  total: number
  completed: number
  imported: number
  duplicates: number
  failed: number
  skipped: number
  current: string
}

export interface KnowledgeImportResult extends KnowledgeImportProgress {
  last?: Entity
  errors: string[]
}

function extensionOf(file: File): string {
  const dot = file.name.lastIndexOf('.')
  return dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
}

export function selectKnowledgeFiles(fileList: FileList | File[]): KnowledgeFileSelection {
  const all = Array.from(fileList)
  const files = all.filter(file => supportedExtensions.has(extensionOf(file)))
  const skipped = all.filter(file => !supportedExtensions.has(extensionOf(file)))
  const firstRelativePath = files[0]?.webkitRelativePath || all[0]?.webkitRelativePath || ''
  return {
    files,
    skipped,
    rootName: firstRelativePath.split('/')[0] || '',
  }
}

export async function importKnowledgeFiles(
  knowledgeBaseId: string,
  selection: KnowledgeFileSelection,
  onProgress?: (progress: KnowledgeImportProgress) => void,
): Promise<KnowledgeImportResult> {
  const progress: KnowledgeImportResult = {
    total: selection.files.length,
    completed: 0,
    imported: 0,
    duplicates: 0,
    failed: 0,
    skipped: selection.skipped.length,
    current: '',
    errors: [],
  }
  onProgress?.({ ...progress })
  for (const file of selection.files) {
    const relativePath = file.webkitRelativePath || file.name
    progress.current = relativePath
    onProgress?.({ ...progress })
    try {
      const item = await api.upload<Entity>(
        `/knowledge-bases/${knowledgeBaseId}/documents/upload`,
        file,
        { relative_path: relativePath },
      )
      progress.last = item
      if (item.ingestion?.duplicate) progress.duplicates += 1
      else progress.imported += 1
    } catch (error: any) {
      progress.failed += 1
      progress.errors.push(`${relativePath}：${error.message || '导入失败'}`)
    }
    progress.completed += 1
    onProgress?.({ ...progress })
  }
  progress.current = ''
  onProgress?.({ ...progress })
  return progress
}
