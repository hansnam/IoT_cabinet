import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, asList } from '../api.js'

/**
 * Trang Quản lý Cảnh báo hệ thống (AlertsPage)
 * Cung cấp:
 * 1. Lọc theo nội dung, mức độ và trạng thái xử lý
 * 2. Xác nhận đã xem sự cố (ACK) và đánh dấu đã xử lý (Resolve)
 * 3. Kiểm tra phân quyền can_operate trước khi gửi yêu cầu
 */
export default function AlertsPage({ showToast }) {
  const [alerts, setAlerts] = useState([])
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [actionLoading, setActionLoading] = useState(null)

  const loadAlertsData = useCallback(async () => {
    try {
      const [alertBody, devBody] = await Promise.all([
        api('/alerts/'),
        api('/devices/'),
      ])
      setAlerts(asList(alertBody))
      setDevices(asList(devBody))
      setError('')
    } catch (err) {
      setError(err.message || 'Không thể tải danh sách cảnh báo.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    let timer

    async function poll() {
      await loadAlertsData()
      if (active) timer = setTimeout(poll, 3000)
    }

    timer = setTimeout(poll, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadAlertsData])

  // Xử lý Acknowledge hoặc Resolve cảnh báo
  async function handleAlertAction(alertId, action) {
    setActionLoading(`alert-${alertId}`)
    try {
      await api(`/alerts/${encodeURIComponent(alertId)}/${action}/`, { method: 'POST' })
      showToast?.(
        action === 'acknowledge'
          ? 'Đã xác nhận sự cố cảnh báo.'
          : 'Đã đánh dấu giải quyết sự cố.',
        'success'
      )
      await loadAlertsData()
    } catch (err) {
      showToast?.(err.message || 'Lỗi khi xử lý cảnh báo.', 'error')
    } finally {
      setActionLoading(null)
    }
  }

  // Lọc cảnh báo
  const filteredAlerts = alerts.filter((alert) => {
    const message = alert.message || alert.alert_type || alert.type || ''
    const matchesSearch = String(message).toLowerCase().includes(search.toLowerCase())

    const alertSev = String(alert.severity || '').toLowerCase()
    const matchesSeverity =
      severity === 'all' ||
      alertSev === severity.toLowerCase() ||
      (severity === 'high' && alertSev === 'warning') ||
      (severity === 'low' && alertSev === 'info')

    const rawStatus = String(alert.status || '').toLowerCase()
    const isClosed = ['resolved', 'closed'].includes(rawStatus)
    const isAcked = rawStatus === 'acknowledged'
    const isPending = !isClosed && !isAcked

    let matchesStatus = true
    if (statusFilter === 'unresolved') matchesStatus = !isClosed
    if (statusFilter === 'pending') matchesStatus = isPending
    if (statusFilter === 'acknowledged') matchesStatus = isAcked
    if (statusFilter === 'resolved') matchesStatus = isClosed

    return matchesSearch && matchesSeverity && matchesStatus
  })

  const format = (val) => (val === null || val === undefined || val === '' ? '--' : val)
  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  return (
    <div className="alerts-page">
      <section className="card" aria-labelledby="alerts-heading">
        <div className="card-heading">
          <div>
            <h2 id="alerts-heading">Danh sách cảnh báo & Xử lý sự cố</h2>
            <p>Theo dõi, xác nhận đã xem (ACK) và đánh dấu đã xử lý (Resolve) các cảnh báo hệ thống</p>
          </div>
          <span className="count">{filteredAlerts.length} / {alerts.length} kết quả</span>
        </div>

        {/* Bộ lọc cảnh báo */}
        <div className="filters" style={{ flexWrap: 'wrap' }}>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm theo nội dung cảnh báo..."
            aria-label="Tìm kiếm nội dung cảnh báo"
          />

          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            aria-label="Lọc theo mức độ nghiêm trọng"
          >
            <option value="all">Tất cả mức độ</option>
            <option value="critical">Critical (Nghiêm trọng)</option>
            <option value="high">High / Warning (Cần chú ý)</option>
            <option value="medium">Medium (Trung bình)</option>
            <option value="low">Low / Info (Thông tin)</option>
          </select>

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Lọc theo trạng thái xử lý"
          >
            <option value="all">Tất cả trạng thái</option>
            <option value="unresolved">Chưa giải quyết (Cần xử lý)</option>
            <option value="pending">Chờ xác nhận (Mới kích hoạt)</option>
            <option value="acknowledged">Đã xác nhận (Đang xử lý)</option>
            <option value="resolved">Đã giải quyết (Hoàn tất)</option>
          </select>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nội dung / Loại sự cố</th>
                <th>Tủ phát sinh</th>
                <th>Mức độ</th>
                <th>Trạng thái</th>
                <th>Thời gian phát hiện</th>
                <th>Thao tác xử lý</th>
              </tr>
            </thead>
            <tbody>
              {filteredAlerts.map((alert, index) => {
                const alertId = alert.id || index
                const devId = alert.device_id || alert.device
                const isLoading = actionLoading === `alert-${alertId}`
                const rawStatus = String(alert.status || '').toLowerCase()
                const isClosed = ['resolved', 'closed'].includes(rawStatus)
                const isAcked = rawStatus === 'acknowledged'
                const canOperateThisDevice = devices.some(
                  (d) => (d.id || d.device_id) === devId && d.can_operate
                )

                return (
                  <tr key={alertId}>
                    <td>
                      <strong>{alert.message || alert.alert_type || alert.type || '--'}</strong>
                      {alert.alert_type && alert.message && (
                        <small>{alert.alert_type}</small>
                      )}
                    </td>
                    <td>
                      {devId ? (
                        <Link to={`/devices/${encodeURIComponent(devId)}`} title="Xem chi tiết tủ">
                          <strong>{devId}</strong>
                        </Link>
                      ) : (
                        '--'
                      )}
                    </td>
                    <td>
                      <span className={`severity ${String(alert.severity || '').toLowerCase()}`}>
                        {format(alert.severity)}
                      </span>
                    </td>
                    <td>
                      {isClosed
                        ? 'Đã giải quyết'
                        : isAcked
                        ? 'Đã xác nhận'
                        : 'Chờ xử lý'}
                    </td>
                    <td>{formatDate(alert.created_at || alert.triggered_at)}</td>
                    <td>
                      <div className="btn-group">
                        {!isClosed && !isAcked && (
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            disabled={isLoading || !canOperateThisDevice}
                            onClick={() => handleAlertAction(alertId, 'acknowledge')}
                            title={!canOperateThisDevice ? 'Bạn không có quyền thao tác tủ này' : 'Xác nhận đã xem sự cố'}
                          >
                            Xác nhận (ACK)
                          </button>
                        )}
                        {!isClosed && (
                          <button
                            type="button"
                            className="btn btn-success btn-sm"
                            disabled={isLoading || !canOperateThisDevice}
                            onClick={() => handleAlertAction(alertId, 'resolve')}
                            title={!canOperateThisDevice ? 'Bạn không có quyền thao tác tủ này' : 'Đánh dấu đã giải quyết sự cố'}
                          >
                            Đã giải quyết
                          </button>
                        )}
                        {isClosed && (
                          <span style={{ color: 'var(--success)', fontWeight: 600, fontSize: '12px' }}>
                            ✓ Hoàn tất
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}

              {/* Lỗi tải cảnh báo */}
              {error && !filteredAlerts.length && (
                <tr>
                  <td colSpan="6" className="table-state-error">
                    <div className="table-error-row">
                      <span>⚠️ Không thể tải danh sách cảnh báo ({error}).</span>
                      <button type="button" className="btn btn-outline btn-sm" onClick={loadAlertsData}>
                        Thử lại
                      </button>
                    </div>
                  </td>
                </tr>
              )}

              {/* Đang tải */}
              {loading && !alerts.length && !error && (
                <tr>
                  <td colSpan="6" className="table-state-loading">
                    Đang tải danh sách cảnh báo…
                  </td>
                </tr>
              )}

              {/* Danh sách rỗng */}
              {!filteredAlerts.length && !loading && !error && (
                <tr>
                  <td colSpan="6" className="empty">
                    Không có cảnh báo nào phù hợp với bộ lọc hiện tại.
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
