import { useCallback, useEffect, useState } from 'react'
import App from './App.jsx'
import { api } from './api.js'
import './SessionApp.css'

/**
 * Quản lý phiên làm việc, cổng xác thực và đổi mật khẩu bắt buộc
 * Khi chưa xác thực: hiển thị form đăng nhập sáng, tập trung.
 * Khi được yêu cầu đổi mật khẩu: hiển thị màn đổi mật khẩu bắt buộc.
 * Khi đã xác thực: chuyển tiếp vào App, không render thanh session-bar trùng lặp phía trên.
 */
export default function SessionApp() {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null) // { type: 'error' | 'success' | 'info', text: string }

  // Tải lại thông tin hồ sơ người dùng
  const loadProfile = useCallback(async () => {
    const body = await api('/profile/me/')
    setUser(body.data || body)
  }, [])

  // Kiểm tra phiên làm việc ngay khi mở ứng dụng
  useEffect(() => {
    let active = true
    api('/profile/me/')
      .then((body) => {
        if (active) setUser(body.data || body)
      })
      .catch((error) => {
        if (active && error.status !== 401) {
          setMessage({ type: 'error', text: error.message || 'Lỗi khi kiểm tra phiên làm việc.' })
        }
      })
      .finally(() => {
        if (active) setReady(true)
      })
    return () => {
      active = false
    }
  }, [])

  // Xử lý thực thi bất đồng bộ có kiểm soát trạng thái chờ
  async function perform(action) {
    setBusy(true)
    setMessage(null)
    try {
      await action()
    } catch (error) {
      setMessage({ type: 'error', text: error.message || 'Đã có lỗi xảy ra.' })
      if (error.status === 401) setUser(null)
    } finally {
      setBusy(false)
    }
  }

  // Tải lại hồ sơ hoặc đăng xuất nếu phiên hết hạn
  const loadProfileOrLogout = useCallback(async () => {
    try {
      await loadProfile()
    } catch {
      setUser(null)
    }
  }, [loadProfile])

  // Khi đổi mật khẩu thành công: Backend đã tự hủy session cũ;
  // chuyển thẳng về màn đăng nhập và giữ thông báo thành công, tránh gọi lại API logout gây lỗi.
  const handlePasswordChangedSuccess = useCallback((successText) => {
    setUser(null)
    setMessage({
      type: 'success',
      text: successText || 'Đã đổi mật khẩu thành công. Vui lòng đăng nhập lại bằng mật khẩu mới.',
    })
  }, [])

  // Xử lý đăng nhập
  function submitLogin(event) {
    event.preventDefault()
    const form = event.currentTarget
    const data = Object.fromEntries(new FormData(form))
    perform(async () => {
      await api('/auth/login/', { method: 'POST', data })
      await loadProfile()
    })
  }

  // Xử lý đổi mật khẩu bắt buộc (must_change_password)
  function submitMandatoryPassword(event) {
    event.preventDefault()
    const form = event.currentTarget
    const data = Object.fromEntries(new FormData(form))

    if (data.new_password !== data.confirm_password) {
      setMessage({ type: 'error', text: 'Mật khẩu xác nhận không khớp với mật khẩu mới.' })
      return
    }

    if (data.new_password.length < 12) {
      setMessage({ type: 'error', text: 'Mật khẩu mới phải có tối thiểu 12 ký tự.' })
      return
    }

    perform(async () => {
      await api('/profile/change-password/', {
        method: 'POST',
        data: {
          old_password: data.old_password,
          new_password: data.new_password,
        },
      })
      setUser(null)
      setMessage({
        type: 'success',
        text: 'Đã đổi mật khẩu thành công. Vui lòng đăng nhập lại với mật khẩu mới.',
      })
    })
  }

  // 1. Trạng thái kiểm tra phiên
  if (!ready) {
    return (
      <div className="session-screen">
        <div className="session-card session-loading-wrap">
          <div className="session-spinner" />
          <p>Đang kiểm tra phiên làm việc…</p>
        </div>
      </div>
    )
  }

  // 2. Màn hình đăng nhập khi chưa xác thực
  if (!user) {
    return (
      <main className="session-screen">
        <div className="session-card">
          <div className="session-header">
            <div className="session-brand-badge">IC</div>
            <h1>IoT Cabinet</h1>
            <p>Hệ thống giám sát và quản trị tủ IoT</p>
          </div>

          {message && (
            <div className={`session-notice ${message.type}`} role="status">
              {message.text}
            </div>
          )}

          <form onSubmit={submitLogin} className="session-form">
            <div className="session-field">
              <label htmlFor="login-username">Tên đăng nhập</label>
              <input
                id="login-username"
                name="username"
                type="text"
                autoComplete="username"
                required
                placeholder="Nhập tên đăng nhập..."
                autoFocus
              />
            </div>

            <div className="session-field">
              <label htmlFor="login-password">Mật khẩu</label>
              <input
                id="login-password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                placeholder="Nhập mật khẩu..."
              />
            </div>

            <button type="submit" className="btn session-btn-submit" disabled={busy}>
              {busy ? 'Đang xác thực…' : 'Đăng nhập'}
            </button>
          </form>
        </div>
      </main>
    )
  }

  // 3. Màn hình đổi mật khẩu bắt buộc
  if (user.must_change_password) {
    return (
      <main className="session-screen">
        <div className="session-card">
          <div className="session-header">
            <div className="session-brand-badge" style={{ backgroundColor: 'var(--warning)' }}>!</div>
            <h1>Đổi mật khẩu bắt buộc</h1>
            <p>Tài khoản của bạn cần đổi mật khẩu trước khi truy cập hệ thống</p>
          </div>

          {message && (
            <div className={`session-notice ${message.type}`} role="status">
              {message.text}
            </div>
          )}

          <form onSubmit={submitMandatoryPassword} className="session-form">
            <div className="session-field">
              <label htmlFor="mandatory-old-password">Mật khẩu hiện tại</label>
              <input
                id="mandatory-old-password"
                name="old_password"
                type="password"
                autoComplete="current-password"
                required
                placeholder="Nhập mật khẩu hiện tại..."
              />
            </div>

            <div className="session-field">
              <label htmlFor="mandatory-new-password">Mật khẩu mới (12–128 ký tự)</label>
              <input
                id="mandatory-new-password"
                name="new_password"
                type="password"
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
                required
                placeholder="Nhập mật khẩu mới..."
              />
              <small>Tối thiểu 12 ký tự gồm chữ và số</small>
            </div>

            <div className="session-field">
              <label htmlFor="mandatory-confirm-password">Xác nhận mật khẩu mới</label>
              <input
                id="mandatory-confirm-password"
                name="confirm_password"
                type="password"
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
                required
                placeholder="Nhập lại mật khẩu mới..."
              />
            </div>

            <button type="submit" className="btn session-btn-submit" disabled={busy}>
              {busy ? 'Đang lưu…' : 'Lưu mật khẩu và tiếp tục'}
            </button>
          </form>
        </div>
      </main>
    )
  }

  // 4. Vào ứng dụng chính (không render thanh session-bar trùng)
  return (
    <App
      sessionUser={user}
      onSessionEnded={loadProfileOrLogout}
      onUserUpdated={setUser}
      onPasswordChangedSuccess={handlePasswordChangedSuccess}
    />
  )
}
