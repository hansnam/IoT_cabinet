const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'
let csrfToken = ''

// Cookie phiên do trình duyệt quản lý; token CSRF chỉ giữ trong bộ nhớ.
export async function api(path, { method = 'GET', data } = {}) {
  const headers = { Accept: 'application/json' }
  if (method !== 'GET') {
    if (!csrfToken) csrfToken = (await api('/auth/csrf/')).csrf_token
    headers['X-CSRFToken'] = csrfToken
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${base}${path}`, {
    method, credentials: 'include', headers,
    body: data === undefined ? undefined : JSON.stringify(data),
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(body.error?.message || body.detail || `Lỗi HTTP ${response.status}`)
    error.status = response.status
    throw error
  }
  // Django xoay CSRF sau đăng nhập; lần POST sau phải lấy token mới.
  if (path.includes('/login/') || path.includes('/logout/') || path.includes('/change-password/')) csrfToken = ''
  return body
}

export function asList(body) {
  return Array.isArray(body) ? body : body.results || body.data || []
}
