import { useRef, useState } from 'react'
import { api } from '../api.js'

const ANSWER_TYPE_META = {
  SMALLTALK: { label: '💬 Hội thoại & Hỗ trợ', tone: 'neutral', detailsTitle: null },
  OUT_OF_SCOPE: { label: '🚫 Ngoài phạm vi hệ thống', tone: 'neutral', detailsTitle: null },
  EXPLAIN: { label: '📘 Giải thích thuật ngữ', tone: 'info', detailsTitle: 'Thuật ngữ kỹ thuật liên quan' },
  STATUS: { label: '📊 Trạng thái vận hành', tone: 'info', detailsTitle: 'Chi tiết thông số ghi nhận' },
  FORECAST: { label: '📈 Dự báo định lượng', tone: 'primary', detailsTitle: 'Chi tiết các mốc ngoại suy & khoảng tin cậy 95%' },
  DIAGNOSE: { label: '🔍 Chẩn đoán kỹ thuật', tone: 'warning', detailsTitle: 'Nguyên nhân & phân tích số liệu' },
  WHAT_IF: { label: '🎛️ Giả lập ngưỡng', tone: 'purple', detailsTitle: 'Thông số mô phỏng & Giả định' },
  COMMANDS: { label: '📡 Phân tích lệnh MQTT', tone: 'info', detailsTitle: 'Chi tiết nhật ký lệnh điều khiển' },
  COMPARE: { label: '⚖️ Đối chiếu & So sánh', tone: 'info', detailsTitle: 'Chi tiết tương quan thông số' },
  ADVICE: { label: '🛠️ Quy trình & Khuyến nghị', tone: 'success', detailsTitle: 'Cơ sở đánh giá hiện trạng' },
}

const STARTER_PROMPTS = [
  { label: 'Tóm tắt tình trạng hiện tại', question: 'Tóm tắt tình trạng & sự cố 24h qua của tủ này' },
  { label: 'Dự báo nhiệt độ 15–30 phút tới', question: 'Dự báo xu hướng nhiệt độ và rủi ro trong 15 - 30 phút tới?' },
  { label: 'Chẩn đoán hiệu suất quạt', question: 'Đánh giá hiệu suất làm mát của quạt và phân tích nguyên nhân bất thường' },
  { label: 'Giả lập: Hạ fan_on xuống 28°C', question: 'Nếu hạ ngưỡng bật quạt fan_on xuống 28°C và fan_off xuống 26°C thì sao?' },
  { label: 'Kiểm tra lệnh TIMEOUT / FAILED', question: 'Giải thích các lệnh TIMEOUT / FAILED gần đây của tủ này' },
  { label: 'So sánh với các tủ khác', question: 'So sánh nhiệt độ tủ này với trung bình các tủ khác trong hệ thống' },
  { label: 'Giải thích Hysteresis là gì?', question: 'Giải thích cơ chế trễ nhiệt Hysteresis và tại sao cần fan_on và fan_off?' },
]

function SeverityBadge({ severity }) {
  if (!severity) return null
  const cls =
    severity === 'CRITICAL'
      ? 'sev-critical'
      : severity === 'ATTENTION'
        ? 'sev-attention'
        : 'sev-normal'
  const text =
    severity === 'CRITICAL'
      ? '🔴 Mức độ: NGHIÊM TRỌNG (CRITICAL)'
      : severity === 'ATTENTION'
        ? '🟠 Mức độ: CẦN CHÚ Ý (ATTENTION)'
        : '🟢 Mức độ: BÌNH THƯỜNG (NORMAL)'
  return <span className={`copilot-severity-badge ${cls}`}>{text}</span>
}

