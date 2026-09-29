import { useOutletContext, useSearchParams } from 'react-router';

export interface Actions {
  openFile: (path: string) => void;
  openGuide: (path: string) => void;
  notify: (message: string) => void;
}

export function useActions() { return useOutletContext<Actions>(); }

export function useFilters() {
  const [params, setParams] = useSearchParams();
  function update(values: Record<string, string | number | boolean | null>, replace = false) {
    setParams(current => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(values)) {
        if (value === null || value === '') next.delete(key);
        else next.set(key, String(value));
      }
      return next;
    }, { replace });
  }
  const rawPage = params.get('page') || '1';
  const rawSize = params.get('pageSize') || '50';
  const page = Number(rawPage), pageSize = Number(rawSize);
  if (!Number.isSafeInteger(page) || page < 1 || ![25, 50, 100].includes(pageSize)) {
    throw new Error('页码或每页数量无效，请修改地址参数。');
  }
  return { params, update, page, pageSize };
}
