import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  asList,
  translateConnectionState,
  translateCommandStatus,
} from '../api.js'
import Pagination from '../components/Pagination.jsx'

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
export default function DeviceDetailPage({ user, showToast }) {
  const { id } = useParams()
  const initializedDevice = useRef(null)
  const [device, setDevice] = useState(null)
  const [telemetry, setTelemetry] = useState([])
  const [telemetryPage, setTelemetryPage] = useState(1)
  const [telemetryPageSize, setTelemetryPageSize] = useState(15)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [commandFeedback, setCommandFeedback] = useState(null)

  // Quản lý MQTT tủ mạng (UI-06)
  const [mqttMeta, setMqttMeta] = useState(null)
  const [mqttLoading, setMqttLoading] = useState(false)
  const [mqttError, setMqttError] = useState('')
  const [showProvisionModal, setShowProvisionModal] = useState(false)
  const [showResetModal, setShowResetModal] = useState(false)
  const [showSecretModal, setShowSecretModal] = useState(false)
  const [secretResult, setSecretResult] = useState(null)
  const [customPassword, setCustomPassword] = useState('')
  const [mqttBusy, setMqttBusy] = useState(false)

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
        if (terminal) {
          // Chỉ cập nhật chế độ theo trạng thái thiết bị đã xác nhận, không đoán từ nút vừa bấm.
          const latestDevice = await api(`/devices/${encodeURIComponent(id)}/`)
          if (active) {
            setDevice(latestDevice)
            setPendingCommandId(null)
          }
          return
        }
      } catch (error) {
        if (active) {
          setCommandFeedback({ type: 'error', message: error.message })
          setPendingCommandId(null)
        }
        return
      }
      if (active) timer = setTimeout(check, 3000)
    }
    check()
    return () => { active = false; clearTimeout(timer) }
  }, [pendingCommandId, id])

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

  // Tải thông tin tài khoản MQTT từ Broker
  const loadMqttMeta = useCallback(async () => {
    if (!id || user?.role !== 'ADMIN') return
    setMqttLoading(true)
    try {
      const res = await api(`/devices/${encodeURIComponent(id)}/mqtt/`)
      setMqttMeta(res)
      setMqttError('')
    } catch (err) {
      setMqttError(err.message || 'Không thể tải thông tin MQTT.')
    } finally {
      setMqttLoading(false)
    }
  }, [id, user?.role])

  useEffect(() => {
    let active = true
    const timer = setTimeout(() => {
      if (active && activeTab === 'mqtt') {
        loadMqttMeta()
      }
    }, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [activeTab, loadMqttMeta])

  // Khởi tạo tài khoản MQTT (Provision)
  const handleProvisionMqtt = async (e) => {
    e.preventDefault()
    setMqttBusy(true)
    try {
      const payload = customPassword ? { password: customPassword } : {}
      const res = await api(`/devices/${encodeURIComponent(id)}/mqtt/provision/`, {
        method: 'POST',
        data: payload,
      })
      setSecretResult(res)
      setShowProvisionModal(false)
      setShowSecretModal(true)
      showToast?.('Khởi tạo tài khoản MQTT thành công!', 'success')
      await loadMqttMeta()
      await loadDeviceDetail()
    } catch (err) {
      showToast?.(err.message || 'Lỗi khi khởi tạo tài khoản MQTT.', 'error')
    } finally {
      setMqttBusy(false)
    }
  }

  // Kích hoạt tài khoản MQTT (Enable)
  const handleEnableMqtt = async () => {
    setMqttBusy(true)
    try {
      await api(`/devices/${encodeURIComponent(id)}/mqtt/enable/`, { method: 'POST' })
      showToast?.('Đã kích hoạt tài khoản MQTT trên Broker!', 'success')
      await loadMqttMeta()
      await loadDeviceDetail()
    } catch (err) {
      showToast?.(err.message || 'Lỗi khi kích hoạt tài khoản MQTT.', 'error')
    } finally {
      setMqttBusy(false)
    }
  }

  // Vô hiệu hóa tài khoản MQTT (Disable)
  const handleDisableMqtt = async () => {
    if (
      !window.confirm(
        `Bạn có chắc chắn muốn vô hiệu hóa tài khoản MQTT của tủ ${id} trên Broker? Tủ sẽ bị ngắt kết nối thật.`
      )
    ) {
      return
    }
    setMqttBusy(true)
    try {
      await api(`/devices/${encodeURIComponent(id)}/mqtt/disable/`, { method: 'POST' })
      showToast?.('Đã vô hiệu hóa tài khoản MQTT trên Broker!', 'success')
      await loadMqttMeta()
      await loadDeviceDetail()
    } catch (err) {
      showToast?.(err.message || 'Lỗi khi vô hiệu hóa tài khoản MQTT.', 'error')
    } finally {
      setMqttBusy(false)
    }
  }

  // Đặt lại mật khẩu MQTT
  const handleResetPasswordMqtt = async (e) => {
    e.preventDefault()
    setMqttBusy(true)
    try {
      const payload = customPassword ? { password: customPassword } : {}
      const res = await api(`/devices/${encodeURIComponent(id)}/mqtt/reset-password/`, {
        method: 'POST',
        data: payload,
      })
      setSecretResult(res)
      setShowResetModal(false)
      setShowSecretModal(true)
      showToast?.('Đã đặt lại mật khẩu MQTT thành công!', 'success')
      await loadMqttMeta()
    } catch (err) {
      showToast?.(err.message || 'Lỗi khi đặt lại mật khẩu MQTT.', 'error')
    } finally {
      setMqttBusy(false)
    }
  }

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
        telemetry_interval_ms: Math.max(1000, Math.round(Number(configForm.telemetry_interval_s) * 1000)),
        manual_timeout_s: Math.max(10, Math.round(Number(configForm.manual_timeout_s))),
      }

      const res = await api(`/devices/${encodeURIComponent(id)}/config/`, {
        method: 'POST',
        data: { desired_config },
      })

      if (res.command_id) {
        setPendingCommandId(res.command_id)
      }

      setConfigFeedback({
        type: 'success',
        message: 'Đã lưu cấu hình. Đang chờ thiết bị xác nhận…',
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

  // Gửi lại cấu hình đã được thiết bị xác nhận (APPLIED)
  async function handleResyncConfig() {
    if (!device?.can_operate) {
      showToast?.('Bạn không có quyền gửi lệnh đồng bộ tủ này.', 'error')
      return
    }
    if (device?.status !== 'ACTIVE') {
      showToast?.(`Tủ mạng đang ở trạng thái ${device.status}, không thể gửi lại cấu hình.`, 'error')
      return
    }
    if (!window.confirm('Bạn có chắc chắn muốn gửi lại cấu hình đã được thiết bị xác nhận (APPLIED) gần nhất?')) {
      return
    }

    setSavingConfig(true)
    setConfigFeedback(null)
    try {
      const res = await api(`/devices/${encodeURIComponent(id)}/resync-config/`, {
        method: 'POST',
      })
      if (res.command_id) {
        setPendingCommandId(res.command_id)
      }
      setConfigFeedback({
        type: 'success',
        message: `Đã phát lệnh gửi lại cấu hình xác nhận v${res.version}! Đang theo dõi phản hồi ACK từ tủ…`,
      })
      showToast?.('Đã phát lệnh gửi lại cấu hình xác nhận.', 'success')
      await loadDeviceDetail()
    } catch (err) {
      setConfigFeedback({
        type: 'error',
        message: err.message || 'Không thể gửi lại cấu hình.',
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
  const mode = device?.latest_mode
  const isAuto = mode === 'AUTO'
  const isManual = mode === 'MANUAL'
  const commandBusy = actionLoading || Boolean(pendingCommandId)

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

      {/* Cảnh báo trạng thái không phải ACTIVE */}
      {device?.status && device.status !== 'ACTIVE' && (
        <div className="notice-box warning" style={{ marginBottom: '20px' }}>
          <span>⚠️</span>
          <span>
            <strong>Trạng thái tủ hiện tại: {device.status}.</strong> Các thao tác gửi lệnh điều khiển quạt, chuyển chế độ và cập nhật cấu hình đang bị tạm khóa.
          </span>
        </div>
      )}

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
                {isFanOn ? 'Bật' : 'Tắt'}
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
        {user?.role === 'ADMIN' && (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'mqtt'}
            className={`tab-btn ${activeTab === 'mqtt' ? 'active' : ''}`}
            onClick={() => setActiveTab('mqtt')}
          >
            📡 Tài khoản MQTT Broker
          </button>
        )}
      </div>

      {/* TAB 1: ĐIỀU KHIỂN QUẠT & CHẾ ĐỘ */}
      {activeTab === 'control' && (
        <section className="card control-tab-card" aria-label="Điều khiển quạt và chế độ vận hành">
          <div className="card-heading">
            <div>
              <h2>Điều khiển quạt</h2>
            </div>
            <span className={`status-pill ${device?.can_operate ? 'online' : 'offline'}`}>
              {device?.can_operate ? 'Có thể điều khiển' : 'Chỉ xem'}
            </span>
          </div>

          {commandFeedback && (
            <div className={`notice-box ${commandFeedback.type}`} role="status">
              <span>{commandFeedback.type === 'success' ? '✓' : 'ℹ'}</span>
              <span>{commandFeedback.message}</span>
            </div>
          )}

          {/* Hộp hướng dẫn giải thích rõ ràng chế độ AUTO vs MANUAL */}
          <div className="control-mode-hint" role="status">
            {isAuto
              ? 'Đang ở chế độ Tự động. Chuyển sang Thủ công để bật/tắt quạt.'
              : isManual
                ? 'Đang ở chế độ Thủ công. Bạn có thể bật/tắt quạt bên dưới.'
                : 'Chưa nhận được chế độ vận hành của thiết bị.'}
          </div>

          {/* Nút chuyển đổi chế độ */}
          <div className="control-group">
            <h3>1. Chế độ vận hành</h3>
            <div className="btn-group">
              <button
                type="button"
                className={`btn ${isAuto ? 'btn-primary' : 'btn-outline'}`}
                disabled={commandBusy || !device?.can_operate || isAuto}
                onClick={() => handleSendCommand({ mode: 'AUTO' }, 'CHUYỂN CHẾ ĐỘ TỰ ĐỘNG')}
              >
                Tự động
              </button>
              <button
                type="button"
                className={`btn ${isManual ? 'btn-primary' : 'btn-outline'}`}
                disabled={commandBusy || !device?.can_operate || isManual}
                onClick={() => handleSendCommand({ mode: 'MANUAL' }, 'CHUYỂN CHẾ ĐỘ THỦ CÔNG')}
              >
                Thủ công
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
                disabled={commandBusy || !device?.can_operate || !isManual || isFanOn}
                onClick={() => handleSendCommand({ command: 'SET_FAN', fan: 'ON' }, 'BẬT QUẠT')}
              >
                Bật quạt
              </button>
              <button
                type="button"
                className="btn btn-outline"
                disabled={commandBusy || !device?.can_operate || !isManual || !isFanOn}
                onClick={() => handleSendCommand({ command: 'SET_FAN', fan: 'OFF' }, 'TẮT QUẠT')}
              >
                Tắt quạt
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

            </div>
          </div>

          {configFeedback && (
            <div className={`notice-box ${configFeedback.type}`} role="status">
              <span>{configFeedback.type === 'success' ? '✓' : '⚠️'}</span>
              <span>{configFeedback.message}</span>
            </div>
          )}

          {/* Đối chiếu song song Cấu hình đã lưu vs Thiết bị đã xác nhận (UI-04) */}
          <div
            style={{
              padding: '16px 20px',
              backgroundColor: 'var(--bg-subtle)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              marginBottom: '20px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <strong style={{ fontSize: '14px', color: 'var(--text-main)' }}>
                Đối chiếu trạng thái cấu hình tủ
              </strong>
              {device?.applied_config &&
              Object.keys(device.applied_config).length > 0 &&
              JSON.stringify(device.desired_config) === JSON.stringify(device.applied_config) ? (
                <span className="status-pill online">✓ Thiết bị đã áp dụng</span>
              ) : (
                <span className="status-pill pending">Đang chờ thiết bị xác nhận</span>
              )}
            </div>

            <div className="config-grid">
              <div className="info-item">
                <span className="info-label">Cấu hình đã lưu</span>
                <span className="info-value" style={{ fontSize: '13px' }}>
                  {device?.desired_config && Object.keys(device.desired_config).length > 0 ? (
                    <>
                      Quạt: Bật {device.desired_config.fan_on_c ?? '--'}°C / Tắt {device.desired_config.fan_off_c ?? '--'}°C<br />
                      Cảnh báo: {device.desired_config.temp_alert_c ?? device.desired_config.max_temp ?? '--'}°C · {device.desired_config.humidity_alert_pct ?? device.desired_config.max_hum ?? '--'}%<br />
                      Chu kỳ: {device.desired_config.telemetry_interval_ms ? `${device.desired_config.telemetry_interval_ms} ms` : '--'} · Chế độ tay: {device.desired_config.manual_timeout_s ? `${device.desired_config.manual_timeout_s} s` : '--'}
                    </>
                  ) : (
                    'Chưa có cấu hình mong muốn.'
                  )}
                </span>
              </div>

              <div className="info-item">
                <span className="info-label">Cấu hình thiết bị đã xác nhận</span>
                <span className="info-value" style={{ fontSize: '13px' }}>
                  {device?.applied_config && Object.keys(device.applied_config).length > 0 ? (
                    <>
                      Quạt: Bật {device.applied_config.fan_on_c ?? '--'}°C / Tắt {device.applied_config.fan_off_c ?? '--'}°C<br />
                      Cảnh báo: {device.applied_config.temp_alert_c ?? device.applied_config.max_temp ?? '--'}°C · {device.applied_config.humidity_alert_pct ?? device.applied_config.max_hum ?? '--'}%<br />
                      Chu kỳ: {device.applied_config.telemetry_interval_ms ? `${device.applied_config.telemetry_interval_ms} ms` : '--'} · Chế độ tay: {device.applied_config.manual_timeout_s ? `${device.applied_config.manual_timeout_s} s` : '--'}
                    </>
                  ) : (
                    'Chưa nhận được phản hồi APPLIED từ thiết bị.'
                  )}
                </span>
              </div>
            </div>
          </div>

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
                <small className="field-hint">Quạt tự bật khi đạt ngưỡng này.</small>
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
                <small className="field-hint">Khoảng thời gian giữa hai lần gửi số đo.</small>
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
                <small className="field-hint">Hết thời gian này, quạt trở về chế độ Tự động.</small>
              </div>
            </div>

            <div className="form-actions" style={{ marginTop: '20px', display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="btn btn-outline"
                disabled={
                  savingConfig ||
                  !device?.can_operate ||
                  device?.status !== 'ACTIVE' ||
                  !device?.applied_config ||
                  Object.keys(device.applied_config).length === 0
                }
                onClick={handleResyncConfig}
                title="Gửi lại đúng snapshot cấu hình mà thiết bị đã từng xác nhận (APPLIED)"
              >
                Gửi lại cấu hình đã xác nhận
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={savingConfig || !device?.can_operate || device?.status !== 'ACTIVE'}
              >
                {savingConfig ? 'Đang gửi lệnh cấu hình…' : '💾 Lưu & Phát lệnh cấu hình'}
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
                  setTelemetryPage(1)
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
                {telemetry
                  .slice((telemetryPage - 1) * telemetryPageSize, telemetryPage * telemetryPageSize)
                  .map((item, idx) => {
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
                            {isOn ? 'Bật' : 'Tắt'}
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

          {/* Phân trang lịch sử số đo */}
          <Pagination
            currentPage={telemetryPage}
            totalItems={telemetry.length}
            pageSize={telemetryPageSize}
            onPageChange={setTelemetryPage}
            onPageSizeChange={setTelemetryPageSize}
            pageSizeOptions={[10, 15, 30, 50]}
          />
        </section>
      )}

      {/* TAB 4: QUẢN TRỊ TÀI KHOẢN MQTT CỦA TỦ (UI-06) */}
      {activeTab === 'mqtt' && user?.role === 'ADMIN' && (
        <section className="card mqtt-tab-card" aria-label="Quản lý tài khoản kết nối MQTT của tủ">
          <div className="card-heading">
            <div>
              <h2>Tài khoản kết nối của tủ {device?.name || id}</h2>

            </div>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={loadMqttMeta}
              disabled={mqttLoading}
            >
              🔄 Tải lại trạng thái
            </button>
          </div>

          <div className="notice-box info" style={{ marginBottom: '20px' }}>
            <span>ℹ️</span>
            <span>
              Tài khoản này giúp tủ kết nối với hệ thống, không dùng để đăng nhập web.
            </span>
          </div>

          {mqttError && (
            <div className="notice-box error" role="alert" style={{ marginBottom: '20px' }}>
              <span>⚠️</span>
              <span>{mqttError}</span>
            </div>
          )}

          {mqttLoading && !mqttMeta ? (
            <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)' }}>
              Đang tải thông tin kết nối…
            </div>
          ) : (
            <>
              {/* Lưới thông tin tài khoản */}
              <div className="info-grid" style={{ marginBottom: '24px' }}>
                <div className="info-item">
                  <span className="info-label">Máy chủ Broker</span>
                  <strong className="info-value">
                    {mqttMeta?.broker_host}:{mqttMeta?.broker_port} {mqttMeta?.use_tls ? '(TLS/SSL)' : '(TCP)'}
                  </strong>
                </div>

                <div className="info-item">
                  <span className="info-label">Tên tài khoản</span>
                  <strong className="info-value">@{mqttMeta?.username}</strong>
                </div>

                <div className="info-item">
                  <span className="info-label">Client ID bắt buộc</span>
                  <strong className="info-value">{mqttMeta?.client_id}</strong>
                </div>

                <div className="info-item">
                  <span className="info-label">Trạng thái trên Broker</span>
                  <div>
                    {!mqttMeta?.broker_connected ? (
                      <span className="status-pill offline" title={mqttMeta?.broker_error}>
                        Mất kết nối tới Broker
                      </span>
                    ) : !mqttMeta?.account_exists ? (
                      <span className="status-pill offline">Chưa khởi tạo trên Broker</span>
                    ) : mqttMeta?.is_disabled ? (
                      <span className="status-pill timeout">Đang bị khóa</span>
                    ) : (
                      <span className="status-pill online">Đang hoạt động</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Phân quyền Topic (ACL) */}
              <div
                style={{
                  padding: '16px 20px',
                  backgroundColor: 'var(--bg-subtle)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-color)',
                  marginBottom: '24px',
                }}
              >
                <strong style={{ display: 'block', marginBottom: '8px', fontSize: '14px', color: 'var(--text-main)' }}>
                  Chi tiết kết nối nâng cao
                </strong>
                <p style={{ margin: '0 0 12px 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                  Tủ chỉ được gửi dữ liệu và nhận lệnh của chính mình.
                </p>
                <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '13px', lineHeight: 1.8 }}>
                  <li>
                    <strong>Gửi số đo viễn trắc (Publish):</strong> <code>{mqttMeta?.topics?.telemetry}</code>
                  </li>
                  <li>
                    <strong>Gửi trạng thái hoạt động (Publish):</strong> <code>{mqttMeta?.topics?.status}</code>
                  </li>
                  <li>
                    <strong>Gửi phản hồi thực thi lệnh ACK (Publish):</strong> <code>{mqttMeta?.topics?.acks}</code>
                  </li>
                  <li>
                    <strong>Nhận lệnh điều khiển từ máy chủ (Subscribe):</strong> <code>{mqttMeta?.topics?.commands}</code>
                  </li>
                </ul>
              </div>

              {/* Các nút thao tác vòng đời */}
              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                {!mqttMeta?.account_exists && (
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={mqttBusy || !mqttMeta?.broker_connected}
                    onClick={() => {
                      setCustomPassword('')
                      setShowProvisionModal(true)
                    }}
                  >
                    ✨ Khởi tạo tài khoản MQTT (Provision)
                  </button>
                )}

                {mqttMeta?.account_exists && mqttMeta?.is_disabled && (
                  <button
                    type="button"
                    className="btn btn-success"
                    disabled={mqttBusy || !mqttMeta?.broker_connected || device?.status === 'DECOMMISSIONED'}
                    onClick={handleEnableMqtt}
                  >
                    ▶️ Kích hoạt tài khoản (Enable)
                  </button>
                )}

                {mqttMeta?.account_exists && !mqttMeta?.is_disabled && (
                  <button
                    type="button"
                    className="btn btn-outline"
                    disabled={mqttBusy || !mqttMeta?.broker_connected}
                    onClick={handleDisableMqtt}
                  >
                    ⏸️ Tạm vô hiệu hóa (Disable)
                  </button>
                )}

                {mqttMeta?.account_exists && (
                  <button
                    type="button"
                    className="btn btn-outline"
                    disabled={mqttBusy || !mqttMeta?.broker_connected}
                    onClick={() => {
                      setCustomPassword('')
                      setShowResetModal(true)
                    }}
                  >
                    🔑 Đặt lại mật khẩu MQTT
                  </button>
                )}
              </div>
            </>
          )}
        </section>
      )}

      {/* MODAL 1: KHỞI TẠO TÀI KHOẢN MQTT */}
      {showProvisionModal && (
        <div className="modal-backdrop" onClick={() => !mqttBusy && setShowProvisionModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Khởi tạo tài khoản MQTT cho tủ {id}</h3>
              <p>Tạo tài khoản để tủ gửi số đo và nhận lệnh điều khiển.</p>
            </div>

            <form onSubmit={handleProvisionMqtt}>
              <div className="form-group">
                <label htmlFor="provision-password">
                  Mật khẩu MQTT tùy chọn (Tối thiểu 12 ký tự)
                </label>
                <input
                  id="provision-password"
                  type="password"
                  value={customPassword}
                  onChange={(e) => setCustomPassword(e.target.value)}
                  placeholder="Để trống để hệ thống tự sinh 16 ký tự an toàn"
                  minLength={12}
                />
                <small className="field-hint">
                  Nếu để trống, hệ thống sẽ tự động sinh mật khẩu ngẫu nhiên có độ phức tạp cao.
                </small>
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={mqttBusy}
                  onClick={() => setShowProvisionModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn btn-primary" disabled={mqttBusy}>
                  {mqttBusy ? 'Đang khởi tạo…' : 'Khởi tạo tài khoản'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: ĐẶT LẠI MẬT KHẨU MQTT */}
      {showResetModal && (
        <div className="modal-backdrop" onClick={() => !mqttBusy && setShowResetModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Đặt lại mật khẩu MQTT cho tủ {id}</h3>
              <p>Đặt mật khẩu kết nối mới cho tủ.</p>
            </div>

            <div className="notice-box warning" style={{ marginBottom: '16px' }}>
              <span>⚠️</span>
              <span>
                <strong>Cảnh báo:</strong> Sau khi đổi mật khẩu, cần cập nhật mật khẩu mới trên thiết bị để tủ kết nối lại.
              </span>
            </div>

            <form onSubmit={handleResetPasswordMqtt}>
              <div className="form-group">
                <label htmlFor="reset-password-input">
                  Mật khẩu mới (Tùy chọn, tối thiểu 12 ký tự)
                </label>
                <input
                  id="reset-password-input"
                  type="password"
                  value={customPassword}
                  onChange={(e) => setCustomPassword(e.target.value)}
                  placeholder="Để trống để hệ thống tự sinh 16 ký tự an toàn"
                  minLength={12}
                />
                <small className="field-hint">
                  Nếu để trống, hệ thống sẽ tự động sinh mật khẩu ngẫu nhiên an toàn.
                </small>
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={mqttBusy}
                  onClick={() => setShowResetModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn btn-primary" disabled={mqttBusy}>
                  {mqttBusy ? 'Đang đổi mật khẩu…' : 'Xác nhận đổi mật khẩu'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 3: HIỂN THỊ MẬT KHẨU DUY NHẤT MỘT LẦN (SECRET VIEWER) */}
      {showSecretModal && secretResult && (
        <div className="modal-backdrop" onClick={() => setShowSecretModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Thông tin kết nối MQTT của tủ</h3>
              <p>{secretResult.message}</p>
            </div>

            <div className="notice-box warning" style={{ marginBottom: '20px' }}>
              <span>⚠️</span>
              <div>
                <strong>LƯU Ý BẢO MẬT QUAN TRỌNG:</strong>
                <p style={{ margin: '4px 0 0 0' }}>{secretResult.note}</p>
              </div>
            </div>

            <div className="form-group">
              <label>Tài khoản và mã kết nối thiết bị</label>
              <input type="text" disabled value={secretResult.username} />
            </div>

            <div className="form-group">
              <label>Mật khẩu MQTT</label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  readOnly
                  value={secretResult.password}
                  style={{
                    fontFamily: 'monospace',
                    fontWeight: 700,
                    letterSpacing: '1px',
                    backgroundColor: '#fffbeb',
                    borderColor: '#fde68a',
                    color: '#92400e',
                  }}
                />
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => {
                    navigator.clipboard.writeText(secretResult.password)
                    showToast?.('Đã sao chép mật khẩu vào bộ nhớ tạm!', 'success')
                  }}
                  title="Sao chép mật khẩu"
                >
                  📋 Sao chép
                </button>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '24px' }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setShowSecretModal(false)}
              >
                Đã sao chép & Đóng hộp thoại
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
