import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'

/**
 * Trang Cấu hình Kết nối MQTT Broker (MqttSettingsPage - UI-05)
 * Dành riêng cho Quản trị viên (ADMIN).
 *
 * Cho phép:
 * 1. Xem trạng thái kết nối của Broker và tiến trình MQTT Worker theo thời gian thực (Heartbeat, Revision, Lỗi).
 * 2. Cấu hình host, port, TLS (CA PEM), tài khoản Worker và tài khoản Quản trị Mosquitto Dynamic Security.
 * 3. Chức năng "Kiểm tra kết nối" (Thử TCP, Xác thực Worker, Quyền DynSec) trước khi lưu.
 * 4. Chức năng "Lưu & Áp dụng": Cập nhật CSDL, worker tự động reload và kết nối lại mà không cần khởi động lại tiến trình.
 */
export default function MqttSettingsPage({ showToast }) {
  const [settingsData, setSettingsData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState(null)

  // Dữ liệu form cấu hình
  const [form, setForm] = useState({
    host: '127.0.0.1',
    port: 1883,
    use_tls: false,
    ca_cert_pem: '',
    worker_username: '',
    worker_password: '',
    clear_worker_password: false,
    admin_username: '',
    admin_password: '',
    clear_admin_password: false,
  })

  // Tải cấu hình từ API
  const loadSettings = useCallback(async () => {
    try {
      const data = await api('/mqtt/settings/')
      setSettingsData(data)
      setForm((prev) => ({
        ...prev,
        host: data.host || '127.0.0.1',
        port: data.port || 1883,
        use_tls: Boolean(data.use_tls),
        ca_cert_pem: data.ca_cert_pem || '',
        worker_username: data.worker_username || '',
        worker_password: '',
        clear_worker_password: false,
        admin_username: data.admin_username || '',
        admin_password: '',
        clear_admin_password: false,
      }))
      setError('')
    } catch (err) {
      setError(err.message || 'Không thể tải cấu hình Broker.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    const timer = setTimeout(() => {
      if (active) loadSettings()
    }, 0)
  return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadSettings])

  // Polling nhẹ trạng thái worker mỗi 5 giây
  useEffect(() => {
    let active = true
    const interval = setInterval(async () => {
      if (!active) return
      try {
        const data = await api('/mqtt/settings/')
        if (active) setSettingsData(data)
      } catch {
        // bỏ qua lỗi polling
      }
    }, 5000)
    return () => {
      active = false
      clearInterval(interval)
    }
  }, [])

  // Xử lý kiểm tra kết nối thử nghiệm
  const handleTestConnection = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const payload = {
        host: form.host,
        port: parseInt(form.port, 10),
        use_tls: form.use_tls,
        ca_cert_pem: form.ca_cert_pem,
        worker_username: form.worker_username,
        worker_password: form.worker_password,
        admin_username: form.admin_username,
        admin_password: form.admin_password,
      }
      const res = await api('/mqtt/settings/test/', {
        method: 'POST',
        data: payload,
      })
      setTestResult(res)
      if (res.success) {
        showToast?.('Kiểm tra kết nối Broker thành công!', 'success')
      } else {
        showToast?.('Kiểm tra thất bại. Vui lòng xem thông tin chi tiết.', 'error')
      }
    } catch (err) {
      setTestResult({
        success: false,
        steps: [{ name: 'Lỗi gửi yêu cầu', ok: false, message: err.message }],
        message: 'Không thể thực hiện kiểm tra kết nối tới Broker.',
      })
    } finally {
      setTesting(false)
    }
  }

  // Xử lý lưu cấu hình vào CSDL
  const handleSaveSettings = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const payload = {
        host: form.host,
        port: parseInt(form.port, 10),
        use_tls: form.use_tls,
        ca_cert_pem: form.ca_cert_pem,
        worker_username: form.worker_username,
        admin_username: form.admin_username,
      }

      if (form.clear_worker_password) {
        payload.clear_worker_password = true
      } else if (form.worker_password) {
        payload.worker_password = form.worker_password
      }

      if (form.clear_admin_password) {
        payload.clear_admin_password = true
      } else if (form.admin_password) {
        payload.admin_password = form.admin_password
      }

      const updated = await api('/mqtt/settings/', {
        method: 'PATCH',
        data: payload,
      })
      setSettingsData(updated)
      setForm((prev) => ({
        ...prev,
        worker_password: '',
        clear_worker_password: false,
        admin_password: '',
        clear_admin_password: false,
      }))
      showToast?.('Đã lưu cấu hình. Hệ thống đang kết nối lại.', 'success')
    } catch (err) {
      setError(err.message || 'Lỗi khi lưu cấu hình Broker.')
    } finally {
      setSaving(false)
    }
  }

  const workerStatus = settingsData?.worker_status || 'DISCONNECTED'
  const isWorkerConnected = workerStatus === 'CONNECTED'
  const isWorkerReconnecting = workerStatus === 'RECONNECTING'
  const isWorkerError = workerStatus === 'ERROR'

  if (loading && !settingsData) {
    return (
      <div className="card" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        Đang tải thông tin cấu hình MQTT Broker…
      </div>
    )
  }

    if (error && !settingsData) return <div role="alert">{error}<button onClick={loadSettings}>Th? l?i</button></div>
  return (
    <div className="mqtt-settings-page">
      {/* Thông báo lỗi nếu có */}
      {error && (
        <div className="notice-box error" role="alert">
          <span>⚠️</span>
          <div style={{ flex: 1 }}>{error}</div>
          <button type="button" className="btn btn-outline btn-sm" onClick={loadSettings}>
            Thử lại
          </button>
        </div>
      )}

      {/* THẺ 1: TRẠNG THÁI TIẾN TRÌNH MQTT WORKER & BROKER RUNTIME */}
      <section className="card" aria-labelledby="worker-status-heading">
        <div className="card-heading">
          <div>
            <h2 id="worker-status-heading">Trạng thái kết nối hệ thống</h2>

          </div>
          <span className="count">
            Phiên bản cấu hình: v{settingsData?.revision ?? 0}
          </span>
        </div>

        <div className="info-grid">
          <div className="info-item">
            <span className="info-label">Trạng thái Worker</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
              <span
                className={`health-dot ${
                  isWorkerConnected
                    ? 'connected'
                    : isWorkerReconnecting
                    ? 'loading'
                    : isWorkerError
                    ? 'error'
                    : ''
                }`}
                aria-hidden="true"
              />
              <strong className="info-value">
                {isWorkerConnected
                  ? 'Đang hoạt động (Connected)'
                  : isWorkerReconnecting
                  ? 'Đang kết nối lại…'
                  : isWorkerError
                  ? 'Lỗi kết nối'
                  : 'Chưa kết nối (Chưa chạy worker)'}
              </strong>
            </div>
          </div>

          <div className="info-item">
            <span className="info-label">Địa chỉ kết nối hiện tại</span>
            <strong className="info-value">
              {settingsData?.host}:{settingsData?.port} {settingsData?.use_tls ? '(TLS/SSL)' : '(TCP)'}
            </strong>
          </div>

          <div className="info-item">
            <span className="info-label">Cấu hình đang sử dụng</span>
            <strong className="info-value">
              v{settingsData?.worker_last_seen_revision ?? 0}{' '}
              {settingsData?.worker_last_seen_revision === settingsData?.revision ? (
                <span style={{ color: 'var(--success)', fontSize: '12px' }}>✓ Đã đồng bộ</span>
              ) : (
                <span style={{ color: 'var(--warning)', fontSize: '12px' }}>⏱️ Chờ đồng bộ</span>
              )}
            </strong>
          </div>

          <div className="info-item">
            <span className="info-label">Lần cập nhật gần nhất</span>
            <strong className="info-value">
              {settingsData?.worker_heartbeat_at
                ? new Date(settingsData.worker_heartbeat_at).toLocaleTimeString('vi-VN')
                : 'Chưa có tín hiệu'}
            </strong>
          </div>
        </div>

        {settingsData?.worker_last_error && (
          <div className="notice-box error" style={{ marginTop: '16px' }}>
            <span>⚠️</span>
            <span>
              <strong>Lỗi ghi nhận từ Worker:</strong> {settingsData.worker_last_error}
            </span>
          </div>
        )}
      </section>

      {/* THẺ 2: BIỂU MẪU CẤU HÌNH BROKER */}
      <section className="card" style={{ marginTop: '24px' }} aria-labelledby="broker-form-heading">
        <div className="card-heading">
          <div>
            <h2 id="broker-form-heading">Thiết lập kết nối MQTT Broker</h2>

          </div>
        </div>

        <form onSubmit={handleSaveSettings} className="broker-config-form">
          <div className="config-grid">
            <div className="form-group">
              <label htmlFor="mqtt-host">Địa chỉ Host / IP Broker *</label>
              <input
                id="mqtt-host"
                type="text"
                required
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                placeholder="Ví dụ: 127.0.0.1 hoặc mqtt.example.com"
              />
              <small className="field-hint">Địa chỉ IP hoặc tên miền của Mosquitto Broker.</small>
            </div>

            <div className="form-group">
              <label htmlFor="mqtt-port">Cổng kết nối (Port) *</label>
              <input
                id="mqtt-port"
                type="number"
                min="1"
                max="65535"
                required
                value={form.port}
                onChange={(e) => setForm({ ...form, port: e.target.value })}
                placeholder="1883 hoặc 8883 (TLS)"
              />
              <small className="field-hint">Mặc định 1883 cho kết nối thường, 8883 cho kết nối TLS/SSL.</small>
            </div>
          </div>

          <div className="form-group" style={{ marginTop: '8px' }}>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={form.use_tls}
                onChange={(e) => setForm({ ...form, use_tls: e.target.checked })}
              />
              <span>Bật mã hóa đường truyền TLS/SSL</span>
            </label>
            <small className="field-hint" style={{ marginTop: '4px' }}>
              Mã hóa dữ liệu khi kết nối tới máy chủ MQTT.
            </small>
          </div>

          {form.use_tls && (
            <div className="form-group" style={{ marginTop: '12px' }}>
              <label htmlFor="mqtt-ca">Chứng chỉ CA công khai (Định dạng PEM) - Tùy chọn</label>
              <textarea
                id="mqtt-ca"
                rows={5}
                value={form.ca_cert_pem}
                onChange={(e) => setForm({ ...form, ca_cert_pem: e.target.value })}
                placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
              />
              <small className="field-hint">
                Chứng chỉ để xác minh máy chủ. Không nhập khóa riêng.
              </small>
            </div>
          )}

          <hr className="menu-divider" style={{ margin: '24px 0' }} />

          <h3 style={{ fontSize: '15px', margin: '0 0 16px 0', color: 'var(--text-main)' }}>
            1. Tài khoản kết nối hệ thống
          </h3>

          <div className="config-grid">
            <div className="form-group">
              <label htmlFor="worker-user">Tên tài khoản Worker</label>
              <input
                id="worker-user"
                type="text"
                value={form.worker_username}
                onChange={(e) => setForm({ ...form, worker_username: e.target.value })}
                placeholder="Ví dụ: django_worker"
              />
            </div>

            <div className="form-group">
              <label htmlFor="worker-pass">
                Mật khẩu Worker{' '}
                {settingsData?.worker_password_set && (
                  <span style={{ color: 'var(--success)', fontSize: '11px', fontWeight: 600 }}>
                    Đã lưu mật khẩu
                  </span>
                )}
              </label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  id="worker-pass"
                  type="password"
                  value={form.worker_password}
                  onChange={(e) =>
                    setForm({ ...form, worker_password: e.target.value, clear_worker_password: false })
                  }
                  placeholder={
                    settingsData?.worker_password_set
                      ? 'Để trống nếu giữ nguyên mật khẩu cũ'
                      : 'Nhập mật khẩu cho Worker…'
                  }
                  disabled={form.clear_worker_password}
                />
                {settingsData?.worker_password_set && !form.clear_worker_password && (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => {
                      if (window.confirm('Bạn có chắc muốn xóa mật khẩu Worker đã lưu?')) {
                        setForm({ ...form, clear_worker_password: true, worker_password: '' })
                      }
                    }}
                    title="Xóa mật khẩu Worker"
                  >
                    Xóa MK
                  </button>
                )}
                {form.clear_worker_password && (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => setForm({ ...form, clear_worker_password: false })}
                  >
                    Hủy xóa
                  </button>
                )}
              </div>
              {form.clear_worker_password && (
                <small style={{ color: 'var(--danger)', fontSize: '12px' }}>
                  Mật khẩu Worker sẽ bị xóa khi bạn nhấn Lưu.
                </small>
              )}
            </div>
          </div>

          <hr className="menu-divider" style={{ margin: '24px 0' }} />

          <h3 style={{ fontSize: '15px', margin: '0 0 16px 0', color: 'var(--text-main)' }}>
            2. Tài khoản quản trị máy chủ MQTT
          </h3>

          <div className="config-grid">
            <div className="form-group">
              <label htmlFor="admin-user">Tên tài khoản Quản trị Broker</label>
              <input
                id="admin-user"
                type="text"
                value={form.admin_username}
                onChange={(e) => setForm({ ...form, admin_username: e.target.value })}
                placeholder="Ví dụ: mosquitto_admin"
              />
              <small className="field-hint">Dùng để tạo và quản lý tài khoản kết nối của các tủ.</small>
            </div>

            <div className="form-group">
              <label htmlFor="admin-pass">
                Mật khẩu Quản trị{' '}
                {settingsData?.admin_password_set && (
                  <span style={{ color: 'var(--success)', fontSize: '11px', fontWeight: 600 }}>
                    Đã lưu mật khẩu
                  </span>
                )}
              </label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  id="admin-pass"
                  type="password"
                  value={form.admin_password}
                  onChange={(e) =>
                    setForm({ ...form, admin_password: e.target.value, clear_admin_password: false })
                  }
                  placeholder={
                    settingsData?.admin_password_set
                      ? 'Để trống nếu giữ nguyên mật khẩu cũ'
                      : 'Nhập mật khẩu Quản trị…'
                  }
                  disabled={form.clear_admin_password}
                />
                {settingsData?.admin_password_set && !form.clear_admin_password && (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => {
                      if (window.confirm('Bạn có chắc muốn xóa mật khẩu Quản trị đã lưu?')) {
                        setForm({ ...form, clear_admin_password: true, admin_password: '' })
                      }
                    }}
                    title="Xóa mật khẩu Quản trị"
                  >
                    Xóa MK
                  </button>
                )}
                {form.clear_admin_password && (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => setForm({ ...form, clear_admin_password: false })}
                  >
                    Hủy xóa
                  </button>
                )}
              </div>
              {form.clear_admin_password && (
                <small style={{ color: 'var(--danger)', fontSize: '12px' }}>
                  Mật khẩu Quản trị sẽ bị xóa khi bạn nhấn Lưu.
                </small>
              )}
            </div>
          </div>

          {/* KẾT QUẢ KIỂM TRA THỬ NGHIỆM */}
          {testResult && (
            <div
              className={`notice-box ${testResult.success ? 'success' : 'error'}`}
              style={{ marginTop: '24px' }}
            >
              <span>{testResult.success ? '✓' : '⚠️'}</span>
              <div style={{ flex: 1 }}>
                <strong>{testResult.message}</strong>
                <ul style={{ margin: '8px 0 0 0', paddingLeft: '20px', fontSize: '12px' }}>
                  {testResult.steps?.map((step, idx) => (
                    <li key={idx} style={{ color: step.ok ? 'var(--success)' : 'var(--danger)' }}>
                      <strong>{step.name}:</strong> {step.message}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          {/* HÀNG NÚT THAO TÁC */}
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end', marginTop: '28px' }}>
            <button
              type="button"
              className="btn btn-outline"
              disabled={testing || saving}
              onClick={handleTestConnection}
            >
              {testing ? 'Đang kiểm tra kết nối…' : '🔍 Kiểm tra kết nối'}
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving || testing}>
              {saving ? 'Đang lưu & áp dụng…' : '💾 Lưu & Áp dụng cấu hình'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
