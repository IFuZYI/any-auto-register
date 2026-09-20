export const API = '/api'
export const API_BASE = '/api'

export function getToken(): string {
  return localStorage.getItem('auth_token') || ''
}

export function setToken(token: string): void {
  localStorage.setItem('auth_token', token)
}

export function clearToken(): void {
  localStorage.removeItem('auth_token')
}

// 后端只在"面板登录态失效"时带上这个头。业务接口也可能回 401（比如上游服务不认
// 你提交的凭据），那种情况只该把错误显示出来，不能把人踢回登录页。
export const PANEL_AUTH_HEADER = 'X-Panel-Auth-Required'

// 批量操作走前端分片：把账号切成小批、逐批请求，每批都稳稳落在 Cloudflare
// 约 100s 的超时窗口内。10 个/批是保守值——最贵的动作（补 RT/回填/CPA 上传）
// 每个账号也就几秒外呼，10 个撑死几十秒，离 100s 还有富余。
export const BATCH_CHUNK_SIZE = 10

export function chunkList<T>(list: T[], size: number = BATCH_CHUNK_SIZE): T[][] {
  if (size <= 0) return [list]
  const chunks: T[][] = []
  for (let i = 0; i < list.length; i += size) {
    chunks.push(list.slice(i, i + size))
  }
  return chunks
}

export interface AccountFilter {
  platform?: string
  status?: string
  email?: string
  plus_status?: string
  created_at_start?: string
  created_at_end?: string
}

interface AccountIdsResponse {
  total: number
  ids: number[]
}

// 拉取"当前筛选"命中的全部账号 ID。只读接口、不发外呼，不会超时。
// 前端拿到完整 ID 列表后再自己切片，就能把"全部/筛选"这种大批量操作分片跑。
export async function fetchAccountIds(filter: AccountFilter = {}): Promise<number[]> {
  const params = new URLSearchParams()
  if (filter.platform) params.set('platform', filter.platform)
  if (filter.status) params.set('status', filter.status)
  if (filter.email) params.set('email', filter.email)
  if (filter.plus_status) params.set('plus_status', filter.plus_status)
  if (filter.created_at_start) params.set('created_at_start', filter.created_at_start)
  if (filter.created_at_end) params.set('created_at_end', filter.created_at_end)
  const qs = params.toString()
  const data = (await apiFetch(`/accounts/ids${qs ? `?${qs}` : ''}`)) as AccountIdsResponse
  return (data.ids || []).filter((id) => Number.isInteger(id) && id > 0)
}

export async function apiFetch(path: string, opts?: RequestInit) {
  const token = getToken()
  const baseHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) baseHeaders['Authorization'] = `Bearer ${token}`
  const res = await fetch(API + path, {
    ...opts,
    headers: { ...baseHeaders, ...(opts?.headers as Record<string, string> || {}) },
  })
  if (res.status === 401 && res.headers.get(PANEL_AUTH_HEADER)) {
    clearToken()
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('未认证，请重新登录')
  }
  if (!res.ok) {
    const text = await res.text()
    try {
      const json = JSON.parse(text)
      throw new Error(json.detail || text)
    } catch (e) {
      if (e instanceof SyntaxError) throw new Error(text)
      throw e
    }
  }
  return res.json()
}
