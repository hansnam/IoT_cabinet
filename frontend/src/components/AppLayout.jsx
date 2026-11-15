import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { translateRole } from '../api.js'

/**
 * Xác định tiêu đề trang và breadcrumb tự động theo đường dẫn URL
 * @param {string} pathname - Đường dẫn hiện tại
 * @returns {{ title: string, breadcrumb: string }}
 */
function getRouteMetadata(pathname) {
  if (pathname === '/') {
    return { title: 'Tổng quan giám sát', breadcrumb: 'Hệ thống / Tổng quan' }
  }
  if (pathname === '/devices') {
    return { title: 'Danh sách tủ mạng', breadcrumb: 'Hệ thống / Thiết bị' }
  }
  if (pathname.startsWith('/devices/')) {
    return { title: 'Chi tiết & Vận hành tủ', breadcrumb: 'Hệ thống / Thiết bị / Chi tiết' }
  }
  if (pathname === '/alerts') {
    return { title: 'Quản lý cảnh báo', breadcrumb: 'Hệ thống / Cảnh báo' }
  }
  if (pathname === '/commands') {
    return { title: 'Lịch sử phát lệnh', breadcrumb: 'Hệ thống / Lịch sử lệnh' }
  }
  if (pathname === '/accounts') {
    return { title: 'Tài khoản & Phân quyền', breadcrumb: 'Quản trị / Tài khoản' }
  }
  if (pathname === '/mqtt-settings') {
    return { title: 'Cấu hình Broker MQTT', breadcrumb: 'Quản trị / Cấu hình Broker' }
  }
  if (pathname === '/profile') {
    return { title: 'Hồ sơ tài khoản & Bảo mật', breadcrumb: 'Tài khoản / Hồ sơ' }
  }
  return { title: 'Giám sát hệ thống', breadcrumb: 'Hệ thống' }
}

/**
 * Bố cục chính của ứng dụng giao diện desktop sáng (Light Theme)
 * Tích hợp điều hướng React Router, thanh đầu trang và menu người dùng duy nhất.
 */
