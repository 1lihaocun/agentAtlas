import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';

export interface Draft { text: string; original: string; version: string; selection: number; scroll: number; path: string; sectionId?: string }
interface Drafts { entries: Record<string, Draft>; initialize: (key: string, text: string, version: string, location?: { path: string; sectionId: string }) => void;
  update: (key: string, values: Partial<Draft>) => void; saved: (key: string, text: string, version: string) => void;
  discard: (key: string) => void }
const Context = createContext<Drafts | null>(null);
export function DraftProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<Record<string, Draft>>({});
  const initialize = useCallback((key: string, text: string, version: string, location?: { path: string; sectionId: string }) => setEntries(old => {
    if (old[key] && (old[key].text !== old[key].original || old[key].version === version)) return old;
    return { ...old, [key]: { text, original: text, version, selection: 0, scroll: 0, path: key, ...location } };
  }), []);
  const update = useCallback((key: string, values: Partial<Draft>) => setEntries(old => ({ ...old, [key]: { ...old[key], ...values } })), []);
  const saved = useCallback((key: string, text: string, version: string) => setEntries(old => {
    if (!old[key]) return old;
    return { ...old, [key]: { ...old[key], original: text, version } };
  }), []);
  const discard = useCallback((key: string) => setEntries(old => { const next = { ...old }; delete next[key]; return next; }), []);
  const dirty = Object.values(entries).some(draft => draft.text !== draft.original);
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
  return <Context.Provider value={{ entries, initialize, update, saved, discard }}>{children}</Context.Provider>;
}
export function useDrafts() { const value = useContext(Context); if (!value) throw new Error('编辑器缺少草稿容器'); return value; }
