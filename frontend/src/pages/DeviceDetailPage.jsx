import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  asList,
  translateConnectionState,
  translateCommandStatus,
} from '../api.js'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

/**
 * Trang Chi tiết tủ mạng (DeviceDetailPage)
 * URL: /devices/:id
 * Phân chia 4 nhóm chức năng rõ ràng:
 * 1. Thông tin tổng quan tủ & Quyền tài khoản
 * 2. Điều khiển quạt & Chế độ vận hành (có hướng dẫn AUTO/MANUAL)
 * 3. Cấu hình ngưỡng cảnh báo & quạt (Form tiếng Việt, kiểm tra hysteresis)
 * 4. Lịch sử số đo viễn trắc riêng của tủ & Xuất CSV an toàn
 */
export default function DeviceDetailPage({ showToast }) {
  const { id } = useParams()
  const initializedDevice = useRef(null)
  const [device, setDevice] = useState(null)
  const [telemetry, setTelemetry] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [commandFeedback, setCommandFeedback] = useState(null)

  const [pendingCommandId, setPendingCommandId] = useState(null)
  useEffect(() => {
    if (!pendingCommandId) return
    let active = true
    let timer
    async function check() {
      try {
        const command = await api(`/commands/${pendingCommandId}/`)
        if (!active) return
        const terminal = ['APPLIED', 'APPLIED_LATE', 'ACKED', 'FAILED', 'TIMEOUT', 'CANCELLED'].includes(command.status)
        setCommandFeedback({ type: ['APPLIED', 'APPLIED_LATE', 'ACKED'].includes(command.status) ? 'success' : terminal ? 'error' : 'info', message: translateCommandStatus(command.status) })
        if (terminal) return
      } catch (error) {
        if (active) setCommandFeedback({ type: 'error', message: error.message })
        return
      }
      if (active) timer = setTimeout(check, 3000)
    }
    check()
    return () => { active = false; clearTimeout(timer) }
  }, [pendingCommandId])

  // Tab chức năng trong chi tiết tủ: 'control' | 'config' | 'telemetry'
  const [activeTab, setActiveTab] = useState('control')

  // Form cấu hình tủ
  const [configForm, setConfigForm] = useState({
    temp_alert_c: 35,
    humidity_alert_pct: 80,
    fan_on_c: 30,
    fan_off_c: 28,
    telemetry_interval_s: 5,
    manual_timeout_s: 60,
  })
  const [savingConfig, setSavingConfig] = useState(false)
  const [configFeedback, setConfigFeedback] = useState(null)

  // Bộ lọc lịch sử viễn trắc
  const [fromTime, setFromTime] = useState('')
  const [toTime, setToTime] = useState('')

  // Tải chi tiết tủ và lịch sử viễn trắc
  const loadDeviceDetail = useCallback(async () => {
    if (!id) return
    try {
      const [devData, telData] = await Promise.all([
        api(`/devices/${encodeURIComponent(id)}/`),
        api(`/telemetry/?device_id=${encodeURIComponent(id)}&limit=50${fromTime ? '&from_time=' + encodeURIComponent(new Date(fromTime).toISOString()) : ''}${toTime ? '&to_time=' + encodeURIComponent(new Date(toTime).toISOString()) : ''}`),
      ])
      setDevice(devData)
      setTelemetry(asList(telData))
      setError('')

      // Đồng bộ form cấu hình nếu có desired_config
      const cfg = devData.desired_config || {}
      if (initializedDevice.current !== id) {
      initializedDevice.current = id
      setConfigForm((prev) => ({
        temp_alert_c: cfg.temp_alert_c ?? cfg.max_temp ?? prev.temp_alert_c,
        humidity_alert_pct: cfg.humidity_alert_pct ?? cfg.max_hum ?? prev.humidity_alert_pct,
        fan_on_c: cfg.fan_on_c ?? prev.fan_on_c,
        fan_off_c: cfg.fan_off_c ?? prev.fan_off_c,
        telemetry_interval_s: cfg.telemetry_interval_ms
          ? Math.round(cfg.telemetry_interval_ms / 1000)
          : prev.telemetry_interval_s,
        manual_timeout_s: cfg.manual_timeout_s ?? prev.manual_timeout_s,
      }))
      }
    } catch (err) {
      setError(err.message || 'Không thể tải thông tin chi tiết tủ.')
    } finally {
      setLoading(false)
    }
  }, [id, fromTime, toTime])

  useEffect(() => {
    let active = true
    let timer

    async function poll() {
      await loadDeviceDetail()
      if (active) timer = setTimeout(poll, 3000)
    }

    timer = setTimeout(poll, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadDeviceDetail])

  // Gửi lệnh điều khiển quạt hoặc chuyển chế độ
  async function handleSendCommand(payload, actionName) {
    if (!device?.can_operate) {
      showToast?.('Bạn không có quyền điều khiển tủ này.', 'error')
      return
    }

    setActionLoading(true)
    setCommandFeedback({
      type: 'info',
      message: `Đang gửi lệnh ${actionName}... Chờ thiết bị xác nhận qua máy chủ.`,
    })

    try {
      const res = await api(`/devices/${encodeURIComponent(id)}/control/`, {
        method: 'POST',
        data: payload,
      })
      setPendingCommandId(res.command_id)
      setCommandFeedback({
        type: 'info',
        message: res.message || `Đã phát lệnh ${actionName} tới tủ thành công.`,
      })
      showToast?.(`Đã gửi lệnh ${actionName}`, 'success')
      await loadDeviceDetail()
    } catch (err) {
      setCommandFeedback({
        type: 'error',
        message: err.message || 'Lỗi khi gửi lệnh điều khiển.',
      })
      showToast?.(err.message, 'error')
    } finally {
      setActionLoading(false)
    }
  }

  // Lưu cấu hình ngưỡng nhiệt độ & quạt
  async function handleSaveConfig(e) {
    e.preventDefault()
    if (!device?.can_operate) {
      showToast?.('Bạn không có quyền thay đổi cấu hình tủ này.', 'error')
      return
    }

    // Kiểm tra tính hợp lệ của ngưỡng bật/tắt quạt (hysteresis)
    const fanOn = Number(configForm.fan_on_c)
    const fanOff = Number(configForm.fan_off_c)
    if (fanOff >= fanOn) {
      setConfigFeedback({
        type: 'error',
        message: 'Nhiệt độ tắt quạt phải thấp hơn nhiệt độ bật quạt (tối thiểu 1 °C) để tránh quạt bật/tắt liên tục.',
      })
      return
    }

    setSavingConfig(true)
    setConfigFeedback(null)

    try {
      const desired_config = {
        temp_alert_c: Number(configForm.temp_alert_c),
        humidity_alert_pct: Number(configForm.humidity_alert_pct),
        fan_on_c: fanOn,
        fan_off_c: fanOff,
        telemetry_interval_ms: Math.max(1, Math.round(Number(configForm.telemetry_interval_s) * 1000)),
        manual_timeout_s: Math.max(10, Math.round(Number(configForm.manual_timeout_s))),
      }

      await api(`/devices/${encodeURIComponent(id)}/config/`, {
        method: 'POST',
        data: { desired_config },
      })

      setConfigFeedback({
        type: 'success',
        message: 'Đã lưu cấu hình mong muốn! Thiết bị sẽ nhận và áp dụng cấu hình trong chu kỳ gửi lệnh tiếp theo.',
      })
      showToast?.('Đã lưu cấu hình tủ thành công.', 'success')
      await loadDeviceDetail()
    } catch (err) {
      setConfigFeedback({
        type: 'error',
        message: err.message || 'Không thể cập nhật cấu hình tủ.',
      })
    } finally {
      setSavingConfig(false)
    }
  }

  // Xuất file CSV lịch sử viễn trắc của riêng tủ này
  function handleExportCsv() {
    let url = `${API_BASE}/telemetry/export/?device_id=${encodeURIComponent(id)}`
    if (fromTime) url += `&from_time=${encodeURIComponent(new Date(fromTime).toISOString())}`
    if (toTime) url += `&to_time=${encodeURIComponent(new Date(toTime).toISOString())}`
    window.open(url, '_blank')
  }

  const format = (val) => (val === null || val === undefined || val === '' ? '--' : val)
  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  if (loading && !device) {
    return (
      <div className="card" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        Đang tải thông tin chi tiết tủ {id}…
      </div>
    )
  }

  if (error && !device) {
    return (
      <div className="card" style={{ padding: '32px' }}>
        <div className="notice-box error" role="alert">
          <span>⚠️</span>
          <div style={{ flex: 1 }}>
            <strong>Không thể tải dữ liệu tủ:</strong> {error}
          </div>
          <Link to="/devices" className="btn btn-outline btn-sm">
            Quay lại danh sách tủ
          </Link>
        </div>
      </div>
    )
  }

  const conn = translateConnectionState(
    device?.is_online,
    device?.connection_state || device?.connection
  )
  const fanStatus = device?.latest_fan_status
  const isFanOn = fanStatus === 'ON' || fanStatus === true || fanStatus === 1
  const mode = device?.latest_mode || 'AUTO'
  const isAuto = mode === 'AUTO'

  return (
    <div className="device-detail-page">
      {/* Nút quay lại và Tiêu đề tủ */}
      <div className="detail-top-bar">
        <Link to="/devices" className="btn btn-outline btn-sm">
          ← Quay lại danh sách tủ
        </Link>
        <div className="detail-title-wrap">
          <h2>{device?.name || id}</h2>
          <span className={`status-pill ${conn.statusClass}`}>{conn.label}</span>
          <span className="permission-badge">
            {device?.can_operate ? '🔑 Có quyền vận hành' : '👁️ Chỉ xem'}
          </span>
        </div>
      </div>

      {/* Nhóm 1: Thẻ thông tin tổng quan tủ */}
      <section className="card detail-info-card" aria-label="Thông tin tổng quan tủ mạng">
        <div className="info-grid">
          <div className="info-item">
            <span className="info-label">Mã định danh tủ</span>
            <strong className="info-value">{device?.id}</strong>
          </div>
          <div className="info-item">
            <span className="info-label">Tên hiển thị</span>
            <strong className="info-value">{device?.name || '--'}</strong>
          </div>
          <div className="info-item">
            <span className="info-label">Trạng thái vòng đời</span>
            <strong className="info-value">
              {device?.status === 'ACTIVE'
                ? 'Đang hoạt động'
                : device?.status === 'REGISTERED'
                ? 'Mới đăng ký'
                : device?.status === 'LOCKED'
                ? 'Tạm khóa'
                : device?.status === 'DECOMMISSIONED'
                ? 'Ngừng sử dụng'
                : format(device?.status)}
            </strong>
          </div>
          <div className="info-item">
            <span className="info-label">Phiên bản phần cứng / FW</span>
            <strong className="info-value">
              {device?.hardware_version || 'HW v1.0'} ({device?.firmware_version || 'FW v1.0'})
            </strong>
          </div>
          <div className="info-item">
            <span className="info-label">Nhiệt độ hiện tại</span>
            <strong className="info-value temp-text">
              {device?.latest_temperature !== undefined && device?.latest_temperature !== null
                ? `${format(device?.latest_temperature)} °C`
                : '--'}
            </strong>
          </div>
          <div className="info-item">
            <span className="info-label">Độ ẩm hiện tại</span>
            <strong className="info-value humid-text">
              {device?.latest_humidity !== undefined && device?.latest_humidity !== null
                ? `${format(device?.latest_humidity)} %`
                : '--'}
            </strong>
          </div>
          <div className="info-item">
            <span className="info-label">Lần cuối gửi tín hiệu</span>
            <strong className="info-value">{formatDate(device?.last_seen_at)}</strong>
          </div>
          <div className="info-item">
            <span className="info-label">Trạng thái quạt hiện tại</span>
            <strong className="info-value">
              <span className={`fan-badge ${isFanOn ? 'on' : 'off'}`}>
                {isFanOn ? 'BẬT (ON)' : 'TẮT (OFF)'}
              </span>
            </strong>
          </div>
        </div>
      </section>

      {/* Thanh chuyển Tab chức năng */}
      <div className="detail-tabs-bar" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'control'}
          className={`tab-btn ${activeTab === 'control' ? 'active' : ''}`}
          onClick={() => setActiveTab('control')}
        >
          ⚡ Điều khiển quạt & Chế độ
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'config'}
          className={`tab-btn ${activeTab === 'config' ? 'active' : ''}`}
          onClick={() => setActiveTab('config')}
        >
          ⚙️ Cấu hình ngưỡng & Chu kỳ
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'telemetry'}
          className={`tab-btn ${activeTab === 'telemetry' ? 'active' : ''}`}
          onClick={() => setActiveTab('telemetry')}
        >
          📈 Lịch sử số đo ({telemetry.length})
        </button>
      </div>

      {/* TAB 1: ĐIỀU KHIỂN QUẠT & CHẾ ĐỘ */}
      {activeTab === 'control' && (
        <section className="card control-tab-card" aria-label="Điều khiển quạt và chế độ vận hành">
          <div className="card-heading">
            <div>
              <h2>Bảng điều khiển quạt & Chế độ vận hành</h2>
              <p>Chọn chế độ vận hành và bật/tắt quạt làm mát tủ</p>
            </div>
            <span className={`status-pill ${device?.can_operate ? 'online' : 'offline'}`}>
              {device?.can_operate ? 'Cho phép phát lệnh' : 'Chỉ xem - Bị khóa'}
            </span>
          </div>

          {commandFeedback && (
            <div className={`notice-box ${commandFeedback.type}`} role="status">
              <span>{commandFeedback.type === 'success' ? '✓' : 'ℹ'}</span>
              <span>{commandFeedback.message}</span>
            </div>
          )}

          {/* Hộp hướng dẫn giải thích rõ ràng chế độ AUTO vs MANUAL */}
          <div className={`control-guide-box ${isAuto ? 'guide-auto' : 'guide-manual'}`}>
            {isAuto ? (
              <>
                <strong>💡 Chế độ hiện tại: Tự động (AUTO)</strong>
                <p>
                  Quạt tản nhiệt đang tự động bật khi nhiệt độ tủ vượt ngưỡng cài đặt và tắt khi hạ nhiệt an toàn.
                  Nếu bạn muốn bật hoặc tắt quạt theo ý muốn ngay bây giờ, hãy bấm chuyển sang chế độ <strong>Thủ công (MANUAL)</strong>.
                </p>
              </>
            ) : (
              <>
                <strong>⚠️ Chế độ hiện tại: Thủ công (MANUAL)</strong>
                <p>
                  Bạn có thể chủ động Bật hoặc Tắt quạt bên dưới. Hệ thống sẽ tự động chuyển về chế độ Tự động sau thời gian cài đặt an toàn.
                </p>
              </>
            )}
          </div>

          {/* Nút chuyển đổi chế độ */}
          <div className="control-group">
            <h3>1. Chế độ vận hành</h3>
            <div className="btn-group">
              <button
                type="button"
                className={`btn ${isAuto ? 'btn-primary' : 'btn-outline'}`}
                disabled={actionLoading || !device?.can_operate || isAuto}
                onClick={() => handleSendCommand({ mode: 'AUTO' }, 'CHUYỂN CHẾ ĐỘ TỰ ĐỘNG')}
              >
                Tự động (AUTO)
              </button>
              <button
                type="button"
                className={`btn ${!isAuto ? 'btn-primary' : 'btn-outline'}`}
                disabled={actionLoading || !device?.can_operate || !isAuto}
                onClick={() => handleSendCommand({ mode: 'MANUAL' }, 'CHUYỂN CHẾ ĐỘ THỦ CÔNG')}
              >
                Thủ công (MANUAL)
              </button>
            </div>
          </div>

          {/* Nút bật/tắt quạt */}
          <div className="control-group" style={{ marginTop: '24px' }}>
            <h3>2. Lệnh quạt tản nhiệt</h3>
            <div className="btn-group">
              <button
                type="button"
                className="btn btn-success"
                disabled={actionLoading || !device?.can_operate || isFanOn}
                onClick={() => handleSendCommand({ command: 'SET_FAN', fan: 'ON' }, 'BẬT QUẠT')}
              >
                ⚡ Bật quạt (ON)
              </button>
              <button
                type="button"
                className="btn btn-outline"
                disabled={actionLoading || !device?.can_operate || !isFanOn}
                onClick={() => handleSendCommand({ command: 'SET_FAN', fan: 'OFF' }, 'TẮT QUẠT')}
              >
                Tắt quạt (OFF)
              </button>
            </div>
            {!device?.can_operate && (
              <small className="field-hint" style={{ marginTop: '8px', display: 'block' }}>
                Tài khoản của bạn chỉ có quyền xem dữ liệu tủ, không được cấp quyền phát lệnh điều khiển.
              </small>
            )}
          </div>
        </section>
      )}

      {/* TAB 2: CẤU HÌNH NGƯỠNG & CHU KỲ */}
      {activeTab === 'config' && (
        <section className="card config-tab-card" aria-label="Cấu hình ngưỡng cảnh báo và chu kỳ">
          <div className="card-heading">
            <div>
              <h2>Cấu hình ngưỡng hoạt động của tủ</h2>
              <p>Thiết lập ngưỡng kích hoạt cảnh báo, điều kiện tự động bật/tắt quạt và chu kỳ gửi số đo</p>
            </div>
          </div>

          {configFeedback && (
            <div className={`notice-box ${configFeedback.type}`} role="status">
              <span>{configFeedback.type === 'success' ? '✓' : '⚠️'}</span>
              <span>{configFeedback.message}</span>
            </div>
          )}

          <form onSubmit={handleSaveConfig} className="config-form">
            <div className="config-grid">
              <div className="form-field">
                <label htmlFor="cfg-temp-alert">Ngưỡng cảnh báo nhiệt độ cao (°C)</label>
                <input
                  id="cfg-temp-alert"
                  type="number"
                  step="0.5"
                  required
                  value={configForm.temp_alert_c}
                  onChange={(e) => setConfigForm({ ...configForm, temp_alert_c: e.target.value })}
                />
                <small className="field-hint">Hệ thống kích hoạt cảnh báo khi nhiệt độ chạm mức này.</small>
              </div>

              <div className="form-field">
                <label htmlFor="cfg-hum-alert">Ngưỡng cảnh báo độ ẩm cao (%)</label>
                <input
                  id="cfg-hum-alert"
                  type="number"
                  step="1"
                  required
                  value={configForm.humidity_alert_pct}
                  onChange={(e) => setConfigForm({ ...configForm, humidity_alert_pct: e.target.value })}
                />
                <small className="field-hint">Kích hoạt cảnh báo khi độ ẩm trong tủ vượt ngưỡng.</small>
              </div>

              <div className="form-field">
                <label htmlFor="cfg-fan-on">Nhiệt độ tự động bật quạt (°C)</label>
                <input
                  id="cfg-fan-on"
                  type="number"
                  step="0.5"
                  required
                  value={configForm.fan_on_c}
                  onChange={(e) => setConfigForm({ ...configForm, fan_on_c: e.target.value })}
                />
                <small className="field-hint">Khi ở chế độ Tự động, quạt sẽ chạy khi nhiệt độ đạt mức này.</small>
              </div>

              <div className="form-field">
                <label htmlFor="cfg-fan-off">Nhiệt độ tự động tắt quạt (°C)</label>
                <input
                  id="cfg-fan-off"
                  type="number"
                  step="0.5"
                  required
                  value={configForm.fan_off_c}
                  onChange={(e) => setConfigForm({ ...configForm, fan_off_c: e.target.value })}
                />
                <small className="field-hint">Phải thấp hơn nhiệt độ bật ít nhất 1 °C để chống bật/tắt liên tục.</small>
              </div>

              <div className="form-field">
                <label htmlFor="cfg-interval">Chu kỳ gửi số đo viễn trắc (giây)</label>
                <input
                  id="cfg-interval"
                  type="number"
                  min="1"
                  max="3600"
                  required
                  value={configForm.telemetry_interval_s}
                  onChange={(e) => setConfigForm({ ...configForm, telemetry_interval_s: e.target.value })}
                />
                <small className="field-hint">Khoảng thời gian phần cứng tủ đo và gửi dữ liệu lên máy chủ.</small>
              </div>

              <div className="form-field">
                <label htmlFor="cfg-timeout">Thời gian tự hủy chế độ thủ công (giây)</label>
                <input
                  id="cfg-timeout"
                  type="number"
                  min="10"
                  max="3600"
                  required
                  value={configForm.manual_timeout_s}
                  onChange={(e) => setConfigForm({ ...configForm, manual_timeout_s: e.target.value })}
                />
                <small className="field-hint">Tự động trả về Tự động sau thời gian này để đảm bảo an toàn.</small>
              </div>
            </div>

            <div className="form-actions" style={{ marginTop: '20px' }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={savingConfig || !device?.can_operate}
              >
                {savingConfig ? 'Đang lưu cấu hình…' : 'Lưu cấu hình tủ'}
              </button>
            </div>
          </form>
        </section>
      )}

      {/* TAB 3: LỊCH SỬ VIỄN TRẮC & XUẤT CSV */}
      {activeTab === 'telemetry' && (
        <section className="card telemetry-tab-card" aria-label="Lịch sử số đo viễn trắc của tủ">
          <div className="card-heading">
            <div>
              <h2>Lịch sử số đo nhiệt độ & độ ẩm của tủ {device?.name || id}</h2>
              <p>Các lần đo cảm biến được thiết bị ghi nhận và gửi về hệ thống</p>
            </div>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={handleExportCsv}
              title="Xuất file CSV chứa dữ liệu đo đạc của tủ này"
            >
              📥 Xuất file CSV báo cáo
            </button>
          </div>

          {/* Bộ lọc khoảng thời gian */}
          <div className="filters" style={{ flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label htmlFor="filter-from-time" style={{ fontSize: '13px', whiteSpace: 'nowrap' }}>Từ ngày:</label>
              <input
                id="filter-from-time"
                type="datetime-local"
                value={fromTime}
                onChange={(e) => setFromTime(e.target.value)}
                style={{ width: 'auto' }}
              />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label htmlFor="filter-to-time" style={{ fontSize: '13px', whiteSpace: 'nowrap' }}>Đến ngày:</label>
              <input
                id="filter-to-time"
                type="datetime-local"
                value={toTime}
                onChange={(e) => setToTime(e.target.value)}
                style={{ width: 'auto' }}
              />
            </div>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={async () => {
                try {
                  const params = new URLSearchParams({ device_id: id })
                  if (fromTime) params.set('from_time', new Date(fromTime).toISOString())
                  if (toTime) params.set('to_time', new Date(toTime).toISOString())
                  setTelemetry(asList(await api(`/telemetry/?${params}`)))
                } catch (error) { showToast?.(error.message, 'error') }
              }}
              title="Tải lại danh sách số đo"
            >
              Lọc số đo
            </button>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thời gian đo</th>
                  <th>Nhiệt độ</th>
                  <th>Độ ẩm</th>
                  <th>Trạng thái quạt</th>
                  <th>Chế độ</th>
                </tr>
              </thead>
              <tbody>
                {telemetry.map((item, idx) => {
                  const tVal = item.temperature ?? item.temperature_c
                  const hVal = item.humidity ?? item.humidity_pct
                  const fVal = item.fan_status !== undefined ? item.fan_status : item.fan_output
                  const isOn = fVal === 'ON' || fVal === true || fVal === 1

                  return (
                    <tr key={item.id || item.measured_at || idx}>
                      <td>{formatDate(item.measured_at || item.received_at)}</td>
                      <td className="temp-text">{format(tVal)} °C</td>
                      <td className="humid-text">{format(hVal)} %</td>
                      <td>
                        {fVal !== undefined && fVal !== null && fVal !== '' ? (
                          <span className={`fan-badge ${isOn ? 'on' : 'off'}`}>
                            {isOn ? 'BẬT (ON)' : 'TẮT (OFF)'}
                          </span>
                        ) : (
                          '--'
                        )}
                      </td>
                      <td>
                        {item.mode ? (
                          <span>{item.mode === 'AUTO' ? 'Tự động' : 'Thủ công'}</span>
                        ) : (
                          '--'
                        )}
                      </td>
                    </tr>
                  )
                })}

                {!telemetry.length && (
                  <tr>
                    <td colSpan="5" className="empty">
                      Chưa nhận được bản ghi số đo nào từ tủ này.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
