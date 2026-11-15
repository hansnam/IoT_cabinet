import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, asList, translateConnectionState } from '../api.js'

/**
 * Trang Tổng quan giám sát hệ thống (OverviewPage)
 * Hiển thị 4 chỉ số tổng hợp toàn hệ thống:
 * 1. Tổng số tủ mạng
 * 2. Số tủ mất kết nối
 * 3. Cảnh báo cần xử lý
 * 4. Số tủ cần chú ý (mất kết nối / nhiệt độ cao / có cảnh báo)
 * Danh sách tủ cần chú ý và cảnh báo mới nhất dẫn trực tiếp tới trang chi tiết.
 */
export default function OverviewPage() {
  const [devices, setDevices] = useState([])
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadOverviewData = useCallback(async () => {
    try {
      const [devBody, alertBody] = await Promise.all([
        api('/devices/'),
        api('/alerts/'),
      ])
      setDevices(asList(devBody))
      setAlerts(asList(alertBody))
      setError('')
    } catch (err) {
      setError(err.message || 'Không thể tải dữ liệu tổng quan.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    let timer

    async function poll() {
      await loadOverviewData()
      if (active) timer = setTimeout(poll, 3000)
    }

    timer = setTimeout(poll, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadOverviewData])

  // Cảnh báo chưa xử lý
  const openAlerts = alerts.filter(
    (a) => !['resolved', 'closed'].includes(String(a.status || '').toLowerCase())
  )

  // Tủ mất kết nối
  const offlineDevices = devices.filter((d) => {
    const conn = translateConnectionState(d.is_online, d.connection_state || d.connection)
    return conn.statusClass === 'offline'
  })

  // Danh sách tủ cần chú ý: mất kết nối, hoặc có cảnh báo active, hoặc nhiệt độ > 35°C
  const attentionDevices = devices.map((d) => {
    const dId = d.id || d.device_id
    const conn = translateConnectionState(d.is_online, d.connection_state || d.connection)
    const isOffline = conn.statusClass === 'offline'
    const temp = d.latest_temperature !== undefined && d.latest_temperature !== null
      ? Number(d.latest_temperature)
      : null
    const isHighTemp = temp !== null && temp >= 35.0
    const devAlerts = openAlerts.filter((a) => (a.device_id || a.device) === dId)

    const reasons = []
    if (isOffline) reasons.push('Mất kết nối với máy chủ')
    if (isHighTemp) reasons.push(`Nhiệt độ cảnh báo (${temp.toFixed(1)} °C)`)
    if (devAlerts.length > 0) reasons.push(`Có ${devAlerts.length} cảnh báo chưa xử lý`)

    return {
      device: d,
      id: dId,
      name: d.name || dId,
      reasons,
      needsAttention: reasons.length > 0,
      alertsCount: devAlerts.length,
      conn,
    }
  }).filter((item) => item.needsAttention)

  const format = (val) => (val === null || val === undefined || val === '' ? '--' : val)
  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  return (
    <div className="overview-page">
      {/* Thông báo lỗi khi không thể tải */}
      {error && (
        <div className="notice-box error" role="alert">
          <span>⚠️</span>
          <div style={{ flex: 1 }}>
            <strong>Không thể kết nối hoặc tải số liệu tổng quan:</strong> {error}
          </div>
          <button type="button" className="btn btn-outline btn-sm" onClick={loadOverviewData}>
            Thử lại
          </button>
        </div>
      )}

      {/* 4 Thẻ Metric Tổng hợp */}
      <section className="cards" aria-label="4 chỉ số tổng quan hệ thống">
        <article className="card metric metric-devices">
          <p>Tổng số tủ mạng</p>
          <h2>{error && !devices.length ? '--' : devices.length}</h2>
          <span>{error && !devices.length ? 'Chưa tải được dữ liệu' : 'Tủ trong phạm vi quản lý'}</span>
        </article>

        <article className="card metric metric-temp">
          <p>Tủ mất kết nối</p>
          <h2 style={{ color: offlineDevices.length > 0 ? 'var(--danger)' : 'var(--success)' }}>
            {error && !devices.length ? '--' : offlineDevices.length}
          </h2>
          <span>
            {error && !devices.length
              ? 'Chưa thể kiểm tra'
              : offlineDevices.length > 0
              ? 'Cần kiểm tra nguồn/mạng tủ'
              : '100% tủ đang kết nối tốt'}
          </span>
        </article>

        <article className="card metric metric-alerts">
          <p>Cảnh báo cần xử lý</p>
          <h2>{error && !alerts.length ? '--' : openAlerts.length}</h2>
          <span>
            {error && !alerts.length
              ? 'Chưa thể kiểm tra'
              : openAlerts.length > 0
              ? 'Sự cố đang chờ xác nhận/xử lý'
              : 'Không có sự cố tồn đọng'}
          </span>
        </article>

        <article className="card metric metric-humid">
          <p>Tủ cần chú ý ngay</p>
          <h2 style={{ color: attentionDevices.length > 0 ? '#ea580c' : 'var(--primary)' }}>
            {error && !devices.length ? '--' : attentionDevices.length}
          </h2>
          <span>
            {error && !devices.length
              ? 'Chưa thể kiểm tra'
              : attentionDevices.length > 0
              ? 'Tủ có cảnh báo hoặc nhiệt độ cao'
              : 'Hệ thống vận hành an toàn'}
          </span>
        </article>
      </section>

      {/* Danh sách Tủ cần chú ý ngay */}
      <section className="card" aria-labelledby="attention-cabinets-heading">
        <div className="card-heading">
          <div>
            <h2 id="attention-cabinets-heading">Tủ cần chú ý ngay</h2>
            <p>Các tủ đang gặp sự cố mất kết nối, quá ngưỡng nhiệt độ hoặc phát sinh cảnh báo</p>
          </div>
          <span className="count">
            {attentionDevices.length} tủ cần xem xét
          </span>
        </div>

        {attentionDevices.length > 0 ? (
          <div className="attention-grid">
            {attentionDevices.map((item) => (
              <div key={item.id} className="attention-card">
                <div className="attention-header">
                  <div>
                    <strong>{item.name}</strong>
                    <small>Mã tủ: {item.id}</small>
                  </div>
                  <span className={`status-pill ${item.conn.statusClass}`}>
                    {item.conn.label}
                  </span>
                </div>
                <div className="attention-reasons">
                  {item.reasons.map((reason, idx) => (
                    <span key={idx} className="reason-tag">
                      ⚠️ {reason}
                    </span>
                  ))}
                </div>
                <div className="attention-footer">
                  <Link to={`/devices/${encodeURIComponent(item.id)}`} className="btn btn-outline btn-sm">
                    Xem chi tiết tủ →
                  </Link>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty" style={{ padding: '28px 16px', color: 'var(--success)' }}>
            {loading && !devices.length ? (
              <span style={{ color: 'var(--text-muted)' }}>Đang tải số liệu tủ mạng…</span>
            ) : error && !devices.length ? (
              <span style={{ color: 'var(--danger)' }}>Không thể tải dữ liệu tủ mạng.</span>
            ) : (
              <span>✓ Tất cả tủ mạng đang hoạt động bình thường, không có sự cố nào cần xử lý.</span>
            )}
          </div>
        )}
      </section>

      {/* Danh sách Cảnh báo mới nhất */}
      <section className="card" aria-labelledby="recent-alerts-heading">
        <div className="card-heading">
          <div>
            <h2 id="recent-alerts-heading">Cảnh báo mới nhất</h2>
            <p>Các sự cố vừa kích hoạt cần người vận hành xác nhận và xử lý</p>
          </div>
          <Link to="/alerts" className="btn btn-outline btn-sm">
            Xem toàn bộ cảnh báo ({openAlerts.length}) →
          </Link>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nội dung / Sự cố</th>
                <th>Tủ phát sinh</th>
                <th>Mức độ</th>
                <th>Thời gian phát hiện</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {openAlerts.slice(0, 5).map((alert, idx) => {
                const aId = alert.id || idx
                const devId = alert.device_id || alert.device
                return (
                  <tr key={aId}>
                    <td>
                      <strong>{alert.message || alert.alert_type || alert.type || '--'}</strong>
                      {alert.alert_type && alert.message && (
                        <small>{alert.alert_type}</small>
                      )}
                    </td>
                    <td>
                      {devId ? (
                        <Link to={`/devices/${encodeURIComponent(devId)}`}>
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
                    <td>{formatDate(alert.created_at || alert.triggered_at)}</td>
                    <td>
                      <Link to="/alerts" className="btn btn-outline btn-sm">
                        Xử lý cảnh báo →
                      </Link>
                    </td>
                  </tr>
                )
              })}

              {!openAlerts.length && !loading && !error && (
                <tr>
                  <td colSpan="5" className="empty">
                    Hiện không có cảnh báo nào chưa xử lý trong hệ thống.
                  </td>
                </tr>
              )}

              {loading && !openAlerts.length && !error && (
                <tr>
                  <td colSpan="5" className="table-state-loading">
                    Đang tải danh sách cảnh báo…
                  </td>
                </tr>
              )}

              {error && !openAlerts.length && (
                <tr>
                  <td colSpan="5" className="table-state-error">
                    <div className="table-error-row">
                      <span>⚠️ Không thể tải danh sách cảnh báo ({error}).</span>
                      <button type="button" className="btn btn-outline btn-sm" onClick={loadOverviewData}>
                        Thử lại
                      </button>
                    </div>
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
