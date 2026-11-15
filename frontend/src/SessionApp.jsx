import { useCallback, useEffect, useState } from 'react'
import App from './App.jsx'
import { api } from './api.js'
import './SessionApp.css'

// Bao dashboard bằng phiên đăng nhập; không hiển thị dữ liệu trước khi xác thực.
export default function SessionApp() {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [passwordForm, setPasswordForm] = useState(false)

  const loadProfile = useCallback(async () => {
    const body = await api('/profile/me/')
    setUser(body.data || body)
  }, [])

  useEffect(() => {
    let active = true
    api('/profile/me/').then(body => {
      if (active) setUser(body.data || body)
    }).catch(error => {
      if (active && error.status !== 401) setMessage(error.message)
    }).finally(() => { if (active) setReady(true) })
    return () => { active = false }
  }, [])

  async function perform(action) {
    setBusy(true); setMessage('')
    try { await action() } catch (error) {
      setMessage(error.message)
      if (error.status === 401) setUser(null)
    } finally { setBusy(false) }
  }

  const loadProfileOrLogout = useCallback(async () => {
    try { await loadProfile() } catch { setUser(null) }
  }, [loadProfile])

  function submitLogin(event) {
    event.preventDefault()
    const data = Object.fromEntries(new FormData(event.currentTarget))
    perform(async () => { await api('/auth/login/', { method: 'POST', data }); await loadProfile() })
  }

  function submitPassword(event) {
    event.preventDefault()
    const data = Object.fromEntries(new FormData(event.currentTarget))
    perform(async () => {
      await api('/profile/change-password/', { method: 'POST', data })
      setUser(null); setPasswordForm(false)
      setMessage('Đã đổi mật khẩu. Hãy đăng nhập lại.')
    })
  }

  if (!ready) return <p className="session-panel">Đang kiểm tra phiên…</p>
  if (!user) return <main className="session-panel">
    <h1>IoT Cabinet</h1><h2>Đăng nhập</h2>
    <form onSubmit={submitLogin}>
      <label>Tên đăng nhập<input name="username" autoComplete="username" required /></label>
      <label>Mật khẩu<input name="password" type="password" autoComplete="current-password" required /></label>
      <button disabled={busy}>Đăng nhập</button>
    </form><p role="status">{message}</p>
  </main>

  return <>
    <section className="session-bar">
      <strong>{user.full_name} · {user.role}</strong>
      <button onClick={() => setPasswordForm(!passwordForm)}>Đổi mật khẩu</button>
      {user.role === 'ADMIN' && <a href="/admin/">Quản trị Django</a>}
      <button disabled={busy} onClick={() => perform(async () => {
        await api('/auth/logout/', { method: 'POST' }); setUser(null)
      })}>Đăng xuất</button>
      <p role="status">{message}</p>
    </section>
    {(passwordForm || user.must_change_password) && <form className="session-panel" onSubmit={submitPassword}>
      <h2>Đổi mật khẩu {user.must_change_password && '(bắt buộc)'}</h2>
      <label>Mật khẩu hiện tại<input type="password" name="old_password" autoComplete="current-password" required /></label>
      <label>Mật khẩu mới (12–128 ký tự)<input type="password" name="new_password" minLength={12} maxLength={128} autoComplete="new-password" required /></label>
      <button disabled={busy}>Lưu mật khẩu</button>
    </form>}
    {!user.must_change_password && <App sessionUser={user} onSessionEnded={loadProfileOrLogout} />}
  </>
}
