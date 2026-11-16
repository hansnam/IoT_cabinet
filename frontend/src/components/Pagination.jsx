/**
 * Thành phần Phân trang chuẩn cho các bảng dữ liệu (Pagination component)
 * Hỗ trợ chọn kích thước trang (10, 20, 50), chuyển trang và hiển thị số lượng bản ghi rõ ràng.
 */
export default function Pagination({
  currentPage = 1,
  totalItems = 0,
  pageSize = 10,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50],
}) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize))
  const startItem = totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1
  const endItem = Math.min(totalItems, currentPage * pageSize)

  if (totalItems === 0) return null

  // Tạo danh sách các số trang cần hiển thị
  const getPageNumbers = () => {
    const pages = []
    const maxButtons = 5
    let startPage = Math.max(1, currentPage - 2)
    let endPage = Math.min(totalPages, startPage + maxButtons - 1)

    if (endPage - startPage < maxButtons - 1) {
      startPage = Math.max(1, endPage - maxButtons + 1)
    }

    for (let i = startPage; i <= endPage; i++) {
      pages.push(i)
    }
    return pages
  }

  const pageNumbers = getPageNumbers()

  return (
    <div className="table-pagination">
      <div className="pagination-info">
        Hiển thị <strong>{startItem} – {endItem}</strong> trong tổng số <strong>{totalItems}</strong> bản ghi
        {onPageSizeChange && (
          <span className="page-size-selector">
            {' · '}Mỗi trang:{' '}
            <select
              value={pageSize}
              onChange={(e) => {
                onPageSizeChange(Number(e.target.value))
                onPageChange(1)
              }}
              aria-label="Số bản ghi mỗi trang"
            >
              {pageSizeOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </span>
        )}
      </div>

      {totalPages > 1 && (
        <div className="pagination-controls" role="navigation" aria-label="Phân trang bảng">
          <button
            type="button"
            className="pagination-btn"
            disabled={currentPage <= 1}
            onClick={() => onPageChange(currentPage - 1)}
            aria-label="Trang trước"
          >
            ‹ Trước
          </button>

          {pageNumbers[0] > 1 && (
            <>
              <button
                type="button"
                className={`pagination-btn ${currentPage === 1 ? 'active' : ''}`}
                onClick={() => onPageChange(1)}
              >
                1
              </button>
              {pageNumbers[0] > 2 && <span className="pagination-ellipsis">…</span>}
            </>
          )}

          {pageNumbers.map((p) => (
            <button
              type="button"
              key={p}
              className={`pagination-btn ${currentPage === p ? 'active' : ''}`}
              onClick={() => onPageChange(p)}
              aria-current={currentPage === p ? 'page' : undefined}
            >
              {p}
            </button>
          ))}

          {pageNumbers[pageNumbers.length - 1] < totalPages && (
            <>
              {pageNumbers[pageNumbers.length - 1] < totalPages - 1 && (
                <span className="pagination-ellipsis">…</span>
              )}
              <button
                type="button"
                className={`pagination-btn ${currentPage === totalPages ? 'active' : ''}`}
                onClick={() => onPageChange(totalPages)}
              >
                {totalPages}
              </button>
            </>
          )}

          <button
            type="button"
            className="pagination-btn"
            disabled={currentPage >= totalPages}
            onClick={() => onPageChange(currentPage + 1)}
            aria-label="Trang sau"
          >
            Sau ›
          </button>
        </div>
      )}
    </div>
  )
}
