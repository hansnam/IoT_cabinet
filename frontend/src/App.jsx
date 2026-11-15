import { useEffect, useState } from 'react'
import './App.css'

// Đường dẫn API chuẩn Django V1.4 (dùng Vite Proxy hoặc biến môi trường)
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

// Hàm lấy token CSRF từ cookie hoặc endpoint API của Django
async function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/)
  if (match && match[1]) return decodeURIComponent(match[1])
  try {
    const res = await fetch(`${API_BASE}/auth/csrf/`, { credentials: 'include' })
    if (res.ok) {
      const data = await res.json()
      return data.csrf_token || ''
    }
  } catch {}
  return ''
}

// Hàm gửi request API kèm credentials và header CSRF tự động cho POST/PATCH/DELETE
async function apiRequest(endpoint, method = 'GET', body = null) {
  const headers = { Accept: 'application/json' }
  const opts = { method, credentials: 'include', headers }
  if (body) {
    headers['Content-Type'] = 'application/json'
    headers['X-CSRFToken'] = await getCsrfToken()
    opts.body = JSON.stringify(body)
  }
  return fetch(`${API_BASE}${endpoint}`, opts)
}

function App() {
  const [page, setPage] = useState('dashboard')
  const [devices, setDevices] = useState([])
  const [alerts, setAlerts] = useState([])
  const [telemetry, setTelemetry] = useState([])
  const [commands, setCommands] = useState([])
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('all')
  const [cmdFilter, setCmdFilter] = useState('all')
  const [actionLoading, setActionLoading] = useState(null)
  const [toast, setToast] = useState(null)

  // State form đăng nhập
  const [showLoginModal, setShowLoginModal] = useState(false)
  const [loginUsername, setLoginUsername] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState('')
  const [loginLoading, setLoginLoading] = useState(false)

  const showToast = (message, type = 'info') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3500)
  }

  // Tải dữ liệu toàn hệ thống
  async function loadData() {
    try {
      const requestOptions = {
        credentials: 'include',
        headers: { Accept: 'application/json' }
      }

      // Gọi đồng thời 5 API nghiệp vụ của Backend
      const [devicesRes, alertsRes, telemetryRes, commandsRes, meRes] = await Promise.all([
        fetch(`${API_BASE}/devices/`, requestOptions).catch(() => null),
        fetch(`${API_BASE}/alerts/`, requestOptions).catch(() => null),
        fetch(`${API_BASE}/telemetry/`, requestOptions).catch(() => null),
        fetch(`${API_BASE}/commands/`, requestOptions).catch(() => null),
        fetch(`${API_BASE}/auth/me/`, requestOptions).catch(() => null),
      ])

      const parseList = async (res) => {
        if (!res || !res.ok) return []
        try {
          const json = await res.json()
          if (Array.isArray(json)) return json
          return Array.isArray(json.data) ? json.data : []
        } catch {
          return []
        }
      }

      // Xử lý thông tin phiên người dùng
      if (meRes && meRes.ok) {
        const meJson = await meRes.json()
        setUser(meJson.data || meJson.user || meJson)
      } else {
        setUser(null)
      }

      // Xử lý danh sách Thiết bị
      if (devicesRes && devicesRes.ok) {
        const list = await parseList(devicesRes)
        setDevices(list)
      } else if (devicesRes && (devicesRes.status === 401 || devicesRes.status === 403)) {
        throw new Error('Chưa đăng nhập hoặc phiên làm việc đã hết hạn.')
      }

      // Xử lý Cảnh báo
      if (alertsRes && alertsRes.ok) {
        setAlerts(await parseList(alertsRes))
      }

      // Xử lý Telemetry
      if (telemetryRes && telemetryRes.ok) {
        setTelemetry(await parseList(telemetryRes))
      }

      // Xử lý Lịch sử lệnh (Commands)
      if (commandsRes && commandsRes.ok) {
        setCommands(await parseList(commandsRes))
      }

      setError('')
    } catch (err) {
      setError(err.message || 'Không thể kết nối đến Backend.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    // Chu kỳ Polling 3 giây / lần
    const timer = setInterval(loadData, 3000)
    return () => clearInterval(timer)
  }, [])

  // Xử lý Đăng nhập
  const handleLogin = async (e) => {
    e.preventDefault()
    setLoginLoading(true)
    setLoginError('')
    try {
      const res = await apiRequest('/auth/login/', 'POST', {
        username: loginUsername.trim(),
        password: loginPassword
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.message || data.error?.message || 'Tên đăng nhập hoặc mật khẩu không chính xác.')
      }
      setUser(data.user)
      setShowLoginModal(false)
      setLoginPassword('')
      showToast('Đăng nhập thành công!', 'success')
      loadData()
    } catch (err) {
      setLoginError(err.message)
    } finally {
      setLoginLoading(false)
    }
  }

  // Xử lý Đăng xuất
  const handleLogout = async () => {
    try {
      await apiRequest('/auth/logout/', 'POST')
      setUser(null)
      showToast('Đã đăng xuất.', 'info')
      loadData()
    } catch (err) {
      showToast('Lỗi khi đăng xuất: ' + err.message, 'error')
    }
  }

  // Xử lý Gửi lệnh điều khiển quạt / chế độ
  const handleControlDevice = async (deviceId, payload, actionName) => {
    setActionLoading(`dev-${deviceId}`)
    try {
      const res = await apiRequest(`/devices/${deviceId}/control/`, 'POST', payload)
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.message || data.error?.message || 'Không thể thực hiện lệnh điều khiển.')
      }
      showToast(data.message || `Đã phát lệnh ${actionName} tới tủ ${deviceId}.`, 'success')
      loadData()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setActionLoading(null)
    }
  }

  // Xử lý Acknowledge / Resolve cảnh báo
  const handleAlertAction = async (alertId, action) => {
    setActionLoading(`alert-${alertId}`)
    try {
      const res = await apiRequest(`/alerts/${alertId}/${action}/`, 'POST')
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.message || data.error?.message || 'Thao tác không thành công.')
      }
      showToast(
        action === 'acknowledge' ? 'Đã xác nhận sự cố cảnh báo.' : 'Đã đánh dấu giải quyết sự cố.',
        'success'
      )
      loadData()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setActionLoading(null)
    }
  }

  // Xuất file CSV viễn trắc
  const handleExportCsv = (deviceId = '') => {
    const url = deviceId
      ? `${API_BASE}/telemetry/export/?device_id=${encodeURIComponent(deviceId)}`
      : `${API_BASE}/telemetry/export/`
    window.open(url, '_blank')
  }

  // Dữ liệu telemetry mới nhất
  const latest = telemetry[0] || (devices[0] ? {
    temperature: devices[0].latest_temperature,
    humidity: devices[0].latest_humidity,
    fan_status: devices[0].latest_fan_status,
  } : {})

  // Danh sách cảnh báo chưa xử lý
  const openAlerts = alerts.filter(alert =>
    !['resolved', 'closed'].includes(String(alert.status || '').toLowerCase())
  )

  // Lọc cảnh báo
  const filteredAlerts = alerts.filter(alert => {
    const message = alert.message || alert.alert_type || alert.type || ''
    const matchesSearch = String(message).toLowerCase().includes(search.toLowerCase())

    const alertSev = String(alert.severity || '').toLowerCase()
    const matchesSeverity =
      severity === 'all' ||
      alertSev === severity.toLowerCase() ||
      (severity === 'high' && alertSev === 'warning') ||
      (severity === 'low' && alertSev === 'info')

    return matchesSearch && matchesSeverity
  })

  // Lọc lịch sử lệnh
  const filteredCommands = commands.filter(cmd => {
    if (cmdFilter === 'all') return true
    return String(cmd.status || '').toUpperCase() === cmdFilter.toUpperCase()
  })

  const format = value => (value === null || value === undefined || value === '' ? '--' : value)
  const formatDate = value => (value ? new Date(value).toLocaleString('vi-VN') : '--')

  return (
    <div className="app">
      {/* Toast thông báo nổi */}
      {toast && (
        <div className="toast-container">
          <div className={`toast ${toast.type}`}>
            <span>{toast.type === 'success' ? '✓' : toast.type === 'error' ? '⚠' : 'ℹ'}</span>
            <span>{toast.message}</span>
          </div>
        </div>
      )}

      {/* Modal Đăng nhập */}
      {showLoginModal && (
        <div className="modal-backdrop" onClick={() => setShowLoginModal(false)}>
          <div className="modal-box" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Đăng nhập hệ thống</h3>
              <p>Đăng nhập tài khoản quản trị viên hoặc vận hành viên để thực hiện điều khiển thiết bị.</p>
            </div>
            {loginError && <div className="notice error">{loginError}</div>}
            <form onSubmit={handleLogin}>
              <div className="form-group">
                <label>Tên đăng nhập (Username)</label>
                <input
                  type="text"
                  required
                  value={loginUsername}
                  onChange={e => setLoginUsername(e.target.value)}
                  placeholder="Ví dụ: test hoặc admin"
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>Mật khẩu (Password)</label>
                <input
                  type="password"
                  required
                  value={loginPassword}
                  onChange={e => setLoginPassword(e.target.value)}
                  placeholder="Nhập mật khẩu..."
                />
              </div>
              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '20px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => setShowLoginModal(false)}
                >
                  Đóng
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={loginLoading}
                >
                  {loginLoading ? 'Đang xác thực...' : 'Đăng nhập'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">IC</div>
          <div>
            <h2>IoT Cabinet</h2>
            <p>V1.4 Management</p>
          </div>
        </div>

        <p className="nav-label">QUẢN LÝ</p>

        <button
          className={page === 'dashboard' ? 'nav-item active' : 'nav-item'}
          onClick={() => setPage('dashboard')}
        >
          <span>▦</span> Dashboard
        </button>

        <button
          className={page === 'alerts' ? 'nav-item active' : 'nav-item'}
          onClick={() => setPage('alerts')}
        >
          <span>♧</span> Cảnh báo
          {openAlerts.length > 0 && (
            <span className="nav-count">{openAlerts.length}</span>
          )}
        </button>

        <button
          className={page === 'commands' ? 'nav-item active' : 'nav-item'}
          onClick={() => setPage('commands')}
        >
          <span>⚙</span> Lịch sử lệnh
          {commands.length > 0 && (
            <span className="count-tag" style={{ marginLeft: 'auto', fontSize: '11px', color: '#93c5fd' }}>
              {commands.length}
            </span>
          )}
        </button>

        <div className="sidebar-bottom">
          <span className="status-dot" />
          Giám sát hệ thống

          {user ? (
            <div className="user-badge">
              <div className="user-info">
                <span className="user-name">{user.full_name || user.username}</span>
                <span className="user-role">{user.role || 'Operator'}</span>
              </div>
              <button className="btn-logout" onClick={handleLogout} title="Đăng xuất">
                Thoát
              </button>
            </div>
          ) : (
            <div style={{ marginTop: '12px' }}>
              <button
                className="btn btn-primary btn-sm"
                style={{ width: '100%' }}
                onClick={() => setShowLoginModal(true)}
              >
                🔑 Đăng nhập
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">IOT CABINET / MONITORING</p>
            <h1>
              {page === 'dashboard'
                ? 'Dashboard Giám sát'
                : page === 'alerts'
                ? 'Quản lý Cảnh báo'
                : 'Lịch sử Phát lệnh Điều khiển'}
            </h1>
          </div>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
            {!user && (
              <button
                className="btn btn-outline btn-sm"
                onClick={() => setShowLoginModal(true)}
              >
                Đăng nhập
              </button>
            )}
            <div className="cabinet-tag">
              <span className="status-dot" />
              Cabinet monitoring
            </div>
          </div>
        </header>

        {error && (
          <div className="notice error" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span><strong>Lưu ý:</strong> {error}</span>
            {!user && (
              <button
                className="btn btn-primary btn-sm"
                onClick={() => setShowLoginModal(true)}
              >
                Đăng nhập ngay
              </button>
            )}
          </div>
        )}

        {loading && <p className="loading">Đang kết nối Backend...</p>}

        {/* 1. Trang DASHBOARD */}
        {page === 'dashboard' && (
          <>
            <div className="section-heading">
              <div>
                <h2>Tổng quan hệ thống</h2>
                <p>Thông tin thiết bị và dữ liệu giám sát mới nhất.</p>
              </div>
              <span className="refresh-label">Tự làm mới mỗi 3 giây</span>
            </div>

            {/* Metrics cards */}
            <section className="cards">
              <article className="card metric metric-devices">
                <p>Tổng thiết bị</p>
                <h2>{devices.length}</h2>
                <span>Thiết bị trong hệ thống</span>
              </article>
              <article className="card metric metric-alerts">
                <p>Cảnh báo chưa xử lý</p>
                <h2>{openAlerts.length}</h2>
                <span>Cần theo dõi ngay</span>
              </article>
              <article className="card metric metric-temp">
                <p>Nhiệt độ gần nhất</p>
                <h2>{format(latest.temperature ?? latest.temperature_c)}<small> °C</small></h2>
                <span>Dữ liệu cảm biến</span>
              </article>
              <article className="card metric metric-humid">
                <p>Độ ẩm gần nhất</p>
                <h2>{format(latest.humidity ?? latest.humidity_pct)}<small> %</small></h2>
                <span>Dữ liệu cảm biến</span>
              </article>
            </section>

            {/* Bảng Danh sách Thiết bị & Điều khiển */}
            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>Danh sách thiết bị & Điều khiển trực tiếp</h2>
                  <p>Trạng thái kết nối và phát lệnh điều khiển phần cứng</p>
                </div>
                <span className="count">{devices.length} thiết bị</span>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Thiết bị</th>
                      <th>Phiên bản</th>
                      <th>Kết nối</th>
                      <th>Trạng thái</th>
                      <th>Lần cuối hoạt động</th>
                      <th>Điều khiển quạt & Chế độ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((device, index) => {
                      const devId = device.id || device.device_id
                      const isLoading = actionLoading === `dev-${devId}`
                      return (
                        <tr key={devId || index}>
                          <td>
                            <strong>{device.name || devId || '--'}</strong>
                            <small>{devId || ''}</small>
                          </td>
                          <td>
                            {format(
                              device.hardware_version
                                ? `${device.hardware_version} (${device.firmware_version || 'v1.0'})`
                                : device.device_type || device.profile || device.source
                            )}
                          </td>
                          <td>
                            <span
                              className={`status-pill ${
                                device.is_online
                                  ? 'online'
                                  : device.is_online === false
                                  ? 'offline'
                                  : String(device.connection_state || device.connection || 'offline').toLowerCase()
                              }`}
                            >
                              {device.is_online !== undefined
                                ? device.is_online
                                  ? 'ONLINE'
                                  : 'OFFLINE'
                                : format(device.connection_state || device.connection)}
                            </span>
                          </td>
                          <td>{format(device.status || device.health_state || device.health)}</td>
                          <td>{formatDate(device.last_seen_at || device.last_seen)}</td>
                          <td>
                            <div className="btn-group">
                              {/* Nút Bật / Tắt Quạt */}
                              <button
                                className="btn btn-success btn-sm"
                                disabled={isLoading}
                                onClick={() =>
                                  handleControlDevice(
                                    devId,
                                    { command: 'SET_FAN', fan: 'ON' },
                                    'BẬT QUẠT'
                                  )
                                }
                              >
                                ⚡ Bật quạt
                              </button>
                              <button
                                className="btn btn-outline btn-sm"
                                disabled={isLoading}
                                onClick={() =>
                                  handleControlDevice(
                                    devId,
                                    { command: 'SET_FAN', fan: 'OFF' },
                                    'TẮT QUẠT'
                                  )
                                }
                              >
                                Tắt
                              </button>

                              {/* Nút Chuyển chế độ Auto / Manual */}
                              <button
                                className="btn btn-primary btn-sm"
                                disabled={isLoading}
                                onClick={() =>
                                  handleControlDevice(
                                    devId,
                                    { mode: 'AUTO' },
                                    'CHẾ ĐỘ TỰ ĐỘNG'
                                  )
                                }
                              >
                                Auto
                              </button>
                              <button
                                className="btn btn-outline btn-sm"
                                disabled={isLoading}
                                onClick={() =>
                                  handleControlDevice(
                                    devId,
                                    { mode: 'MANUAL' },
                                    'CHẾ ĐỘ THỦ CÔNG'
                                  )
                                }
                              >
                                Manual
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                    {!devices.length && !loading && (
                      <tr>
                        <td colSpan="6" className="empty">
                          Chưa có thiết bị nào trong CSDL Backend.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Bảng Lịch sử Telemetry & Nút Xuất CSV */}
            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>Lịch sử viễn trắc (Telemetry)</h2>
                  <p>Bản ghi nhiệt độ & độ ẩm đo từ cảm biến gửi về Broker</p>
                </div>
                <button
                  className="btn btn-outline btn-sm"
                  onClick={() => handleExportCsv(devices[0]?.id || '')}
                  title="Xuất file CSV báo cáo viễn trắc"
                >
                  📥 Xuất file CSV
                </button>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Thời gian đo</th>
                      <th>Mã tủ</th>
                      <th>Nhiệt độ</th>
                      <th>Độ ẩm</th>
                      <th>Trạng thái quạt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {telemetry.slice(0, 10).map((item, index) => (
                      <tr key={item.id || item.measured_at || index}>
                        <td>{formatDate(item.measured_at || item.received_at)}</td>
                        <td><strong>{item.device_id || devices[0]?.id || '--'}</strong></td>
                        <td className="temp-text">{format(item.temperature ?? item.temperature_c)} °C</td>
                        <td className="humid-text">{format(item.humidity ?? item.humidity_pct)} %</td>
                        <td>
                          {(() => {
                            const val = item.fan_status !== undefined ? item.fan_status : item.fan_output
                            if (val === null || val === undefined || val === '') return '--'
                            const isOn = val === 'ON' || val === true || val === 1
                            return (
                              <span className={`fan-badge ${isOn ? 'on' : 'off'}`}>
                                {isOn ? 'BẬT (ON)' : 'TẮT (OFF)'}
                              </span>
                            )
                          })()}
                          {item.mode && (
                            <small style={{ display: 'block', color: '#888' }}>
                              Chế độ: {item.mode}
                            </small>
                          )}
                        </td>
                      </tr>
                    ))}
                    {!telemetry.length && !loading && (
                      <tr>
                        <td colSpan="5" className="empty">
                          Chưa có bản ghi telemetry (Thiết bị chưa gửi số đo).
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}

        {/* 2. Trang CẢNH BÁO */}
        {page === 'alerts' && (
          <section className="card">
            <div className="card-heading">
              <div>
                <h2>Danh sách cảnh báo & Xử lý sự cố</h2>
                <p>Theo dõi, xác nhận (Acknowledge) và giải quyết (Resolve) các cảnh báo hệ thống.</p>
              </div>
              <span className="count">{filteredAlerts.length} kết quả</span>
            </div>

            <div className="filters">
              <input
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder="Tìm nội dung cảnh báo..."
                aria-label="Tìm cảnh báo"
              />
              <select
                value={severity}
                onChange={event => setSeverity(event.target.value)}
                aria-label="Lọc mức độ cảnh báo"
              >
                <option value="all">Tất cả mức độ</option>
                <option value="critical">Critical</option>
                <option value="high">High / Warning</option>
                <option value="medium">Medium</option>
                <option value="low">Low / Info</option>
              </select>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Nội dung / Loại</th>
                    <th>Mức độ</th>
                    <th>Trạng thái</th>
                    <th>Thời gian kích hoạt</th>
                    <th>Thao tác xử lý</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAlerts.map((alert, index) => {
                    const alertId = alert.id || index
                    const isLoading = actionLoading === `alert-${alertId}`
                    const isClosed = ['resolved', 'closed'].includes(String(alert.status || '').toLowerCase())
                    const isAcked = String(alert.status || '').toLowerCase() === 'acknowledged'

                    return (
                      <tr key={alertId}>
                        <td>
                          <strong>{alert.message || alert.alert_type || alert.type || '--'}</strong>
                          <small>
                            {alert.device_id ? `Tủ: ${alert.device_id}` : ''}
                            {alert.alert_type && alert.message ? ` • ${alert.alert_type}` : ''}
                          </small>
                        </td>
                        <td>
                          <span className={`severity ${String(alert.severity || '').toLowerCase()}`}>
                            {format(alert.severity)}
                          </span>
                        </td>
                        <td>{format(alert.status)}</td>
                        <td>{formatDate(alert.created_at || alert.triggered_at)}</td>
                        <td>
                          <div className="btn-group">
                            {!isClosed && !isAcked && (
                              <button
                                className="btn btn-primary btn-sm"
                                disabled={isLoading}
                                onClick={() => handleAlertAction(alertId, 'acknowledge')}
                              >
                                Xác nhận (ACK)
                              </button>
                            )}
                            {!isClosed && (
                              <button
                                className="btn btn-success btn-sm"
                                disabled={isLoading}
                                onClick={() => handleAlertAction(alertId, 'resolve')}
                              >
                                Đã giải quyết
                              </button>
                            )}
                            {isClosed && (
                              <span style={{ color: '#16a34a', fontWeight: 600, fontSize: '12px' }}>
                                ✓ Hoàn tất
                              </span>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                  {!filteredAlerts.length && !loading && (
                    <tr>
                      <td colSpan="5" className="empty">
                        Không có cảnh báo nào phù hợp.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* 3. Trang LỊCH SỬ LỆNH (COMMANDS) */}
        {page === 'commands' && (
          <section className="card">
            <div className="card-heading">
              <div>
                <h2>Lịch sử phát lệnh điều khiển (Commands)</h2>
                <p>Theo dõi trạng thái giao dịch lệnh qua Transactional Outbox và MQTT Broker</p>
              </div>
              <span className="count">{filteredCommands.length} lệnh</span>
            </div>

            <div className="filters">
              <select
                value={cmdFilter}
                onChange={e => setCmdFilter(e.target.value)}
                aria-label="Lọc trạng thái lệnh"
              >
                <option value="all">Tất cả trạng thái</option>
                <option value="PENDING">PENDING (Chờ gửi)</option>
                <option value="SENT">SENT (Đã phát tới Broker)</option>
                <option value="ACKED">ACKED (Thiết bị đã xác nhận)</option>
                <option value="FAILED">FAILED (Thất bại / Hết hạn)</option>
              </select>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Mã lệnh (ID)</th>
                    <th>Thiết bị</th>
                    <th>Loại lệnh</th>
                    <th>Trạng thái</th>
                    <th>Người thực hiện</th>
                    <th>Thời gian gửi</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCommands.map((cmd, index) => (
                    <tr key={cmd.id || index}>
                      <td>
                        <strong>{String(cmd.id).slice(0, 8)}...</strong>
                        <small>{cmd.correlation_id ? `Corr: ${String(cmd.correlation_id).slice(0, 8)}...` : ''}</small>
                      </td>
                      <td><strong>{cmd.device_id || cmd.device || '--'}</strong></td>
                      <td>
                        <span style={{ fontWeight: 600, color: '#2563eb' }}>
                          {format(cmd.command_type || cmd.command)}
                        </span>
                      </td>
                      <td>
                        <span className={`status-pill ${String(cmd.status || '').toLowerCase()}`}>
                          {format(cmd.status)}
                        </span>
                      </td>
                      <td>{format(cmd.actor_username || cmd.actor || 'System')}</td>
                      <td>{formatDate(cmd.created_at || cmd.sent_at)}</td>
                    </tr>
                  ))}
                  {!filteredCommands.length && !loading && (
                    <tr>
                      <td colSpan="6" className="empty">
                        Chưa có lệnh điều khiển nào được phát hành.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

export default App
