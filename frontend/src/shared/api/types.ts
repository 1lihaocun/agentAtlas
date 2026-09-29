export interface Asset {
  path: string; name: string; platform?: string; category?: string; project?: string;
  profile?: string; bytes: number; mtime?: number; editable?: boolean; searchable?: boolean;
  reason?: string; restricted?: boolean; worktree?: boolean; dup?: boolean; kind?: string;
  scope?: string; dir?: string; root?: string; proj?: string; projectPath?: string;
}

export interface Page<T> {
  items: T[]; total: number; page: number; pageSize: number; catalogVersion: string | null;
}

export interface FileDocument {
  path: string; content: string; sha256: string | null; version: string | null; editable: boolean;
  category: string; bytes: number; mtime: number; truncated: boolean; readOnlyReason: string;
}

export interface SaveResult {
  changed: boolean; sha256: string; version: string; bytes: number; backup?: string;
}

export interface Section {
  id: string; title: string; level: number; text: string; start_line: number; end_line: number;
}

export interface SectionDocument { path: string; version: string; editable: boolean; sections: Section[] }

export interface MemorySemantics {
  level: string; label: string; appliesTo: string[]; allProjects: boolean; reader: string;
  loading: { mode: string; label: string; trigger: string; portion: string };
  inheritance: string; observation: { label: string }; notes: string[];
  basis: { kind: string; label: string; detail: string; sources: { title: string; url: string }[] };
  pipeline?: { stage: string; label: string }; role?: { id: string; label: string };
}

export interface MemoryAsset extends Asset {
  semantics: MemorySemantics;
  memoryInfo?: { title: string; description: string; status: string; truncated: boolean;
    relatedProjects: { name: string; path: string; basis: string; evidence: { path: string; line: number } }[] };
  memoryStorage?: { directory: string; root: string; ancestors: string[]; relativePath: string };
}

export interface DirectoryNode {
  path: string; name: string; parent: string | null; children: string[]; ancestors: string[];
  directFiles: string[]; filePaths: string[]; displayPath: string; directCount: number; totalCount: number;
}

export interface StorageTree { roots: string[]; nodes: Record<string, DirectoryNode>; unplacedFiles: string[] }

export interface Guide {
  id: string; label: string; role: string; purpose: string; producer?: string; consumer?: string;
  loadingMechanism?: string; generationMechanism?: string; evidence?: string;
  path?: string; matchPatterns?: string[]; cautions?: string[]; reviewedAt?: string;
  sources: { label?: string; url?: string; kind?: string; path?: string; line?: number; checkedAt?: string }[];
  filename: { pattern?: string; original?: string; status: string;
    fields: { key: string; label: string; value?: string; meaning: string }[]; cautions?: string[] };
}

export interface GuideLibrary { schemaVersion: number; types: Guide[]; maintenancePath: string; reviewedAt: string; notice: string }
export interface FileContext { memory: MemoryAsset | null; fileGuide: Guide }

export interface InstructionTree {
  files: Asset[]; totalFiles: number; dupFiles: number; totalBytes: number; generatedAt: number;
  durationMs: number; scanErrors: number; projectCandidates: { path: string; status: string }[];
  dupGroups: string[][];
}

export interface SearchHit extends Asset { snippet: string; startLine: number; endLine: number; chunkEndLine: number; score: number; matchType: string }
export interface Settings { baseUrl: string; model: string; dimensions: number | null; batchSize: number; categories: string[]; apiKeyConfigured: boolean }
export interface Job { id?: string; running: boolean; stage: string; kind?: string; error: string | null; refreshQueued?: boolean; progress?: Record<string, number | string> }
export interface JobStatus { job: Job; index: Record<string, number>; catalogVersion: string; available: boolean }
