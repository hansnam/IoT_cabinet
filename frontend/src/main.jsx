import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import SessionApp from './SessionApp.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SessionApp />
  </StrictMode>,
)
