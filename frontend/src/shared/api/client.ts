export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public changed = false) {
    super(message);
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, options);
  let body: unknown;
  try { body = await response.json(); }
  catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new ApiError(`服务返回了无法解析的响应（HTTP ${response.status}）`, response.status);
  }
  if (!body || typeof body !== 'object' || !('ok' in body)) {
    throw new ApiError(`服务响应格式无效（HTTP ${response.status}）`, response.status);
  }
  if (!response.ok || body.ok !== true) {
    throw new ApiError('error' in body && typeof body.error === 'string' ? body.error : `请求失败（${response.status}）`, response.status,
      'code' in body && typeof body.code === 'string' ? body.code : undefined, 'changed' in body && body.changed === true);
  }
  if (!('data' in body)) throw new ApiError(`服务响应缺少数据（HTTP ${response.status}）`, response.status);
  return body.data as T;
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

export function queryString(values: Record<string, string | number | boolean | undefined | null>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  return params.toString();
}
