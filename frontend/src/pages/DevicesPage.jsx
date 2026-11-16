import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, asList, translateConnectionState } from '../api.js'
import Pagination from '../components/Pagination.jsx'

/**
 * Trang Danh sách tủ mạng (DevicesPage - UI-04)
 * Cung cấp:
 * 1. Tìm kiếm theo tên hoặc mã tủ
 * 2. Lọc theo trạng thái kết nối (Đang kết nối / Mất kết nối)
 * 3. Xem nhanh số đo nhiệt độ/độ ẩm/quạt và dẫn tới trang chi tiết từng tủ
 * 4. [ADMIN] Thêm tủ mạng mới (mã chữ/số/_/-, tên, loại Thật/Mô phỏng, trạng thái khởi tạo REGISTERED)
 * 5. [ADMIN] Chỉnh sửa tên, phần cứng, chuyển trạng thái Tạm khóa / Ngừng sử dụng có xác nhận
 */
export default function DevicesPage({ user, showToast }) {
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  // Modals quản trị thiết bị (ADMIN)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [selectedDevice, setSelectedDevice] = useState(null)
  const [formBusy, setFormBusy] = useState(false)
  const [formError, setFormError] = useState('')

  // Form thêm thiết bị mới
  const [createForm, setCreateForm] = useState({
    id: '',
    name: '',
    source: 'REAL',
    hardware_version: 'v1.0',
    firmware_version: 'v1.0.0',
  })

  // Form sửa thông tin thiết bị
  const [editForm, setEditForm] = useState({
    name: '',
    hardware_version: '',
    firmware_version: '',
    status: 'ACTIVE',
  })

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

  // Dữ liệu phân trang
  const paginatedDevices = filteredDevices.slice((page - 1) * pageSize, page * pageSize)

  // Xử lý Thêm thiết bị mới
  const handleCreateDevice = async (e) => {
    e.preventDefault()
    setFormBusy(true)
    setFormError('')

    const cleanId = createForm.id.trim()
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(cleanId)) {
      setFormError('Mã tủ chỉ được gồm chữ cái, chữ số, dấu gạch dưới (_) hoặc gạch nối (-), tối đa 64 ký tự.')
      setFormBusy(false)
      return
    }

    try {
      await api('/devices/', {
        method: 'POST',
        data: {
          id: cleanId,
          name: createForm.name.trim(),
          source: createForm.source,
          hardware_version: createForm.hardware_version.trim() || 'v1.0',
          firmware_version: createForm.firmware_version.trim() || 'v1.0.0',
        },
      })
      showToast?.(`Đã thêm tủ mạng ${cleanId} thành công (trạng thái: Chờ kích hoạt).`, 'success')
      setShowCreateModal(false)
      setCreateForm({
        id: '',
        name: '',
        source: 'REAL',
        hardware_version: 'v1.0',
        firmware_version: 'v1.0.0',
      })
      await loadDevices()
    } catch (err) {
      setFormError(err.message || 'Lỗi khi thêm thiết bị mới.')
    } finally {
      setFormBusy(false)
    }
  }

  // Mở modal sửa thiết bị
  const openEditDevice = (dev) => {
    setSelectedDevice(dev)
    setEditForm({
      name: dev.name || '',
      hardware_version: dev.hardware_version || 'v1.0',
      firmware_version: dev.firmware_version || 'v1.0.0',
      status: dev.status || 'ACTIVE',
    })
    setFormError('')
    setShowEditModal(true)
  }

  // Xử lý Cập nhật thông tin/trạng thái thiết bị
  const handleUpdateDevice = async (e) => {
    e.preventDefault()
    if (!selectedDevice) return

    setFormBusy(true)
    setFormError('')

    try {
      await api(`/devices/${encodeURIComponent(selectedDevice.id)}/`, {
        method: 'PATCH',
        data: {
          name: editForm.name.trim(),
          hardware_version: editForm.hardware_version.trim(),
          firmware_version: editForm.firmware_version.trim(),
          status: editForm.status,
        },
      })
      showToast?.(`Đã cập nhật thông tin tủ ${selectedDevice.id}.`, 'success')
      setShowEditModal(false)
      await loadDevices()
    } catch (err) {
      setFormError(err.message || 'Lỗi khi cập nhật tủ mạng.')
    } finally {
      setFormBusy(false)
    }
  }

  const format = (val) => (val === null || val === undefined || val === '' ? '--' : val)
  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  const translateLifecycle = (st) => {
    switch (st) {
      case 'ACTIVE':
        return { label: 'Hoạt động', pill: 'online' }
      case 'REGISTERED':
        return { label: 'Mới đăng ký', pill: 'pending' }
      case 'LOCKED':
        return { label: 'Tạm khóa', pill: 'timeout' }
      case 'DECOMMISSIONED':
        return { label: 'Ngừng dùng', pill: 'offline' }
      case 'MAINTENANCE':
        return { label: 'Bảo trì', pill: 'sent' }
      default:
        return { label: st || '--', pill: '' }
    }
  }

  return (
    <div className="devices-page">
      <section className="card" aria-labelledby="devices-list-heading">
        <div className="card-heading">
          <div>
            <h2 id="devices-list-heading">Danh sách tủ mạng</h2>

          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span className="count">{filteredDevices.length} / {devices.length} tủ</span>
            {user?.role === 'ADMIN' && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  setFormError('')
                  setShowCreateModal(true)
                }}
              >
                + Thêm tủ mạng mới
              </button>
            )}
          </div>
        </div>

        {/* Bộ lọc và Tìm kiếm */}
        <div className="filters">
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            placeholder="Tìm theo tên tủ hoặc mã tủ..."
            aria-label="Tìm kiếm tủ mạng"
          />
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value)
              setPage(1)
            }}
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
                <th>Vòng đời</th>
                <th>Kết nối</th>
                <th>Số đo mới nhất</th>
                <th>Trạng thái quạt</th>
                <th>Lần cuối hoạt động</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {paginatedDevices.map((device, idx) => {
                const devId = device.id || device.device_id
                const conn = translateConnectionState(
                  device.is_online,
                  device.connection_state || device.connection
                )

                const temp = device.latest_temperature
                const humid = device.latest_humidity
                const fanStatus = device.latest_fan_status
                const isFanOn = fanStatus === 'ON' || fanStatus === true || fanStatus === 1
                const lc = translateLifecycle(device.status)

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
                      <span className={`status-pill ${lc.pill}`}>{lc.label}</span>
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
                          {isFanOn ? 'Bật' : 'Tắt'}
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
                      <div className="btn-group">
                        {user?.role === 'ADMIN' && (
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={() => openEditDevice(device)}
                            title="Sửa thông tin và trạng thái tủ"
                          >
                            Sửa
                          </button>
                        )}
                        <Link
                          to={`/devices/${encodeURIComponent(devId)}`}
                          className="btn btn-outline btn-sm"
                          title="Xem thông tin chi tiết và điều khiển tủ"
                        >
                          Chi tiết →
                        </Link>
                      </div>
                    </td>
                  </tr>
                )
              })}

              {/* Lỗi tải danh sách */}
              {error && !filteredDevices.length && (
                <tr>
                  <td colSpan="8" className="table-state-error">
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
                  <td colSpan="8" className="table-state-loading">
                    Đang tải danh sách tủ mạng từ hệ thống…
                  </td>
                </tr>
              )}

              {/* Danh sách rỗng */}
              {!filteredDevices.length && !loading && !error && (
                <tr>
                  <td colSpan="8" className="empty">
                    {search || statusFilter !== 'all'
                      ? 'Không tìm thấy tủ mạng nào phù hợp với bộ lọc hiện tại.'
                      : 'Chưa có tủ mạng nào được gán quyền cho tài khoản của bạn.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Phân trang danh sách tủ */}
        <Pagination
          currentPage={page}
          totalItems={filteredDevices.length}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
        />
      </section>

      {/* MODAL 1: THÊM TỦ MẠNG MỚI (ADMIN) */}
      {showCreateModal && (
        <div className="modal-backdrop" onClick={() => !formBusy && setShowCreateModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Thêm tủ mạng mới</h3>

            </div>

            {formError && (
              <div className="notice-box error" role="alert">
                <span>⚠️</span>
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleCreateDevice}>
              <div className="form-group">
                <label htmlFor="dev-new-id">Mã định danh tủ (Device ID) *</label>
                <input
                  id="dev-new-id"
                  type="text"
                  required
                  maxLength={64}
                  value={createForm.id}
                  onChange={(e) => setCreateForm({ ...createForm, id: e.target.value })}
                  placeholder="Ví dụ: CABINET-02, NODE_SERVER_01"
                  autoFocus
                />
                <small className="field-hint">
                  Chỉ gồm chữ cái, số, gạch dưới (_) hoặc gạch nối (-). <strong>Không thể sửa sau khi tạo.</strong>
                </small>
              </div>

              <div className="form-group">
                <label htmlFor="dev-new-name">Tên hiển thị tủ mạng *</label>
                <input
                  id="dev-new-name"
                  type="text"
                  required
                  maxLength={128}
                  value={createForm.name}
                  onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                  placeholder="Ví dụ: Tủ mạng Phòng Máy Chủ Tầng 2"
                />
              </div>

              <div className="form-group">
                <label htmlFor="dev-new-source">Nguồn thiết bị *</label>
                <select
                  id="dev-new-source"
                  value={createForm.source}
                  onChange={(e) => setCreateForm({ ...createForm, source: e.target.value })}
                >
                  <option value="REAL">Phần cứng vật lý (ESP32)</option>
                  <option value="SIMULATED">Mô phỏng phần mềm</option>
                </select>
              </div>

              <div className="config-grid">
                <div className="form-group">
                  <label htmlFor="dev-new-hw">Phiên bản phần cứng</label>
                  <input
                    id="dev-new-hw"
                    type="text"
                    value={createForm.hardware_version}
                    onChange={(e) => setCreateForm({ ...createForm, hardware_version: e.target.value })}
                    placeholder="v1.0"
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="dev-new-fw">Phiên bản Firmware</label>
                  <input
                    id="dev-new-fw"
                    type="text"
                    value={createForm.firmware_version}
                    onChange={(e) => setCreateForm({ ...createForm, firmware_version: e.target.value })}
                    placeholder="v1.0.0"
                  />
                </div>
              </div>

              <div className="notice-box info" style={{ marginTop: '12px' }}>
                <span>ℹ️</span>
                <span>
                  Tủ mới ở trạng thái <strong>Chờ kích hoạt</strong>. Tạo tài khoản kết nối cho tủ trước khi sử dụng.
                </span>
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={formBusy}
                  onClick={() => setShowCreateModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn btn-primary" disabled={formBusy}>
                  {formBusy ? 'Đang thêm…' : 'Thêm tủ mạng'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: SỬA THÔNG TIN & TRẠNG THÁI TỦ (ADMIN) */}
      {showEditModal && selectedDevice && (
        <div className="modal-backdrop" onClick={() => !formBusy && setShowEditModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Chỉnh sửa tủ mạng: {selectedDevice.id}</h3>

            </div>

            {formError && (
              <div className="notice-box error" role="alert">
                <span>⚠️</span>
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleUpdateDevice}>
              <div className="form-group">
                <label>Mã định danh tủ</label>
                <input type="text" disabled value={selectedDevice.id} />
                <small className="field-hint">Không thể đổi mã tủ sau khi tạo.</small>
              </div>

              <div className="form-group">
                <label htmlFor="dev-edit-name">Tên hiển thị tủ mạng *</label>
                <input
                  id="dev-edit-name"
                  type="text"
                  required
                  maxLength={128}
                  value={editForm.name}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label htmlFor="dev-edit-status">Trạng thái vòng đời *</label>
                <select
                  id="dev-edit-status"
                  value={editForm.status}
                  onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}
                >
                  <option value="ACTIVE">Đang hoạt động (ACTIVE) — Cho phép gửi lệnh và nhận viễn trắc</option>
                  <option value="REGISTERED">Chờ kích hoạt (REGISTERED) — Chưa sẵn sàng nhận lệnh</option>
                  <option value="MAINTENANCE">Đang bảo trì (MAINTENANCE) — Tạm dừng kiểm tra cảnh báo</option>
                  <option value="LOCKED">Đã tạm khóa (LOCKED) — Chặn lệnh điều khiển trong ứng dụng</option>
                  <option value="DECOMMISSIONED">Ngừng sử dụng (DECOMMISSIONED) — Loại biên, bảo lưu lịch sử</option>
                </select>
              </div>

              {(editForm.status === 'LOCKED' || editForm.status === 'DECOMMISSIONED') && (
                <div className="notice-box warning" style={{ marginTop: '12px' }}>
                  <span>⚠️</span>
                  <span>
                    Chuyển sang <strong>{editForm.status === 'LOCKED' ? 'Tạm khóa' : 'Ngừng sử dụng'}</strong>{' '}
                    sẽ chặn lệnh điều khiển và viễn trắc mới. Toàn bộ lịch sử đo đạc và nhật ký kiểm toán cũ vẫn được bảo lưu an toàn.
                  </span>
                </div>
              )}

              <div className="config-grid" style={{ marginTop: '12px' }}>
                <div className="form-group">
                  <label htmlFor="dev-edit-hw">Phiên bản phần cứng</label>
                  <input
                    id="dev-edit-hw"
                    type="text"
                    value={editForm.hardware_version}
                    onChange={(e) => setEditForm({ ...editForm, hardware_version: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="dev-edit-fw">Phiên bản Firmware</label>
                  <input
                    id="dev-edit-fw"
                    type="text"
                    value={editForm.firmware_version}
                    onChange={(e) => setEditForm({ ...editForm, firmware_version: e.target.value })}
                  />
                </div>
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={formBusy}
                  onClick={() => setShowEditModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn btn-primary" disabled={formBusy}>
                  {formBusy ? 'Đang cập nhật…' : 'Lưu thay đổi'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
