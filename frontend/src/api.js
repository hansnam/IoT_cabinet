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

/**
 * Chuyển đổi mã vai trò kỹ thuật sang tên tiếng Việt hiển thị
 * @param {string} role - Mã vai trò (ADMIN, OPERATOR, VIEWER)
 * @returns {string} Tên vai trò tiếng Việt
 */
export function translateRole(role) {
  switch (String(role || '').toUpperCase()) {
    case 'ADMIN':
      return 'Quản trị viên'
    case 'OPERATOR':
      return 'Người vận hành'
    case 'VIEWER':
      return 'Người xem'
    default:
      return role || 'Người dùng'
  }
}

/**
 * Chuyển đổi trạng thái kết nối sang tiếng Việt hiển thị
 * @param {boolean|null|undefined} isOnline - Cờ kết nối thiết bị
 * @param {string} fallbackState - Trạng thái chuỗi kỹ thuật
 * @returns {{ label: string, statusClass: string }}
 */
export function translateConnectionState(isOnline, fallbackState) {
  if (isOnline === true) {
    return { label: 'Đang kết nối', statusClass: 'online' }
  }
  if (isOnline === false) {
    return { label: 'Mất kết nối', statusClass: 'offline' }
  }
  const normalized = String(fallbackState || '').toLowerCase()
  if (normalized === 'online' || normalized === 'connected') {
    return { label: 'Đang kết nối', statusClass: 'online' }
  }
  return { label: 'Mất kết nối', statusClass: 'offline' }
}

/**
 * Chuyển đổi trạng thái phát lệnh sang tiếng Việt
 * @param {string} status - Trạng thái kỹ thuật (PENDING, SENT, ACKED, APPLIED, APPLIED_LATE, FAILED, TIMEOUT, CANCELLED)
 * @returns {string} Tên trạng thái tiếng Việt
 */
export function translateCommandStatus(status) {
  switch (String(status || '').toUpperCase()) {
    case 'PENDING':
      return 'Chờ gửi'
    case 'SENT':
      return 'Đã gửi tới máy chủ'
    case 'ACKED':
    case 'APPLIED':
      return 'Đã thực hiện'
    case 'APPLIED_LATE':
      return 'Thực hiện trễ'
    case 'TIMEOUT':
      return 'Chưa nhận phản hồi'
    case 'FAILED':
      return 'Thất bại'
    case 'CANCELLED':
      return 'Đã hủy'
    default:
      return status || '--'
  }
}

/**
 * Chuyển đổi loại lệnh kỹ thuật sang tiếng Việt
 * @param {string} commandType - Loại lệnh (SET_FAN, SET_MODE, APPLY_CONFIG)
 * @returns {string} Tên lệnh tiếng Việt
 */
export function translateCommandType(commandType) {
  switch (String(commandType || '').toUpperCase()) {
    case 'SET_FAN':
      return 'Bật/Tắt quạt'
    case 'SET_MODE':
      return 'Chuyển chế độ quạt'
    case 'APPLY_CONFIG':
      return 'Áp dụng cấu hình'
    default:
      return commandType || 'Lệnh điều khiển'
  }
}
