import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { warmBackend } from './services/warmup'

// Before React mounts, so the backend's cold start overlaps with rendering
// rather than with the user's first real request. See services/warmup.ts.
warmBackend()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
