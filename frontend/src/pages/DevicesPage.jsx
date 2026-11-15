import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, asList, translateConnectionState } from '../api.js'

/**
 * Trang Danh sách tủ mạng (DevicesPage)
 * Cung cấp:
 * 1. Tìm kiếm theo tên hoặc mã tủ
 * 2. Lọc theo trạng thái kết nối (Đang kết nối / Mất kết nối)
 * 3. Xem nhanh số đo nhiệt độ/độ ẩm/quạt và dẫn tới trang chi tiết từng tủ
 */
export default function DevicesPage() {
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')

  const loadDevices = useCallback(async () => {
    try {
      const body = await api('/devices/')
      setDevices(asList(body))
      setError('')
    } catch (err) {
      setError(err.message || 'Không thể tải danh sách tủ mạng.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    let timer

    async function poll() {
      await loadDevices()
      if (active) timer = setTimeout(poll, 3000)
    }

    timer = setTimeout(poll, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadDevices])

  // Lọc theo tìm kiếm và trạng thái kết nối
  const filteredDevices = devices.filter((device) => {
    const devId = String(device.id || device.device_id || '').toLowerCase()
    const name = String(device.name || '').toLowerCase()
    const q = search.trim().toLowerCase()
    const matchesSearch = !q || devId.includes(q) || name.includes(q)

    const conn = translateConnectionState(
      device.is_online,
      device.connection_state || device.connection
    )
    const matchesFilter =
      statusFilter === 'all' ||
      (statusFilter === 'online' && conn.statusClass === 'online') ||
      (statusFilter === 'offline' && conn.statusClass === 'offline')

    return matchesSearch && matchesFilter
  })

  const format = (val) => (val === null || val === undefined || val === '' ? '--' : val)
  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  return (
    <div className="devices-page">
      <section className="card" aria-labelledby="devices-list-heading">
        <div className="card-heading">
          <div>
            <h2 id="devices-list-heading">Danh sách tủ mạng</h2>
            <p>Danh sách các tủ mạng trong phạm vi phân quyền truy cập của bạn</p>
          </div>
          <span className="count">{filteredDevices.length} / {devices.length} tủ</span>
        </div>

        {/* Bộ lọc và Tìm kiếm */}
        <div className="filters">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm theo tên tủ hoặc mã tủ..."
            aria-label="Tìm kiếm tủ mạng"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Lọc theo trạng thái kết nối"
          >
            <option value="all">Tất cả trạng thái kết nối</option>
            <option value="online">Đang kết nối (Online)</option>
            <option value="offline">Mất kết nối (Offline)</option>
          </select>
        </div>

        {/* Bảng danh sách thiết bị */}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tủ mạng</th>
                <th>Phần cứng / Firmware</th>
                <th>Kết nối</th>
                <th>Số đo mới nhất</th>
                <th>Trạng thái quạt</th>
                <th>Lần cuối hoạt động</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {filteredDevices.map((device, idx) => {
                const devId = device.id || device.device_id
                const conn = translateConnectionState(
                  device.is_online,
                  device.connection_state || device.connection
                )

                const temp = device.latest_temperature
                const humid = device.latest_humidity
                const fanStatus = device.latest_fan_status
                const isFanOn = fanStatus === 'ON' || fanStatus === true || fanStatus === 1

                return (
                  <tr key={devId || idx}>
                    <td>
                      <Link to={`/devices/${encodeURIComponent(devId)}`}>
                        <strong>{device.name || devId}</strong>
                      </Link>
                      <small>Mã: {devId}</small>
                    </td>
                    <td>
                      {format(
                        device.hardware_version
                          ? `${device.hardware_version} (${device.firmware_version || 'v1.0'})`
                          : device.device_type || device.profile || device.source
                      )}
                    </td>
                    <td>
                      <span className={`status-pill ${conn.statusClass}`}>
                        {conn.label}
                      </span>
                    </td>
                    <td>
                      <span className="temp-text">
                        {temp !== undefined && temp !== null ? `${format(temp)} °C` : '--'}
                      </span>
                      {' · '}
                      <span className="humid-text">
                        {humid !== undefined && humid !== null ? `${format(humid)} %` : '--'}
                      </span>
                    </td>
                    <td>
                      {fanStatus !== undefined && fanStatus !== null && fanStatus !== '' ? (
                        <span className={`fan-badge ${isFanOn ? 'on' : 'off'}`}>
                          {isFanOn ? 'BẬT (ON)' : 'TẮT (OFF)'}
                        </span>
                      ) : (
                        '--'
                      )}
                      {device.latest_mode && (
                        <small style={{ color: 'var(--text-muted)' }}>
                          {device.latest_mode === 'AUTO' ? 'Tự động' : 'Thủ công'}
                        </small>
                      )}
                    </td>
                    <td>{formatDate(device.last_seen_at || device.last_seen)}</td>
                    <td>
                      <Link
                        to={`/devices/${encodeURIComponent(devId)}`}
                        className="btn btn-outline btn-sm"
                        title="Xem thông tin chi tiết và điều khiển tủ"
                      >
                        Chi tiết tủ →
                      </Link>
                    </td>
                  </tr>
                )
              })}

              {/* Lỗi tải danh sách */}
              {error && !filteredDevices.length && (
                <tr>
                  <td colSpan="7" className="table-state-error">
                    <div className="table-error-row">
                      <span>⚠️ Không thể tải danh sách tủ ({error}).</span>
                      <button type="button" className="btn btn-outline btn-sm" onClick={loadDevices}>
                        Thử lại
                      </button>
                    </div>
                  </td>
                </tr>
              )}

              {/* Đang tải */}
              {loading && !devices.length && !error && (
                <tr>
                  <td colSpan="7" className="table-state-loading">
                    Đang tải danh sách tủ mạng từ hệ thống…
                  </td>
                </tr>
              )}

              {/* Danh sách rỗng */}
              {!filteredDevices.length && !loading && !error && (
                <tr>
                  <td colSpan="7" className="empty">
                    {search || statusFilter !== 'all'
                      ? 'Không tìm thấy tủ mạng nào phù hợp với bộ lọc hiện tại.'
                      : 'Chưa có tủ mạng nào được gán quyền cho tài khoản của bạn.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
