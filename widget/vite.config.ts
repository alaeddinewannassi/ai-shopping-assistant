import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/widget.ts",
      name: "AssistantWidget",
      fileName: () => "assistant-widget.js",
      formats: ["iife"],
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "http://localhost:3000" },
    },
    include: ["tests/**/*.test.ts"],
  },
});
