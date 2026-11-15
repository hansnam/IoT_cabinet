import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { api, asList } from './api.js'
import AppLayout from './components/AppLayout.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import CommandsPage from './pages/CommandsPage.jsx'
import DeviceDetailPage from './pages/DeviceDetailPage.jsx'
import DevicesPage from './pages/DevicesPage.jsx'
import OverviewPage from './pages/OverviewPage.jsx'
import ProfilePage from './pages/ProfilePage.jsx'
import AccountsPage from './pages/AccountsPage.jsx'

/**
 * Thành phần chính App của giao diện
 * Tích hợp hệ thống định tuyến React Router:
 * - / : Trang Tổng quan (OverviewPage)
 * - /devices : Danh sách tủ mạng (DevicesPage)
 * - /devices/:id : Chi tiết & Vận hành tủ (DeviceDetailPage)
 * - /alerts : Quản lý cảnh báo (AlertsPage)
 * - /commands : Lịch sử phát lệnh (CommandsPage)
 * - /profile : Hồ sơ cá nhân & Bảo mật (ProfilePage)
 */
function App({ sessionUser, onSessionEnded, onUserUpdated, onPasswordChangedSuccess }) {
  const [user, setUser] = useState(sessionUser)
  const [openAlertsCount, setOpenAlertsCount] = useState(0)
  const [commandsCount, setCommandsCount] = useState(0)
  const [systemStatus, setSystemStatus] = useState('loading') // 'connected' | 'loading' | 'error'
  const [lastUpdated, setLastUpdated] = useState('')
  const [toast, setToast] = useState(null)

  const showToast = (message, type = 'info') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3500)
  }

  // Tải trạng thái và số lượng huy hiệu cho header và sidebar
  const refreshGlobalState = useCallback(async () => {
    try {
      const [alertBody, commandBody, profileBody] = await Promise.all([
        api('/alerts/'),
        api('/commands/'),
        api('/auth/me/'),
      ])

      const alertList = asList(alertBody)
      const unclosedAlerts = alertList.filter(
        (a) => !['resolved', 'closed'].includes(String(a.status || '').toLowerCase())
      )
      setOpenAlertsCount(unclosedAlerts.length)

      const cmdList = asList(commandBody)
      setCommandsCount(cmdList.length)

      const currentUser = profileBody.data || profileBody.user || profileBody
      setUser(currentUser)
      if (onUserUpdated) onUserUpdated(currentUser)

      setSystemStatus('connected')
      setLastUpdated(new Date().toLocaleTimeString('vi-VN'))

      if ((profileBody.data || profileBody).must_change_password) {
        onSessionEnded()
      }
    } catch (err) {
      setSystemStatus('error')
      if (err.status === 401) onSessionEnded()
    }
  }, [onSessionEnded, onUserUpdated])

  // Polling nhẹ định kỳ mỗi 5 giây cho số lượng cảnh báo/lệnh
  useEffect(() => {
    let active = true
    let timer

    async function poll() {
      await refreshGlobalState()
      if (active) timer = setTimeout(poll, 5000)
    }

    timer = setTimeout(poll, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [refreshGlobalState])

  // Xử lý Đăng xuất
  const handleLogout = async () => {
    try {
      await api('/auth/logout/', { method: 'POST' })
      setUser(null)
      onSessionEnded()
      showToast('Đã đăng xuất thành công.', 'info')
    } catch (err) {
      showToast('Lỗi khi đăng xuất: ' + err.message, 'error')
    }
  }

  return (
    <AppLayout
      user={user}
      onLogout={handleLogout}
      openAlertsCount={openAlertsCount}
      commandsCount={commandsCount}
      lastUpdated={lastUpdated}
      systemStatus={systemStatus}
      onRetry={refreshGlobalState}
    >
      {/* Toast thông báo nổi */}
      {toast && (
        <div className="toast-container" role="status">
          <div className={`toast ${toast.type}`}>
            <span>{toast.type === 'success' ? '✓' : toast.type === 'error' ? '⚠️' : 'ℹ️'}</span>
            <span>{toast.message}</span>
          </div>
        </div>
      )}

      {/* Điều hướng các trang chức năng độc lập */}
      <Routes>
        <Route path="/" element={<OverviewPage showToast={showToast} />} />
        <Route path="/devices" element={<DevicesPage showToast={showToast} />} />
        <Route path="/devices/:id" element={<DeviceDetailPage showToast={showToast} />} />
        <Route path="/alerts" element={<AlertsPage showToast={showToast} />} />
        <Route path="/commands" element={<CommandsPage showToast={showToast} />} />
        <Route
          path="/accounts"
          element={
            user?.role === 'ADMIN' ? (
              <AccountsPage showToast={showToast} />
            ) : (
              <Navigate to="/" replace />
            )
          }
        />
        <Route
          path="/profile"
          element={
            <ProfilePage
              user={user}
              onUserUpdated={(updatedUser) => {
                setUser(updatedUser)
                if (onUserUpdated) onUserUpdated(updatedUser)
              }}
              onPasswordChangedSuccess={onPasswordChangedSuccess}
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  )
}

export default App
