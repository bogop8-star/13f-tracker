import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// GitHub Pages 하위 경로에서도 자산을 찾도록 상대 경로로 빌드합니다.
export default defineConfig({
  base: './',
  plugins: [react()],
})
