import { useState } from 'react'
import { api, translateRole } from '../api.js'

/**
 * Trang Hồ sơ cá nhân & Đổi mật khẩu
 * Cho phép:
 * 1. Xem thông tin tài khoản hiện tại
 * 2. Cập nhật họ tên hiển thị qua API PATCH /profile/me/
 * 3. Đổi mật khẩu cá nhân qua API POST /profile/change-password/
 */
export default function ProfilePage({ user, onUserUpdated, onPasswordChangedSuccess }) {
  // Trạng thái cập nhật họ tên
  const [fullName, setFullName] = useState(user?.full_name || '')
  const [savingProfile, setSavingProfile] = useState(false)
  const [profileFeedback, setProfileFeedback] = useState(null)

  // Trạng thái đổi mật khẩu
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [savingPassword, setSavingPassword] = useState(false)
  const [passwordFeedback, setPasswordFeedback] = useState(null)

  // Xử lý lưu họ tên mới
  async function handleProfileSubmit(event) {
    event.preventDefault()
    setSavingProfile(true)
    setProfileFeedback(null)

    try {
      const trimmedName = fullName.trim()
      const response = await api('/profile/me/', {
        method: 'PATCH',
        data: { full_name: trimmedName },
      })
      const updatedUser = response.data || response
      if (onUserUpdated) onUserUpdated(updatedUser)
      setProfileFeedback({
        type: 'success',
        message: 'Đã cập nhật họ và tên thành công.',
      })
    } catch (error) {
      setProfileFeedback({
        type: 'error',
        message: error.message || 'Không thể cập nhật hồ sơ.',
      })
    } finally {
      setSavingProfile(false)
    }
  }

  // Xử lý đổi mật khẩu
  async function handlePasswordSubmit(event) {
    event.preventDefault()
    setPasswordFeedback(null)

    if (newPassword.length < 12) {
      setPasswordFeedback({
        type: 'error',
        message: 'Mật khẩu mới phải có ít nhất 12 ký tự.',
      })
      return
    }

    if (newPassword !== confirmPassword) {
      setPasswordFeedback({
        type: 'error',
        message: 'Mật khẩu xác nhận không khớp với mật khẩu mới.',
      })
      return
    }

    setSavingPassword(true)
    try {
      await api('/profile/change-password/', {
        method: 'POST',
        data: {
          old_password: oldPassword,
          new_password: newPassword,
        },
      })

      setOldPassword('')
      setNewPassword('')
      setConfirmPassword('')

      // Backend đã thu hồi toàn bộ session của tài khoản này;
      // Chuyển thẳng về màn hình đăng nhập kèm thông báo thành công, tránh gọi lại API logout gây lỗi.
      if (onPasswordChangedSuccess) {
        onPasswordChangedSuccess(
          'Đã đổi mật khẩu thành công! Vui lòng đăng nhập lại bằng mật khẩu mới.'
        )
      }
    } catch (error) {
      setPasswordFeedback({
        type: 'error',
        message: error.message || 'Không thể đổi mật khẩu.',
      })
    } finally {
      setSavingPassword(false)
    }
  }

  return (
    <div className="profile-page-grid">
      {/* Thẻ 1: Thông tin tài khoản */}
      <section className="card profile-card" aria-labelledby="profile-info-heading">
        <div className="card-heading">
          <div>
            <h2 id="profile-info-heading">Thông tin tài khoản</h2>
            <p>Xem thông tin đăng nhập và cập nhật họ tên người dùng</p>
          </div>
        </div>

        {profileFeedback && (
          <div className={`notice-box ${profileFeedback.type}`} role="status">
            <span>{profileFeedback.type === 'success' ? '✓' : '⚠'}</span>
            <span>{profileFeedback.message}</span>
          </div>
        )}

        <form onSubmit={handleProfileSubmit} className="profile-form">
          <div className="form-field">
            <label htmlFor="input-username">Tên đăng nhập</label>
            <input
              id="input-username"
              type="text"
              value={user?.username || ''}
              disabled
              readOnly
              title="Tên đăng nhập không thể thay đổi"
            />
            <small className="field-hint">Tên đăng nhập do Quản trị viên khởi tạo và không thể sửa đổi.</small>
          </div>

          <div className="form-field">
            <label htmlFor="input-fullname">Họ và tên</label>
            <input
              id="input-fullname"
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Nhập họ và tên hiển thị..."
            />
          </div>

          <div className="form-field">
            <label htmlFor="input-email">Địa chỉ Email</label>
            <input
              id="input-email"
              type="email"
              value={user?.email || 'Chưa thiết lập'}
              disabled
              readOnly
            />
          </div>

          <div className="form-field">
            <label>Vai trò hệ thống</label>
            <div>
              <span className="role-pill primary">
                {translateRole(user?.role)}
              </span>
            </div>
          </div>

          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={savingProfile || fullName.trim() === (user?.full_name || '').trim()}
            >
              {savingProfile ? 'Đang lưu…' : 'Lưu thay đổi'}
            </button>
          </div>
        </form>
      </section>

      {/* Thẻ 2: Đổi mật khẩu cá nhân */}
      <section className="card profile-card" aria-labelledby="profile-password-heading">
        <div className="card-heading">
          <div>
            <h2 id="profile-password-heading">Bảo mật & Đổi mật khẩu</h2>
            <p>Mật khẩu an toàn cần tối thiểu 12 ký tự, bao gồm chữ và số</p>
          </div>
        </div>

        {passwordFeedback && (
          <div className={`notice-box ${passwordFeedback.type}`} role="status">
            <span>{passwordFeedback.type === 'success' ? '✓' : '⚠'}</span>
            <span>{passwordFeedback.message}</span>
          </div>
        )}

        <form onSubmit={handlePasswordSubmit} className="profile-form">
          <div className="form-field">
            <label htmlFor="input-old-password">Mật khẩu hiện tại</label>
            <input
              id="input-old-password"
              type="password"
              required
              autoComplete="current-password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              placeholder="Nhập mật khẩu đang dùng..."
            />
          </div>

          <div className="form-field">
            <label htmlFor="input-new-password">Mật khẩu mới (12–128 ký tự)</label>
            <input
              id="input-new-password"
              type="password"
              required
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Nhập mật khẩu mới..."
            />
            <small className="field-hint">Độ dài tối thiểu 12 ký tự để đảm bảo chính sách bảo mật.</small>
          </div>

          <div className="form-field">
            <label htmlFor="input-confirm-password">Xác nhận mật khẩu mới</label>
            <input
              id="input-confirm-password"
              type="password"
              required
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Nhập lại mật khẩu mới..."
            />
          </div>

          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={savingPassword || !oldPassword || !newPassword || !confirmPassword}
            >
              {savingPassword ? 'Đang cập nhật…' : 'Cập nhật mật khẩu'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
