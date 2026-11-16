import { useCallback, useEffect, useState } from 'react'
import { api, asList, translateRole } from '../api.js'
import Pagination from '../components/Pagination.jsx'

/**
 * Trang Quản trị Tài khoản & Phân quyền tủ mạng (AccountsPage)
 * Chỉ dành cho Quản trị viên (ADMIN).
 * Cung cấp:
 * 1. Quản lý danh sách tài khoản: Thêm, sửa, đổi vai trò, tạm khóa, ngừng sử dụng, đặt lại mật khẩu tạm
 * 2. Phân quyền tủ mạng: Cấp quyền xem (VIEW) / vận hành (OPERATE) từng tủ cho người dùng, thu hồi quyền
 */
export default function AccountsPage({ showToast }) {
  const [activeTab, setActiveTab] = useState('users') // 'users' | 'access'
  const [users, setUsers] = useState([])
  const [grants, setGrants] = useState([])
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Bộ lọc danh sách người dùng
  const [userSearch, setUserSearch] = useState('')
  const [userRoleFilter, setUserRoleFilter] = useState('all')
  const [userStatusFilter, setUserStatusFilter] = useState('all')
  const [userPage, setUserPage] = useState(1)
  const [userPageSize, setUserPageSize] = useState(10)

  // Phân trang danh sách cấp quyền
  const [grantPage, setGrantPage] = useState(1)
  const [grantPageSize, setGrantPageSize] = useState(10)

  // Trạng thái modal
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const [showGrantModal, setShowGrantModal] = useState(false)
  const [selectedUser, setSelectedUser] = useState(null)

  // Form tạo tài khoản
  const [createForm, setCreateForm] = useState({
    username: '',
    full_name: '',
    email: '',
    role: 'OPERATOR',
    status: 'ACTIVE',
    temporary_password: '',
  })
  const [formBusy, setFormBusy] = useState(false)
  const [formError, setFormError] = useState('')

  // Form sửa tài khoản
  const [editForm, setEditForm] = useState({
    full_name: '',
    email: '',
    role: 'OPERATOR',
    status: 'ACTIVE',
  })

  // Form đặt lại mật khẩu tạm
  const [resetPasswordForm, setResetPasswordForm] = useState({
    temporary_password: '',
    confirm_password: '',
  })

  // Form cấp quyền tủ
  const [grantForm, setGrantForm] = useState({
    user_id: '',
    device_id: '',
    role: 'VIEW',
    expires_at: '',
  })

  // Tải danh sách người dùng, quyền tủ và danh sách thiết bị
  const loadData = useCallback(async () => {
    try {
      const [usersRes, grantsRes, devRes] = await Promise.all([
        api('/management/users/'),
        api('/device-access/'),
        api('/devices/'),
      ])
      setUsers(asList(usersRes))
      setGrants(asList(grantsRes))
      setDevices(asList(devRes))
      setError('')
    } catch (err) {
      setError(err.message || 'Không thể tải dữ liệu quản trị.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    const timer = setTimeout(() => {
      if (active) loadData()
    }, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadData])

  // Lọc người dùng
  const filteredUsers = users.filter((u) => {
    const q = userSearch.trim().toLowerCase()
    const matchesSearch =
      !q ||
      String(u.username || '').toLowerCase().includes(q) ||
      String(u.full_name || '').toLowerCase().includes(q) ||
      String(u.email || '').toLowerCase().includes(q)

    const matchesRole = userRoleFilter === 'all' || u.role === userRoleFilter
    const matchesStatus = userStatusFilter === 'all' || u.status === userStatusFilter

    return matchesSearch && matchesRole && matchesStatus
  })

  // Dữ liệu phân trang
  const paginatedUsers = filteredUsers.slice((userPage - 1) * userPageSize, userPage * userPageSize)
  const paginatedGrants = grants.slice((grantPage - 1) * grantPageSize, grantPage * grantPageSize)

  // Xử lý tạo người dùng mới
  async function handleCreateUser(e) {
    e.preventDefault()
    setFormBusy(true)
    setFormError('')

    if (createForm.temporary_password.length < 12) {
      setFormError('Mật khẩu tạm thời phải có tối thiểu 12 ký tự.')
      setFormBusy(false)
      return
    }

    try {
      await api('/management/users/', {
        method: 'POST',
        data: {
          username: createForm.username.trim(),
          full_name: createForm.full_name.trim(),
          email: createForm.email.trim(),
          role: createForm.role,
          status: createForm.status,
          temporary_password: createForm.temporary_password,
        },
      })
      showToast?.('Đã tạo tài khoản người dùng thành công.', 'success')
      setShowCreateModal(false)
      setCreateForm({
        username: '',
        full_name: '',
        email: '',
        role: 'OPERATOR',
        status: 'ACTIVE',
        temporary_password: '',
      })
      await loadData()
    } catch (err) {
      setFormError(err.message || 'Lỗi khi tạo tài khoản.')
    } finally {
      setFormBusy(false)
    }
  }

  // Mở modal sửa thông tin tài khoản
  function openEditModal(targetUser) {
    setSelectedUser(targetUser)
    setEditForm({
      full_name: targetUser.full_name || '',
      email: targetUser.email || '',
      role: targetUser.role || 'OPERATOR',
      status: targetUser.status || 'ACTIVE',
    })
    setFormError('')
    setShowEditModal(true)
  }

  // Xử lý lưu thông tin sửa
  async function handleUpdateUser(e) {
    e.preventDefault()
    if (!selectedUser) return
    setFormBusy(true)
    setFormError('')

    try {
      await api(`/management/users/${selectedUser.id}/`, {
        method: 'PATCH',
        data: {
          full_name: editForm.full_name.trim(),
          email: editForm.email.trim(),
          role: editForm.role,
          status: editForm.status,
        },
      })
      showToast?.('Đã cập nhật thông tin tài khoản thành công.', 'success')
      setShowEditModal(false)
      await loadData()
    } catch (err) {
      setFormError(err.message || 'Lỗi khi cập nhật tài khoản.')
    } finally {
      setFormBusy(false)
    }
  }

  // Mở modal đặt lại mật khẩu tạm
  function openPasswordModal(targetUser) {
    setSelectedUser(targetUser)
    setResetPasswordForm({ temporary_password: '', confirm_password: '' })
    setFormError('')
    setShowPasswordModal(true)
  }

  // Xử lý đặt lại mật khẩu tạm
  async function handleResetPassword(e) {
    e.preventDefault()
    if (!selectedUser) return
    setFormBusy(true)
    setFormError('')

    if (resetPasswordForm.temporary_password.length < 12) {
      setFormError('Mật khẩu tạm thời phải có tối thiểu 12 ký tự.')
      setFormBusy(false)
      return
    }

    if (resetPasswordForm.temporary_password !== resetPasswordForm.confirm_password) {
      setFormError('Mật khẩu xác nhận không khớp.')
      setFormBusy(false)
      return
    }

    try {
      await api(`/management/users/${selectedUser.id}/`, {
        method: 'PATCH',
        data: {
          temporary_password: resetPasswordForm.temporary_password,
        },
      })
      showToast?.(`Đã đặt lại mật khẩu tạm cho @${selectedUser.username}.`, 'success')
      setShowPasswordModal(false)
      await loadData()
    } catch (err) {
      setFormError(err.message || 'Lỗi khi đặt lại mật khẩu.')
    } finally {
      setFormBusy(false)
    }
  }

  // Khóa / Mở khóa nhanh tài khoản
  async function handleToggleStatus(targetUser) {
    const isLocking = targetUser.status === 'ACTIVE'
    const newStatus = isLocking ? 'LOCKED' : 'ACTIVE'
    const actionName = isLocking ? 'tạm khóa' : 'mở khóa'

    if (!window.confirm(`Bạn có chắc chắn muốn ${actionName} tài khoản @${targetUser.username}?`)) {
      return
    }

    try {
      await api(`/management/users/${targetUser.id}/`, {
        method: 'PATCH',
        data: { status: newStatus },
      })
      showToast?.(`Đã ${actionName} tài khoản @${targetUser.username}.`, 'success')
      await loadData()
    } catch (err) {
      showToast?.(err.message || `Không thể ${actionName} tài khoản.`, 'error')
    }
  }

  // Mở modal cấp quyền truy cập tủ
  function openGrantModal(defaultUserId = '') {
    setGrantForm({
      user_id: defaultUserId || (users[0]?.id || ''),
      device_id: devices[0]?.id || '',
      role: 'VIEW',
      expires_at: '',
    })
    setFormError('')
    setShowGrantModal(true)
  }

  // Xử lý cấp quyền tủ
  async function handleGrantAccess(e) {
    e.preventDefault()
    setFormBusy(true)
    setFormError('')

    try {
      const payload = {
        user_id: grantForm.user_id,
        device_id: grantForm.device_id,
        role: grantForm.role,
        expires_at: grantForm.expires_at ? new Date(grantForm.expires_at).toISOString() : null,
      }
      await api('/device-access/', {
        method: 'POST',
        data: payload,
      })
      showToast?.('Đã cấp quyền truy cập tủ thành công.', 'success')
      setShowGrantModal(false)
      await loadData()
    } catch (err) {
      setFormError(err.message || 'Lỗi khi cấp quyền tủ.')
    } finally {
      setFormBusy(false)
    }
  }

  // Xử lý thu hồi quyền tủ
  async function handleRevokeGrant(grantId, username, deviceId) {
    if (!window.confirm(`Bạn có chắc chắn muốn thu hồi quyền truy cập tủ ${deviceId} của người dùng @${username}?`)) {
      return
    }

    try {
      await api(`/device-access/${grantId}/`, { method: 'DELETE' })
      showToast?.(`Đã thu hồi quyền tủ ${deviceId}.`, 'success')
      await loadData()
    } catch (err) {
      showToast?.(err.message || 'Không thể thu hồi quyền.', 'error')
    }
  }

  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  return (
    <div className="accounts-page">
      {/* Thông báo lỗi nếu có */}
      {error && (
        <div className="notice-box error" role="alert">
          <span>⚠️</span>
          <div style={{ flex: 1 }}>{error}</div>
          <button type="button" className="btn btn-outline btn-sm" onClick={loadData}>
            Thử lại
          </button>
        </div>
      )}

      {/* Tabs Quản trị: Danh sách người dùng vs Phân quyền tủ */}
      <div className="detail-tabs-bar" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'users'}
          className={`tab-btn ${activeTab === 'users' ? 'active' : ''}`}
          onClick={() => setActiveTab('users')}
        >
          👤 Danh sách tài khoản ({users.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'access'}
          className={`tab-btn ${activeTab === 'access' ? 'active' : ''}`}
          onClick={() => setActiveTab('access')}
        >
          🔑 Phân quyền truy cập tủ ({grants.length})
        </button>
      </div>

      {/* TAB 1: DANH SÁCH TÀI KHOẢN */}
      {activeTab === 'users' && (
        <section className="card" aria-labelledby="users-management-heading">
          <div className="card-heading">
            <div>
              <h2 id="users-management-heading">Quản lý tài khoản người dùng</h2>
              <p>Khởi tạo, sửa thông tin, đặt lại mật khẩu tạm và quản lý trạng thái tài khoản</p>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setFormError('')
                setShowCreateModal(true)
              }}
            >
              + Thêm tài khoản mới
            </button>
          </div>

          {/* Bộ lọc người dùng */}
          <div className="filters" style={{ flexWrap: 'wrap' }}>
            <input
              type="text"
              value={userSearch}
              onChange={(e) => {
                setUserSearch(e.target.value)
                setUserPage(1)
              }}
              placeholder="Tìm theo tên đăng nhập, họ tên hoặc email..."
              aria-label="Tìm kiếm tài khoản"
            />
            <select
              value={userRoleFilter}
              onChange={(e) => {
                setUserRoleFilter(e.target.value)
                setUserPage(1)
              }}
              aria-label="Lọc theo vai trò"
            >
              <option value="all">Tất cả vai trò</option>
              <option value="ADMIN">Quản trị viên (ADMIN)</option>
              <option value="OPERATOR">Người vận hành (OPERATOR)</option>
              <option value="VIEWER">Người xem (VIEWER)</option>
            </select>
            <select
              value={userStatusFilter}
              onChange={(e) => {
                setUserStatusFilter(e.target.value)
                setUserPage(1)
              }}
              aria-label="Lọc theo trạng thái"
            >
              <option value="all">Tất cả trạng thái</option>
              <option value="ACTIVE">Đang hoạt động</option>
              <option value="LOCKED">Đã tạm khóa</option>
              <option value="ARCHIVED">Đã ngừng dùng</option>
            </select>
          </div>

          {/* Bảng danh sách tài khoản */}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tài khoản & Họ tên</th>
                  <th>Email</th>
                  <th>Vai trò</th>
                  <th>Trạng thái</th>
                  <th>Số tủ được cấp</th>
                  <th>Thao tác quản trị</th>
                </tr>
              </thead>
              <tbody>
                {paginatedUsers.map((u) => {
                  const isLocked = u.status === 'LOCKED'
                  const isArchived = u.status === 'ARCHIVED'
                  const isRoleAdmin = u.role === 'ADMIN'

                  return (
                    <tr key={u.id}>
                      <td>
                        <strong>{u.full_name || '--'}</strong>
                        <small>@{u.username}</small>
                      </td>
                      <td>{u.email || '--'}</td>
                      <td>
                        <span className={`status-pill ${isRoleAdmin ? 'sent' : 'pending'}`}>
                          {translateRole(u.role)}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`status-pill ${
                            u.status === 'ACTIVE'
                              ? 'online'
                              : isLocked
                              ? 'timeout'
                              : 'offline'
                          }`}
                        >
                          {u.status === 'ACTIVE'
                            ? 'Hoạt động'
                            : isLocked
                            ? 'Tạm khóa'
                            : 'Đã ngừng dùng'}
                        </span>
                      </td>
                      <td>
                        {isRoleAdmin ? (
                          <span title="Quản trị viên có quyền trên tất cả các tủ">Toàn bộ tủ ({u.accessible_devices_count})</span>
                        ) : (
                          <span>{u.accessible_devices_count} tủ</span>
                        )}
                      </td>
                      <td>
                        <div className="btn-group">
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={() => openEditModal(u)}
                            title="Sửa họ tên, email, vai trò"
                          >
                            Sửa
                          </button>
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={() => openPasswordModal(u)}
                            title="Đặt lại mật khẩu tạm"
                          >
                            Đặt lại mật khẩu
                          </button>
                          {!isArchived && (
                            <button
                              type="button"
                              className={`btn btn-sm ${isLocked ? 'btn-success' : 'btn-outline'}`}
                              onClick={() => handleToggleStatus(u)}
                              title={isLocked ? 'Mở khóa tài khoản' : 'Tạm khóa tài khoản'}
                            >
                              {isLocked ? 'Mở khóa' : 'Khóa'}
                            </button>
                          )}
                          {!isRoleAdmin && (
                            <button
                              type="button"
                              className="btn btn-outline btn-sm"
                              onClick={() => {
                                setActiveTab('access')
                                openGrantModal(u.id)
                              }}
                              title="Cấp quyền truy cập tủ"
                            >
                              Cấp quyền tủ
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}

                {!filteredUsers.length && !loading && (
                  <tr>
                    <td colSpan="6" className="empty">
                      Không tìm thấy tài khoản người dùng nào phù hợp.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Phân trang danh sách người dùng */}
          <Pagination
            currentPage={userPage}
            totalItems={filteredUsers.length}
            pageSize={userPageSize}
            onPageChange={setUserPage}
            onPageSizeChange={setUserPageSize}
          />
        </section>
      )}

      {/* TAB 2: PHÂN QUYỀN TRUY CẬP TỦ */}
      {activeTab === 'access' && (
        <section className="card" aria-labelledby="access-management-heading">
          <div className="card-heading">
            <div>
              <h2 id="access-management-heading">Phân quyền truy cập tủ mạng</h2>
              <p>Chỉ định rõ người dùng được xem hoặc được phép điều khiển những tủ nào</p>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => openGrantModal()}
            >
              + Cấp quyền tủ mới
            </button>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Người dùng</th>
                  <th>Tủ mạng được cấp</th>
                  <th>Mức quyền</th>
                  <th>Thời hạn</th>
                  <th>Người cấp</th>
                  <th>Thời điểm cấp</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {paginatedGrants.map((grant) => (
                  <tr key={grant.id}>
                    <td>
                      <strong>{grant.user_full_name || grant.username}</strong>
                      <small>@{grant.username}</small>
                    </td>
                    <td>
                      <strong>{grant.device_name || grant.device}</strong>
                      <small>Mã tủ: {grant.device}</small>
                    </td>
                    <td>
                      <span className={`status-pill ${grant.role === 'OPERATE' ? 'online' : 'sent'}`}>
                        {grant.role === 'OPERATE' ? 'Được điều khiển (OPERATE)' : 'Chỉ xem (VIEW)'}
                      </span>
                    </td>
                    <td>{grant.expires_at ? formatDate(grant.expires_at) : 'Vô thời hạn'}</td>
                    <td>{grant.granted_by_username ? `@${grant.granted_by_username}` : 'Hệ thống'}</td>
                    <td>{formatDate(grant.granted_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => handleRevokeGrant(grant.id, grant.username, grant.device)}
                        title="Thu hồi quyền truy cập tủ này"
                      >
                        Thu hồi
                      </button>
                    </td>
                  </tr>
                ))}

                {!grants.length && !loading && (
                  <tr>
                    <td colSpan="7" className="empty">
                      Chưa có bản ghi phân quyền tủ nào được khởi tạo.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Phân trang danh sách cấp quyền */}
          <Pagination
            currentPage={grantPage}
            totalItems={grants.length}
            pageSize={grantPageSize}
            onPageChange={setGrantPage}
            onPageSizeChange={setGrantPageSize}
          />
        </section>
      )}

      {/* MODAL 1: THÊM TÀI KHOẢN MỚI */}
      {showCreateModal && (
        <div className="modal-backdrop" onClick={() => !formBusy && setShowCreateModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Thêm tài khoản người dùng mới</h3>

            </div>

            {formError && (
              <div className="notice-box error" role="alert">
                <span>⚠️</span>
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleCreateUser}>
              <div className="form-group">
                <label htmlFor="create-username">Tên đăng nhập *</label>
                <input
                  id="create-username"
                  type="text"
                  required
                  value={createForm.username}
                  onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })}
                  placeholder="Ví dụ: nva_operator (3–64 ký tự thường, số, dấu chấm)"
                  autoFocus
                />
                <small className="field-hint">Tên đăng nhập không thể thay đổi sau khi tạo.</small>
              </div>

              <div className="form-group">
                <label htmlFor="create-fullname">Họ và tên *</label>
                <input
                  id="create-fullname"
                  type="text"
                  required
                  value={createForm.full_name}
                  onChange={(e) => setCreateForm({ ...createForm, full_name: e.target.value })}
                  placeholder="Ví dụ: Nguyễn Văn A"
                />
              </div>

              <div className="form-group">
                <label htmlFor="create-email">Địa chỉ Email</label>
                <input
                  id="create-email"
                  type="email"
                  value={createForm.email}
                  onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                  placeholder="Ví dụ: nva@example.com"
                />
              </div>

              <div className="form-group">
                <label htmlFor="create-role">Vai trò hệ thống *</label>
                <select
                  id="create-role"
                  value={createForm.role}
                  onChange={(e) => setCreateForm({ ...createForm, role: e.target.value })}
                >
                  <option value="OPERATOR">Người vận hành (OPERATOR) — Được điều khiển tủ được phân quyền</option>
                  <option value="VIEWER">Người xem (VIEWER) — Chỉ xem số đo và cảnh báo, không được gửi lệnh</option>
                  <option value="ADMIN">Quản trị viên (ADMIN) — Toàn quyền hệ thống</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="create-password">Mật khẩu tạm thời * (tối thiểu 12 ký tự)</label>
                <input
                  id="create-password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={createForm.temporary_password}
                  onChange={(e) => setCreateForm({ ...createForm, temporary_password: e.target.value })}
                  placeholder="Nhập mật khẩu tạm an toàn..."
                />
                <small className="field-hint">Người dùng bắt buộc phải đổi mật khẩu ở lần đăng nhập đầu tiên.</small>
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
                  {formBusy ? 'Đang tạo…' : 'Tạo tài khoản'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: SỬA THÔNG TIN TÀI KHOẢN */}
      {showEditModal && selectedUser && (
        <div className="modal-backdrop" onClick={() => !formBusy && setShowEditModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Sửa tài khoản @{selectedUser.username}</h3>

            </div>

            {formError && (
              <div className="notice-box error" role="alert">
                <span>⚠️</span>
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleUpdateUser}>
              <div className="form-group">
                <label>Tên đăng nhập</label>
                <input type="text" value={selectedUser.username} disabled readOnly />
                <small className="field-hint">Tên đăng nhập không thể sửa đổi.</small>
              </div>

              <div className="form-group">
                <label htmlFor="edit-fullname">Họ và tên *</label>
                <input
                  id="edit-fullname"
                  type="text"
                  required
                  value={editForm.full_name}
                  onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label htmlFor="edit-email">Email</label>
                <input
                  id="edit-email"
                  type="email"
                  value={editForm.email}
                  onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                />
              </div>

              <div className="form-group">
                <label htmlFor="edit-role">Vai trò</label>
                <select
                  id="edit-role"
                  value={editForm.role}
                  onChange={(e) => setEditForm({ ...editForm, role: e.target.value })}
                >
                  <option value="OPERATOR">Người vận hành (OPERATOR)</option>
                  <option value="VIEWER">Người xem (VIEWER)</option>
                  <option value="ADMIN">Quản trị viên (ADMIN)</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="edit-status">Trạng thái tài khoản</label>
                <select
                  id="edit-status"
                  value={editForm.status}
                  onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}
                >
                  <option value="ACTIVE">Hoạt động (ACTIVE)</option>
                  <option value="LOCKED">Tạm khóa (LOCKED)</option>
                  <option value="ARCHIVED">Ngừng sử dụng (ARCHIVED)</option>
                </select>
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
                  {formBusy ? 'Đang lưu…' : 'Lưu thay đổi'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 3: ĐẶT LẠI MẬT KHẨU TẠM */}
      {showPasswordModal && selectedUser && (
        <div className="modal-backdrop" onClick={() => !formBusy && setShowPasswordModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Đặt lại mật khẩu tạm cho @{selectedUser.username}</h3>
              <p>Mật khẩu tạm thời sẽ buộc người dùng đổi mật khẩu ở lần đăng nhập tiếp theo</p>
            </div>

            {formError && (
              <div className="notice-box error" role="alert">
                <span>⚠️</span>
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleResetPassword}>
              <div className="form-group">
                <label htmlFor="reset-password">Mật khẩu tạm mới * (tối thiểu 12 ký tự)</label>
                <input
                  id="reset-password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={resetPasswordForm.temporary_password}
                  onChange={(e) =>
                    setResetPasswordForm({ ...resetPasswordForm, temporary_password: e.target.value })
                  }
                  placeholder="Nhập mật khẩu tạm mới..."
                  autoFocus
                />
              </div>

              <div className="form-group">
                <label htmlFor="confirm-reset-password">Nhập lại mật khẩu tạm *</label>
                <input
                  id="confirm-reset-password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={resetPasswordForm.confirm_password}
                  onChange={(e) =>
                    setResetPasswordForm({ ...resetPasswordForm, confirm_password: e.target.value })
                  }
                  placeholder="Xác nhận mật khẩu tạm..."
                />
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={formBusy}
                  onClick={() => setShowPasswordModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn btn-primary" disabled={formBusy}>
                  {formBusy ? 'Đang cập nhật…' : 'Lưu mật khẩu tạm'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 4: CẤP QUYỀN TRUY CẬP TỦ */}
      {showGrantModal && (
        <div className="modal-backdrop" onClick={() => !formBusy && setShowGrantModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Cấp quyền truy cập tủ mạng</h3>

            </div>

            {formError && (
              <div className="notice-box error" role="alert">
                <span>⚠️</span>
                <span>{formError}</span>
              </div>
            )}

            <form onSubmit={handleGrantAccess}>
              <div className="form-group">
                <label htmlFor="grant-user">Chọn Người dùng *</label>
                <select
                  id="grant-user"
                  required
                  value={grantForm.user_id}
                  onChange={(e) => setGrantForm({ ...grantForm, user_id: e.target.value })}
                >
                  {users
                    .filter((u) => u.role !== 'ADMIN')
                    .map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.full_name || u.username} (@{u.username}) — {translateRole(u.role)}
                      </option>
                    ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="grant-device">Chọn Tủ mạng *</label>
                <select
                  id="grant-device"
                  required
                  value={grantForm.device_id}
                  onChange={(e) => setGrantForm({ ...grantForm, device_id: e.target.value })}
                >
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name ? `${d.name} (${d.id})` : d.id}
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="grant-role">Mức quyền *</label>
                <select
                  id="grant-role"
                  required
                  value={grantForm.role}
                  onChange={(e) => setGrantForm({ ...grantForm, role: e.target.value })}
                >
                  <option value="VIEW">Chỉ xem (VIEW) — Xem số đo và cảnh báo</option>
                  <option value="OPERATE">Vận hành & Điều khiển (OPERATE) — Bật/tắt quạt và cấu hình</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="grant-expires">Hạn quyền (Tùy chọn, để trống = Vô thời hạn)</label>
                <input
                  id="grant-expires"
                  type="datetime-local"
                  value={grantForm.expires_at}
                  onChange={(e) => setGrantForm({ ...grantForm, expires_at: e.target.value })}
                />
              </div>

              <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={formBusy}
                  onClick={() => setShowGrantModal(false)}
                >
                  Hủy
                </button>
                <button type="submit" className="btn btn-primary" disabled={formBusy}>
                  {formBusy ? 'Đang cấp…' : 'Cấp quyền tủ'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
