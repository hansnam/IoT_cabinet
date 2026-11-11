import { useEffect, useState } from 'react'
import './App.css'

// Đường dẫn API chuẩn Django V1.4 (dùng Vite Proxy hoặc biến môi trường)
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

function App() {
  const [page, setPage] = useState('dashboard')
  const [devices, setDevices] = useState([])
  const [alerts, setAlerts] = useState([])
  const [telemetry, setTelemetry] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('all')

  useEffect(() => {
    let active = true

    async function loadData() {
      try {
        const requestOptions = {
          credentials: 'include',
          headers: {
            'Accept': 'application/json'
          }
        }

        // 1. Gọi các API độc lập kèm .catch() để endpoint lỗi không kéo sập toàn bộ
        const [devicesRes, alertsRes, telemetryRes] = await Promise.all([
          fetch(`${API_BASE}/devices/`, requestOptions).catch(() => null),
          fetch(`${API_BASE}/alerts/`, requestOptions).catch(() => null),
          fetch(`${API_BASE}/telemetry/`, requestOptions).catch(() => null)
        ])

        // Hàm hỗ trợ bóc tách mảng JSON an toàn từ Response
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

        // 2. Xử lý kết quả danh sách Thiết bị (Devices)
        if (devicesRes && devicesRes.ok) {
          const devicesList = await parseList(devicesRes)
          if (active) setDevices(devicesList)
        } else if (devicesRes && (devicesRes.status === 401 || devicesRes.status === 403)) {
          throw new Error('Chưa đăng nhập hoặc phiên làm việc đã hết hạn.')
        } else if (devicesRes && !devicesRes.ok) {
          throw new Error(`Không thể lấy danh sách thiết bị (Mã lỗi: ${devicesRes.status})`)
        }

        // 3. Xử lý Cảnh báo (Alerts)
        const alertsList = await parseList(alertsRes)
        if (active) setAlerts(alertsList)

        // 4. Xử lý Telemetry
        const telemetryList = await parseList(telemetryRes)
        if (active) setTelemetry(telemetryList)

        if (active) setError('')
      } catch (err) {
        if (active) {
          setError(err.message || 'Không thể kết nối đến Backend.')
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    loadData()
    // Chu kỳ Polling 2 giây một lần theo đặc tả P0 V1.4
    const timer = setInterval(loadData, 2000)

    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  // Dữ liệu telemetry mới nhất (hoặc lấy từ số đo mới nhất của thiết bị đầu tiên nếu telemetry rỗng)
  const latest = telemetry[0] || (devices[0] ? {
    temperature: devices[0].latest_temperature,
    humidity: devices[0].latest_humidity,
    fan_status: devices[0].latest_fan_status,
  } : {})

  // Danh sách cảnh báo chưa xử lý (OPEN / ACKNOWLEDGED)
  const openAlerts = alerts.filter(alert =>
    !['resolved', 'closed'].includes(
      String(alert.status || '').toLowerCase()
    )
  )

  // Lọc cảnh báo theo từ khóa và mức độ
  const filteredAlerts = alerts.filter(alert => {
    const message = alert.message || alert.alert_type || alert.type || ''
    const matchesSearch = String(message)
      .toLowerCase()
      .includes(search.toLowerCase())

    const alertSev = String(alert.severity || '').toLowerCase()
    const matchesSeverity =
      severity === 'all' ||
      alertSev === severity.toLowerCase() ||
      (severity === 'high' && alertSev === 'warning') ||
      (severity === 'low' && alertSev === 'info')

    return matchesSearch && matchesSeverity
  })

  // Định dạng an toàn: null / undefined -> '--'
  const format = value =>
    value === null || value === undefined || value === ''
      ? '--'
      : value

  const formatDate = value =>
    value ? new Date(value).toLocaleString('vi-VN') : '--'

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">IC</div>
          <div>
            <h2>IoT Cabinet</h2>
            <p>Device Monitoring</p>
          </div>
        </div>

        <p className="nav-label">MENU</p>

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

        <div className="sidebar-bottom">
          <span className="status-dot" />
          Giám sát hệ thống
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">IOT CABINET / MONITORING</p>
            <h1>{page === 'dashboard' ? 'Dashboard' : 'Cảnh báo'}</h1>
          </div>
          <div className="cabinet-tag">
            <span className="status-dot" />
            Cabinet monitoring
          </div>
        </header>

        {error && (
          <div className="notice error">
            <strong>Lỗi kết nối.</strong> {error}
          </div>
        )}

        {loading && <p className="loading">Đang kết nối Backend...</p>}

        {page === 'dashboard' ? (
          <>
            <div className="section-heading">
              <div>
                <h2>Tổng quan hệ thống</h2>
                <p>Thông tin thiết bị và dữ liệu giám sát mới nhất.</p>
              </div>
              <span className="refresh-label">Tự làm mới mỗi 2 giây</span>
            </div>

            <section className="cards">
              <article className="card metric metric-devices">
                <p>Tổng thiết bị</p>
                <h2>{devices.length}</h2>
                <span>Thiết bị được tải</span>
              </article>
              <article className="card metric metric-alerts">
                <p>Cảnh báo chưa xử lý</p>
                <h2>{openAlerts.length}</h2>
                <span>Cần theo dõi</span>
              </article>
              <article className="card metric metric-temp">
                <p>Nhiệt độ gần nhất</p>
                <h2>{format(latest.temperature ?? latest.temperature_c)}<small> °C</small></h2>
                <span>Dữ liệu telemetry</span>
              </article>
              <article className="card metric metric-humid">
                <p>Độ ẩm gần nhất</p>
                <h2>{format(latest.humidity ?? latest.humidity_pct)}<small> %</small></h2>
                <span>Dữ liệu telemetry</span>
              </article>
            </section>

            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>Danh sách thiết bị</h2>
                  <p>Trạng thái kết nối và sức khỏe thiết bị</p>
                </div>
                <span className="count">{devices.length} thiết bị</span>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Thiết bị</th>
                      <th>Loại</th>
                      <th>Kết nối</th>
                      <th>Sức khỏe</th>
                      <th>Lần cuối hoạt động</th>
                    </tr>
                  </thead>
                  <tbody>
                    {devices.map((device, index) => (
                      <tr key={device.id || device.device_id || index}>
                        <td>
                          <strong>{device.name || device.id || device.device_id || '--'}</strong>
                          <small>{device.id || device.device_id || ''}</small>
                        </td>
                        <td>{format(device.hardware_version ? `${device.hardware_version} (${device.firmware_version || 'v1.0'})` : (device.device_type || device.profile || device.source))}</td>
                        <td>
                          <span className={`status-pill ${device.is_online ? 'online' : (device.is_online === false ? 'offline' : String(device.connection_state || device.connection || 'offline').toLowerCase())}`}>
                            {device.is_online !== undefined ? (device.is_online ? 'ONLINE' : 'OFFLINE') : format(device.connection_state || device.connection)}
                          </span>
                        </td>
                        <td>{format(device.status || device.health_state || device.health)}</td>
                        <td>{formatDate(device.last_seen_at || device.last_seen)}</td>
                      </tr>
                    ))}
                    {!devices.length && !loading && (
                      <tr>
                        <td colSpan="5" className="empty">
                          Chưa có thiết bị nào trong CSDL Backend.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="card">
              <div className="card-heading">
                <div>
                  <h2>Lịch sử telemetry</h2>
                  <p>Bản ghi nhiệt độ & độ ẩm đo từ cảm biến</p>
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Thời gian</th>
                      <th>Nhiệt độ</th>
                      <th>Độ ẩm</th>
                      <th>Đầu ra quạt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {telemetry.slice(0, 10).map((item, index) => (
                      <tr key={item.id || item.measured_at || index}>
                        <td>{formatDate(item.measured_at || item.received_at)}</td>
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
                            <small style={{ display: 'block', color: '#888' }}>Chế độ: {item.mode}</small>
                          )}
                        </td>
                      </tr>
                    ))}
                    {!telemetry.length && !loading && (
                      <tr>
                        <td colSpan="4" className="empty">
                          Chưa có bản ghi telemetry (Thiết bị chưa gửi số đo).
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : (
          <section className="card">
            <div className="card-heading">
              <div>
                <h2>Danh sách cảnh báo</h2>
                <p>Theo dõi và lọc các cảnh báo từ hệ thống.</p>
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
                    <th>Thời gian</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAlerts.map((alert, index) => (
                    <tr key={alert.id || index}>
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
                    </tr>
                  ))}
                  {!filteredAlerts.length && !loading && (
                    <tr>
                      <td colSpan="4" className="empty">
                        Không có cảnh báo nào.
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