export default function AppLayout({
  user,
  onLogout,
  openAlertsCount = 0,
  commandsCount = 0,
  lastUpdated,
  systemStatus = 'connected', // 'connected' | 'loading' | 'error'
  onRetry,
  children,
}) {
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  const menuRef = useRef(null)
  const buttonRef = useRef(null)
  const location = useLocation()
  const navigate = useNavigate()

  const { title, breadcrumb } = getRouteMetadata(location.pathname)

  // Đóng menu tài khoản khi nhấn ra ngoài hoặc nhấn phím Escape
  useEffect(() => {
    function handleClickOutside(event) {
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target)
      ) {
        setAccountMenuOpen(false)
      }
    }

    function handleKeyDown(event) {
      if (event.key === 'Escape' && accountMenuOpen) {
        setAccountMenuOpen(false)
        if (buttonRef.current) buttonRef.current.focus()
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [accountMenuOpen])

  const userRoleText = translateRole(user?.role)
  const userDisplayName = user?.full_name || user?.username || 'Tài khoản'

  return (
    <div className="app-layout">
      {/* 1. Sidebar điều hướng sáng — 224px */}
      <aside className="app-sidebar" aria-label="Thanh điều hướng chính">
        <div className="sidebar-brand">
          <div className="brand-badge">IC</div>
          <div className="brand-text">
            <h2>IoT Cabinet</h2>
            <p>Hệ thống giám sát tủ</p>
          </div>
        </div>

        <nav className="sidebar-nav">
          <p className="nav-group-label">GIÁM SÁT & VẬN HÀNH</p>

          <NavLink
            to="/"
            end
            className={({ isActive }) => `nav-button ${isActive ? 'active' : ''}`}
          >
            <span className="nav-icon" aria-hidden="true">📊</span>
            <span className="nav-text">Tổng quan</span>
          </NavLink>

          <NavLink
            to="/devices"
            className={({ isActive }) =>
              `nav-button ${isActive || location.pathname.startsWith('/devices/') ? 'active' : ''}`
            }
          >
            <span className="nav-icon" aria-hidden="true">🗄️</span>
            <span className="nav-text">Thiết bị</span>
          </NavLink>

          <NavLink
            to="/alerts"
            className={({ isActive }) => `nav-button ${isActive ? 'active' : ''}`}
          >
            <span className="nav-icon" aria-hidden="true">⚠️</span>
            <span className="nav-text">Cảnh báo</span>
            {openAlertsCount > 0 && (
              <span className="nav-badge alert-badge" title="Cảnh báo cần xử lý">
                {openAlertsCount}
              </span>
            )}
          </NavLink>

          <NavLink
            to="/commands"
            className={({ isActive }) => `nav-button ${isActive ? 'active' : ''}`}
          >
            <span className="nav-icon" aria-hidden="true">⏱️</span>
            <span className="nav-text">Lịch sử lệnh</span>
            {commandsCount > 0 && (
              <span className="nav-badge count-badge">
                {commandsCount}
              </span>
            )}
          </NavLink>

          {user?.role === 'ADMIN' && (
            <>
              <p className="nav-group-label" style={{ marginTop: '20px' }}>QUẢN TRỊ</p>

              <NavLink
                to="/accounts"
                className={({ isActive }) => `nav-button ${isActive ? 'active' : ''}`}
              >
                <span className="nav-icon" aria-hidden="true">👥</span>
                <span className="nav-text">Tài khoản & Phân quyền</span>
              </NavLink>

              <NavLink
                to="/mqtt-settings"
                className={({ isActive }) => `nav-button ${isActive ? 'active' : ''}`}
              >
                <span className="nav-icon" aria-hidden="true">🔌</span>
                <span className="nav-text">Cấu hình Broker MQTT</span>
              </NavLink>
            </>
          )}

          <p className="nav-group-label" style={{ marginTop: '20px' }}>TÀI KHOẢN</p>

          <NavLink
            to="/profile"
            className={({ isActive }) => `nav-button ${isActive ? 'active' : ''}`}
          >
            <span className="nav-icon" aria-hidden="true">👤</span>
            <span className="nav-text">Hồ sơ cá nhân</span>
          </NavLink>
        </nav>

        <div className="sidebar-footer">
          <div className="system-health">
            <span className={`health-dot ${systemStatus}`} aria-hidden="true" />
            <span className="health-text">
              {systemStatus === 'error'
                ? 'Mất kết nối máy chủ'
                : systemStatus === 'loading'
                ? 'Đang kiểm tra kết nối…'
                : 'Hệ thống sẵn sàng'}
            </span>
          </div>
          <small className="system-version">Phiên bản v1.4</small>
        </div>
      </aside>

      {/* 2. Vùng nội dung chính */}
      <div className="app-main-wrapper">
        {/* Thanh đầu trang thống nhất */}
        <header className="app-topbar">
          <div className="topbar-left">
            <span className="topbar-breadcrumb">{breadcrumb}</span>
            <h1 className="topbar-title">{title}</h1>
          </div>

          <div className="topbar-right">
            {/* Tình trạng dữ liệu & thời điểm cập nhật thật */}
            <div className={`data-status-chip ${systemStatus}`}>
              <span className={`status-dot ${systemStatus}`} aria-hidden="true" />
              <span className="data-status-text">
                {systemStatus === 'error'
                  ? 'Mất kết nối'
                  : systemStatus === 'loading'
                  ? 'Đang cập nhật…'
                  : 'Đã cập nhật'}
              </span>
              {systemStatus === 'connected' && lastUpdated && (
                <span className="data-timestamp">({lastUpdated})</span>
              )}
              {systemStatus === 'error' && onRetry && (
                <button
                  type="button"
                  className="btn-retry-chip"
                  onClick={onRetry}
                  title="Thử kết nối lại"
                >
                  Thử lại
                </button>
              )}
            </div>

            {/* Menu tài khoản người dùng duy nhất */}
            <div className="account-dropdown-wrapper">
              <button
                ref={buttonRef}
                type="button"
                className={`account-trigger-btn ${accountMenuOpen ? 'active' : ''}`}
                onClick={() => setAccountMenuOpen(!accountMenuOpen)}
                aria-expanded={accountMenuOpen}
                aria-haspopup="true"
                aria-label="Menu tài khoản"
              >
                <div className="user-avatar" aria-hidden="true">
                  {userDisplayName.charAt(0).toUpperCase()}
                </div>
                <div className="account-trigger-info">
                  <span className="account-name">{userDisplayName}</span>
                  <span className="account-role-tag">{userRoleText}</span>
                </div>
                <span className="dropdown-caret" aria-hidden="true">▾</span>
              </button>

              {accountMenuOpen && (
                <div
                  ref={menuRef}
                  className="account-menu-popover"
                  role="menu"
                  aria-label="Tùy chọn tài khoản"
                >
                  <div className="menu-user-header">
                    <strong>{userDisplayName}</strong>
                    <span className="menu-username">@{user?.username}</span>
                    <span className="menu-role-badge">{userRoleText}</span>
                  </div>

                  <hr className="menu-divider" />

                  <button
                    type="button"
                    className="menu-item-btn"
                    role="menuitem"
                    onClick={() => {
                      setAccountMenuOpen(false)
                      navigate('/profile')
                    }}
                  >
                    <span className="menu-item-icon" aria-hidden="true">👤</span>
                    <span className="menu-item-text">Hồ sơ & Đổi mật khẩu</span>
                  </button>

                  {user?.role === 'ADMIN' && (
                    <button
                      type="button"
                      className="menu-item-btn"
                      role="menuitem"
                      onClick={() => {
                        setAccountMenuOpen(false)
                        navigate('/accounts')
                      }}
                    >
                      <span className="menu-item-icon" aria-hidden="true">👥</span>
                      <span className="menu-item-text">Tài khoản & Phân quyền</span>
                    </button>
                  )}

                  {user?.role === 'ADMIN' && (
                    <button
                      type="button"
                      className="menu-item-btn"
                      role="menuitem"
                      onClick={() => {
                        setAccountMenuOpen(false)
                        navigate('/mqtt-settings')
                      }}
                    >
                      <span className="menu-item-icon" aria-hidden="true">🔌</span>
                      <span className="menu-item-text">Cấu hình Broker MQTT</span>
                    </button>
                  )}

                  {user?.role === 'ADMIN' && (
                    <a
                      href="/admin/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="menu-item-link"
                      role="menuitem"
                      onClick={() => setAccountMenuOpen(false)}
                    >
                      <span className="menu-item-icon" aria-hidden="true">⚙️</span>
                      <span className="menu-item-text">Trang quản trị Django</span>
                    </a>
                  )}

                  <hr className="menu-divider" />

                  <button
                    type="button"
                    className="menu-item-btn danger"
                    role="menuitem"
                    onClick={() => {
                      setAccountMenuOpen(false)
                      onLogout()
                    }}
                  >
                    <span className="menu-item-icon" aria-hidden="true">🚪</span>
                    <span className="menu-item-text">Đăng xuất</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Nội dung trang */}
        <main className="app-content-body">
          {children}
        </main>
      </div>
    </div>
  )
}
