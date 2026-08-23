import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/upload": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/documents": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/chat": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/chats": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/history": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
