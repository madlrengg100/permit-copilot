import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // /api 를 백엔드로 프록시한다.
    //
    // 이게 없으면 브라우저가 백엔드(8000)를 직접 호출해야 해서 포트를 2개
    // 열어야 하고, 하나만 열리면 지도가 조용히 안 뜬다. 프록시를 두면
    // 프론트 포트 하나만 있으면 되고 CORS 도 필요 없다.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