function ForecastV2Card({ forecast }) {
  if (!forecast) return null

  if (!forecast.available) {
    return (
      <div className="copilot-forecast-gated" role="status">
        <div className="copilot-forecast-gated-header">
          <span>🔒 Tạm khóa ngoại suy dự báo ({forecast.reason || 'UNAVAILABLE'})</span>
        </div>
        <p className="copilot-forecast-gated-text">
          {forecast.predictive_assessment || 'Không đủ điều kiện dữ liệu thời gian thực để ngoại suy dự báo.'}
        </p>
      </div>
    )
  }

  const ci = forecast.confidence_intervals || {}
  const ci5 = ci['5m']
  const ci15 = ci['15m']
  const ci30 = ci['30m']

  return (
    <div className="copilot-forecast-v2-card">
      <div className="copilot-forecast-v2-header">
        <span className="copilot-forecast-v2-title">
          📈 Dự báo ngắn hạn({forecast.basis_samples} mẫu · σ = {forecast.residual_std_c ?? '--'}°C)
        </span>
        {forecast.capped && (
          <span className="copilot-severity-badge sev-attention">
            Giới hạn trần vật lý ({forecast.physical_ceiling_c}°C)
          </span>
        )}
      </div>

      <p className="copilot-forecast-v2-desc">{forecast.predictive_assessment}</p>

      <div className="copilot-forecast-v2-grid">
        <div className="copilot-forecast-v2-cell">
          <span className="cell-label">Hiện tại</span>
          <strong className="cell-value">
            {forecast.current_temperature_c ?? '--'}°C · {forecast.current_humidity_pct ?? '--'}%
          </strong>
          <span className="cell-sub">
            Tốc độ: {forecast.temp_slope_per_min > 0 ? '+' : ''}
            {forecast.temp_slope_per_min ?? 0} °C/phút
          </span>
        </div>

        <div className="copilot-forecast-v2-cell">
          <span className="cell-label">Dự báo +5 phút</span>
          <strong className="cell-value">{forecast.projected_temp_5m_c ?? '--'} °C</strong>
          {ci5 && (
            <span className="cell-sub">95% CI: [{ci5[0]}, {ci5[1]}]°C</span>
          )}
        </div>

        <div className="copilot-forecast-v2-cell">
          <span className="cell-label">Dự báo +15 phút</span>
          <strong className="cell-value">{forecast.projected_temp_15m_c ?? '--'} °C</strong>
          {ci15 && (
            <span className="cell-sub">95% CI: [{ci15[0]}, {ci15[1]}]°C</span>
          )}
        </div>

        <div className="copilot-forecast-v2-cell">
          <span className="cell-label">Dự báo +30 phút</span>
          <strong className="cell-value">{forecast.projected_temp_30m_c ?? '--'} °C</strong>
          {ci30 && (
            <span className="cell-sub">95% CI: [{ci30[0]}, {ci30[1]}]°C</span>
          )}
        </div>
      </div>
    </div>
  )
}

function WhatIfSimulationCard({ simulation }) {
  if (!simulation) return null
  const cur = simulation.current_thresholds || {}
  const prop = simulation.proposed_thresholds || {}

  return (
    <div className={`copilot-whatif-card ${!simulation.valid ? 'whatif-invalid' : ''}`}>
      <div className="copilot-whatif-header">
        <span className="copilot-whatif-title">
          🎛️ Mô phỏng kịch bản ngưỡng
        </span>
        <span
          className={`copilot-severity-badge ${!simulation.valid || simulation.risk_level === 'CRITICAL'
            ? 'sev-critical'
            : simulation.risk_level === 'HIGH'
              ? 'sev-attention'
              : 'sev-normal'
            }`}
        >
          {!simulation.valid ? `Lỗi: ${simulation.error_code}` : `Rủi ro quá nhiệt: ${simulation.risk_level}`}
        </span>
      </div>

      <div className="copilot-whatif-grid">
        <div className="copilot-whatif-cell">
          <span className="cell-label">Ngưỡng hiện hành</span>
          <strong>fan_on: {cur.fan_on_c ?? '--'}°C · fan_off: {cur.fan_off_c ?? '--'}°C</strong>
          <span className="cell-sub">Cảnh báo quá nhiệt: {cur.temp_alert_c ?? '--'}°C</span>
        </div>

        <div className="copilot-whatif-cell">
          <span className="cell-label">Ngưỡng giả lập</span>
          <strong>fan_on: {prop.fan_on_c ?? '--'}°C · fan_off: {prop.fan_off_c ?? '--'}°C</strong>
          <span className="cell-sub">
            Biên độ trễ (Hysteresis): {simulation.hysteresis_band_c ?? '--'}°C
          </span>
        </div>

        <div className="copilot-whatif-cell">
          <span className="cell-label">Độ dịch thời điểm bật quạt</span>
          <strong>
            {simulation.immediate_fan_trigger
              ? 'Kích hoạt ngay lập tức'
              : simulation.trigger_time_shift_minutes != null
                ? `${simulation.trigger_time_shift_minutes < 0 ? 'Sớm hơn' : 'Muộn hơn'} ${Math.abs(simulation.trigger_time_shift_minutes)} phút`
                : 'Chưa chạm ngưỡng bật'}
          </strong>
          <span className="cell-sub">
            Khoảng đệm tới cảnh báo: {simulation.margin_to_temp_alert_c ?? '--'}°C
          </span>
        </div>
      </div>

      {Array.isArray(simulation.assumptions) && simulation.assumptions.length > 0 && (
        <div className="copilot-whatif-assumptions">
          <strong>Giả định mô phỏng:</strong> {simulation.assumptions.join(' · ')}
        </div>
      )}
    </div>
  )
}

