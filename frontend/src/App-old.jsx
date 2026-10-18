
import { useEffect, useState } from 'react'
import './App.css'

const API = 'http://127.0.0.1:5000'

function App() {
  const [page, setPage] = useState('dashboard')
  const [devices, setDevices] = useState([])
  const [alerts, setAlerts] = useState([])
  const [telemetry, setTelemetry] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('all')

  useEffect(() => {
    let active = true

    async function loadData() {
      setLoading(true)
      setError('')

      try {
        const results = await Promise.all([
          fetch(`${API}/api/devices`),
          fetch(`${API}/api/alerts`),
          fetch(`${API}/api/telemetry`)
        ])

        if (results.some(response => !response.ok)) {
          throw new Error('API chưa sẵn sàng hoặc đường dẫn chưa đúng.')
        }

        const data = await Promise.all(results.map(response => response.json()))

        if (active) {
          setDevices(Array.isArray(data[0]) ? data[0] : data[0].data || [])
          setAlerts(Array.isArray(data[1]) ? data[1] : data[1].data || [])
          setTelemetry(Array.isArray(data[2]) ? data[2] : data[2].data || [])
        }
      } catch (err) {
        if (active) setError(err.message || 'Không thể kết nối backend.')
      } finally {
        if (active) setLoading(false)
      }
    }

    loadData()
    const timer = setInterval(loadData, 5000)

    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  const latest = telemetry[0] || {}
  const openAlerts = alerts.filter(alert =>
    !['resolved', 'closed'].includes(
      String(alert.status || '').toLowerCase()
    )
  )

  const filteredAlerts = alerts.filter(alert => {
    const matchesSearch = String(alert.message || '')
      .toLowerCase()
      .includes(search.toLowerCase())
    const matchesSeverity =
      severity === 'all' ||
      String(alert.severity || '').toLowerCase() === severity
    return matchesSearch && matchesSeverity
  })

  const format = value =>
    value === null || value === undefined || value === ''
      ? '--'
      : value

  const date = value =>
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
            <strong>Chưa tải được dữ liệu.</strong> {error}
            <p>Kiểm tra backend và cấu hình API trước khi sử dụng.</p>
          </div>
        )}

        {loading && <p className="loading">Đang tải dữ liệu...</p>}

        {page === 'dashboard' ? (
          <>
            <div className="section-heading">
              <div>
                <h2>Tổng quan hệ thống</h2>
                <p>Thông tin thiết bị và dữ liệu giám sát mới nhất.</p>
              </div>
              <span className="refresh-label">Tự làm mới mỗi 5 giây</span>
            </div>

            <section className="cards">
              <article className="card metric">
                <p>Tổng thiết bị</p>
                <h2>{devices.length}</h2>
                <span>Thiết bị được trả về từ API</span>
              </article>
              <article className="card metric">
                <p>Cảnh báo chưa xử lý</p>
                <h2>{openAlerts.length}</h2>
                <span>Cần theo dõi</span>
              </article>
              <article className="card metric">
                <p>Nhiệt độ gần nhất</p>
                <h2>{format(latest.temperature_c)}<small> °C</small></h2>
                <span>Dữ liệu telemetry</span>
              </article>
              <article className="card metric">
                <p>Độ ẩm gần nhất</p>
                <h2>{format(latest.humidity_pct)}<small> %</small></h2>
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
                      <tr key={device.device_id || index}>
                        <td>
                          <strong>{device.name || device.device_id || '--'}</strong>
                          <small>{device.device_id || ''}</small>
                        </td>
                        <td>{format(device.device_type)}</td>
                        <td>
                          <span className="status-pill">
                            {format(device.connection_state)}
                          </span>
                        </td>
                        <td>{format(device.health_state)}</td>
                        <td>{date(device.last_seen_at)}</td>
                      </tr>
                    ))}
                    {!devices.length && (
                      <tr>
                        <td colSpan="5" className="empty">
                          Chưa có dữ liệu thiết bị.
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
                  <p>Các bản ghi đo được trả về từ API</p>
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
                      <tr key={item.id || item.received_at || index}>
                        <td>{date(item.received_at || item.measured_at)}</td>
                        <td>{format(item.temperature_c)} °C</td>
                        <td>{format(item.humidity_pct)} %</td>
                        <td>{format(item.fan_output)}</td>
                      </tr>
                    ))}
                    {!telemetry.length && (
                      <tr>
                        <td colSpan="4" className="empty">
                          Chưa có bản ghi telemetry.
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
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Nội dung</th>
                    <th>Mức độ</th>
                    <th>Trạng thái</th>
                    <th>Thời gian</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAlerts.map((alert, index) => (
                    <tr key={alert.id || index}>
                      <td>
                        <strong>{alert.message || '--'}</strong>
                        <small>{alert.device_id || ''}</small>
                      </td>
                      <td>
                        <span className={`severity ${String(alert.severity || '').toLowerCase()}`}>
                          {format(alert.severity)}
                        </span>
                      </td>
                      <td>{format(alert.status)}</td>
                      <td>{date(alert.created_at || alert.triggered_at)}</td>
                    </tr>
                  ))}
                  {!filteredAlerts.length && (
                    <tr>
                      <td colSpan="4" className="empty">
                        Không có cảnh báo phù hợp.
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
