import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, asList, translateCommandStatus, translateCommandType } from '../api.js'
import Pagination from '../components/Pagination.jsx'

/**
 * Trang Lịch sử phát lệnh điều khiển (CommandsPage)
 * Theo dõi tiến trình gửi và nhận phản hồi lệnh điều khiển từ thiết bị.
 * Phân biệt rõ ràng giữa:
 * - SENT: Đã gửi tới máy chủ MQTT (chưa khẳng định thiết bị thực thi thành công)
 * - APPLIED / ACKED: Thiết bị đã thực hiện và gửi xác nhận
 * - APPLIED_LATE: Thiết bị thực hiện muộn sau thời gian chờ
 * - TIMEOUT / FAILED: Chưa nhận phản hồi hoặc lệnh thất bại
 */
export default function CommandsPage() {
  const [commands, setCommands] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cmdFilter, setCmdFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const loadCommands = useCallback(async () => {
    try {
      const body = await api('/commands/')
      setCommands(asList(body))
      setError('')
    } catch (err) {
      setError(err.message || 'Không thể tải lịch sử phát lệnh.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    let timer

    async function poll() {
      await loadCommands()
      if (active) timer = setTimeout(poll, 3000)
    }

    timer = setTimeout(poll, 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [loadCommands])

  // Lọc theo trạng thái
  const filteredCommands = commands.filter((cmd) => {
    if (cmdFilter === 'all') return true
    const st = String(cmd.status || '').toUpperCase()
    if (cmdFilter === 'APPLIED') {
      return st === 'ACKED' || st === 'APPLIED'
    }
    if (cmdFilter === 'APPLIED_LATE') {
      return st === 'APPLIED_LATE'
    }
    if (cmdFilter === 'FAILED') {
      return st === 'FAILED'
    }
    return st === cmdFilter.toUpperCase()
  })

  // Dữ liệu phân trang
  const paginatedCommands = filteredCommands.slice((page - 1) * pageSize, page * pageSize)

  const format = (val) => (val === null || val === undefined || val === '' ? '--' : val)
  const formatDate = (val) => (val ? new Date(val).toLocaleString('vi-VN') : '--')

  return (
    <div className="commands-page">
      <section className="card" aria-labelledby="commands-heading">
        <div className="card-heading">
          <div>
            <h2 id="commands-heading">Lịch sử phát lệnh điều khiển</h2>

          </div>
          <span className="count">{filteredCommands.length} / {commands.length} lệnh</span>
        </div>

        {/* Bộ lọc trạng thái lệnh */}
        <div className="filters">
          <select
            value={cmdFilter}
            onChange={(e) => {
              setCmdFilter(e.target.value)
              setPage(1)
            }}
            aria-label="Lọc theo trạng thái lệnh"
          >
            <option value="all">Tất cả trạng thái lệnh</option>
            <option value="PENDING">Chờ gửi</option>
            <option value="SENT">Đã gửi tới máy chủ</option>
            <option value="APPLIED">Đã thực hiện</option>
            <option value="APPLIED_LATE">Thực hiện trễ</option>
            <option value="FAILED">Thất bại</option>
            <option value="TIMEOUT">Chưa nhận phản hồi</option>
            <option value="CANCELLED">Đã hủy</option>
          </select>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Mã lệnh (ID)</th>
                <th>Tủ nhận lệnh</th>
                <th>Loại lệnh điều khiển</th>
                <th>Trạng thái thực thi</th>
                <th>Người thực hiện</th>
                <th>Thời gian gửi</th>
              </tr>
            </thead>
            <tbody>
              {paginatedCommands.map((cmd, idx) => {
                const devId = cmd.device_id || cmd.device
                const statusStr = String(cmd.status || '').toLowerCase()
                const rawCmdType = cmd.command_type || cmd.command

                return (
                  <tr key={cmd.id || idx}>
                    <td>
                      <strong>{String(cmd.id).slice(0, 8)}...</strong>
                      {cmd.correlation_id && (
                        <small>Mã đối soát: {String(cmd.correlation_id).slice(0, 8)}...</small>
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
                      <span style={{ fontWeight: 600, color: 'var(--primary)' }}>
                        {translateCommandType(rawCmdType)}
                      </span>
                      {rawCmdType && (
                        <small style={{ color: 'var(--text-muted)' }}>{rawCmdType}</small>
                      )}
                    </td>
                    <td>
                      <span className={`status-pill ${statusStr}`}>
                        {translateCommandStatus(cmd.status)}
                      </span>
                    </td>
                    <td>{format(cmd.actor_username || cmd.actor || 'Hệ thống')}</td>
                    <td>{formatDate(cmd.created_at || cmd.sent_at)}</td>
                  </tr>
                )
              })}

              {/* Lỗi tải lệnh */}
              {error && !filteredCommands.length && (
                <tr>
                  <td colSpan="6" className="table-state-error">
                    <div className="table-error-row">
                      <span>⚠️ Không thể tải lịch sử phát lệnh ({error}).</span>
                      <button type="button" className="btn btn-outline btn-sm" onClick={loadCommands}>
                        Thử lại
                      </button>
                    </div>
                  </td>
                </tr>
              )}

              {/* Đang tải */}
              {loading && !commands.length && !error && (
                <tr>
                  <td colSpan="6" className="table-state-loading">
                    Đang tải lịch sử phát lệnh điều khiển…
                  </td>
                </tr>
              )}

              {/* Danh sách rỗng */}
              {!filteredCommands.length && !loading && !error && (
                <tr>
                  <td colSpan="6" className="empty">
                    Chưa có lệnh điều khiển nào phù hợp với bộ lọc hiện tại.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Phân trang danh sách lệnh */}
        <Pagination
          currentPage={page}
          totalItems={filteredCommands.length}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
        />
      </section>
    </div>
  )
}