export default function CopilotChat({ deviceId, deviceName }) {
  const [question, setQuestion] = useState('')
  const [hours, setHours] = useState(24)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [messages, setMessages] = useState([])
  const msgSeqRef = useRef(0)

  const buildHistoryPayload = (currentMessages) => {
    const turns = []
    for (const msg of currentMessages.slice(-4)) {
      if (msg.role === 'user' && msg.content) {
        turns.push({ role: 'user', content: String(msg.content).slice(0, 300) })
      } else if (msg.role === 'assistant' && msg.response?.answer?.summary) {
        turns.push({ role: 'assistant', content: String(msg.response.answer.summary).slice(0, 300) })
      }
    }
    return turns.slice(-4)
  }

  const sendQuestion = async (rawText) => {
    const cleanQ = String(rawText ?? question).trim()
    if (!cleanQ || loading) return

    setError('')
    const historyPayload = buildHistoryPayload(messages)
    msgSeqRef.current += 1
    const userMsg = {
      id: `u-${msgSeqRef.current}`,
      role: 'user',
      content: cleanQ,
      timestamp: new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    }

    setMessages((prev) => [...prev, userMsg])
    setQuestion('')
    setLoading(true)

    try {
      const res = await api(`/devices/${encodeURIComponent(deviceId)}/ai-copilot/`, {
        method: 'POST',
        data: {
          question: cleanQ,
          hours: Number(hours) || 24,
          history: historyPayload,
        },
      })
      msgSeqRef.current += 1
      const assistantMsg = {
        id: `a-${msgSeqRef.current}`,
        role: 'assistant',
        response: res,
        timestamp: new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch (err) {
      if (err.status === 429) {
        setError('Bạn đã gửi quá nhiều câu hỏi trong 1 phút (tối đa 10 câu/phút). Vui lòng đợi ít giây rồi thử lại.')
      } else {
        setError(err.message || 'Không thể kết nối tới Trợ lý AI lúc này.')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleFormSubmit = (e) => {
    e.preventDefault()
    sendQuestion(question)
  }

  const handleClearChat = () => {
    setMessages([])
    setError('')
  }

  return (
    <section className="card copilot-tab-card" aria-label="Trợ lý AI Hội thoại Thích ứng">
      <div className="card-heading">
        <div>
          <h2>🤖 Trợ lý AI Hội thoại — {deviceName}</h2>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <label htmlFor="copilot-hours-select" style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              Phạm vi dữ liệu:
            </label>
            <select
              id="copilot-hours-select"
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              disabled={loading}
              style={{ height: '34px', padding: '0 10px', borderRadius: '6px', border: '1px solid var(--border-color)' }}
            >
              <option value={6}>6 giờ gần nhất</option>
              <option value={12}>12 giờ gần nhất</option>
              <option value={24}>24 giờ gần nhất</option>
              <option value={48}>48 giờ gần nhất</option>
              <option value={72}>72 giờ gần nhất</option>
            </select>
          </div>

          {messages.length > 0 && (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={handleClearChat}
              disabled={loading}
            >
              🗑️ Xóa hội thoại
            </button>
          )}
        </div>
      </div>

      <div className="notice-box info" style={{ marginBottom: '14px' }}>
        <span>ℹ️</span>
        <span>
          <strong>Chế độ chỉ đọc — Trợ lý AI không tự động gửi lệnh điều khiển phần cứng.</strong>
        </span>
      </div>

      <div className="copilot-quick-prompts">
        <span className="copilot-quick-label">Gợi ý nhanh:</span>
        {STARTER_PROMPTS.map((item) => (
          <button
            key={item.label}
            type="button"
            className="btn btn-outline btn-sm"
            disabled={loading}
            onClick={() => sendQuestion(item.question)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="notice-box error" role="alert" style={{ marginBottom: '14px' }}>
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {/* LUỒNG HỘI THOẠI (CHAT THREAD) */}
      <div className="copilot-chat-thread" aria-live="polite">
        {messages.length === 0 && !loading && (
          <div className="copilot-empty-state">
            <span>🤖 Hãy đặt câu hỏi cho Trợ lý AI</span>
          </div>
        )}

        {messages.map((msg) => {
          if (msg.role === 'user') {
            return (
              <div key={msg.id} className="copilot-msg-row copilot-msg-user">
                <div className="copilot-bubble copilot-bubble-user">
                  <div className="copilot-bubble-text">{msg.content}</div>
                  <span className="copilot-msg-time">{msg.timestamp}</span>
                </div>
              </div>
            )
          }

          const res = msg.response || {}
          const ans = res.answer || {}
          const ansType = ans.answer_type || res.answer_type || 'DIAGNOSE'
          const meta = ANSWER_TYPE_META[ansType] || ANSWER_TYPE_META.DIAGNOSE
          const details =
            Array.isArray(ans.details) && ans.details.length > 0
              ? ans.details
              : Array.isArray(ans.possible_causes)
                ? ans.possible_causes
                : []
          const actions = Array.isArray(ans.recommended_actions) ? ans.recommended_actions : []
          const followUps = Array.isArray(ans.follow_up_suggestions) ? ans.follow_up_suggestions : []
          const dataUsed = Array.isArray(ans.data_used) ? ans.data_used : []
          const isConversationalOnly = ['SMALLTALK', 'OUT_OF_SCOPE', 'EXPLAIN'].includes(ansType)

          return (
            <div key={msg.id} className="copilot-msg-row copilot-msg-assistant">
              <div className={`copilot-bubble copilot-bubble-assistant copilot-type-${ansType.toLowerCase()}`}>
                <div className="copilot-assistant-top">
                  <span className={`copilot-intent-tag tone-${meta.tone}`}>
                    {meta.label}
                  </span>
                  {!isConversationalOnly && <SeverityBadge severity={ans.severity_assessment} />}
                </div>

                {/* SUMMARY CHÍNH */}
                <p className="copilot-summary-text">{ans.summary}</p>

                {/* THẺ DỰ BÁO ĐỊNH LƯỢNG V2 (chỉ hiển thị khi hỏi FORECAST hoặc DIAGNOSE có dự báo khả dụng) */}
                {(ansType === 'FORECAST' || (ansType === 'DIAGNOSE' && res.real_time_forecast?.available)) && (
                  <ForecastV2Card forecast={res.real_time_forecast} />
                )}

                {/* THẺ MÔ PHỎNG WHAT-IF (chỉ hiển thị khi hỏi WHAT_IF) */}
                {ansType === 'WHAT_IF' && (
                  <WhatIfSimulationCard simulation={res.what_if_simulation} />
                )}

                {/* DANH SÁCH CHI TIẾT / NGUYÊN NHÂN */}
                {details.length > 0 && meta.detailsTitle && (
                  <div className="copilot-section">
                    <h4>{meta.detailsTitle}</h4>
                    <ul>
                      {details.map((item, idx) => (
                        <li key={idx}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* KHUYẾN NGHỊ HÀNH ĐỘNG */}
                {!isConversationalOnly && actions.length > 0 && (
                  <div className="copilot-section">
                    <h4>Khuyến nghị vận hành</h4>
                    <ul>
                      {actions.map((act, idx) => (
                        <li key={idx}>{act}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* GỢI Ý CÂU HỎI TIẾP THEO (FOLLOW-UP CHIPS) */}
                {followUps.length > 0 && (
                  <div className="copilot-followups">
                    <span className="followup-label">Hỏi tiếp:</span>
                    {followUps.map((f, idx) => (
                      <button
                        key={idx}
                        type="button"
                        className="btn-followup-chip"
                        disabled={loading}
                        onClick={() => sendQuestion(f)}
                      >
                        {f}
                      </button>
                    ))}
                  </div>
                )}

                {/* CHÂN TRANG METADATA */}
                <div className="copilot-result-footer">
                  <span>
                    <strong>Nguồn:</strong> {res.source_label || res.source}
                  </span>
                  <span style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                    {res.latency_ms != null && <span>⏱️ {res.latency_ms} ms</span>}
                    {dataUsed.length > 0 && <span>🗂️ Dữ liệu: {dataUsed.join(', ')}</span>}
                  </span>
                </div>
              </div>
            </div>
          )
        })}

        {loading && (
          <div className="copilot-msg-row copilot-msg-assistant">
            <div className="copilot-bubble copilot-bubble-assistant copilot-loading-bubble">
              <span>⏳ Đang phân loại ý định và phân tích ngữ cảnh...</span>
            </div>
          </div>
        )}
      </div>

      {/* Ô NHẬP CÂU HỎI HỘI THOẠI */}
      <form onSubmit={handleFormSubmit} className="copilot-form">
        <div className="copilot-input-row">
          <input
            type="text"
            maxLength={500}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Nhập câu hỏi cho Trợ lý AI"
            disabled={loading}
            aria-label="Câu hỏi cho Trợ lý AI"
          />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || !question.trim()}
          >
            {loading ? 'Đang gửi…' : 'Gửi câu hỏi'}
          </button>
        </div>
        <div className="copilot-char-counter" style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span>Hỗ trợ nhớ ngữ cảnh tối đa 4 lượt gần nhất</span>
          <span>{question.length}/500 ký tự</span>
        </div>
      </form>
    </section>
  )
}
