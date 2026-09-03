import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import basicSsl from '@vitejs/plugin-basic-ssl'

// HTTPS is on by default (needed for camera access when testing the scanner on
// a phone over the local network). Set VITE_HTTPS=false to serve plain HTTP —
// docker-compose does this, because an HTTPS page cannot call the HTTP backend
// without the browser blocking it as mixed content.
const useHttps = process.env.VITE_HTTPS !== 'false'

export default defineConfig({
  plugins: [react(), ...(useHttps ? [basicSsl()] : [])],
  server: {
    host: true, // Expose to local network
  },
})
