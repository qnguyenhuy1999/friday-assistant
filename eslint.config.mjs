import { defineConfig } from "eslint/config";
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default defineConfig(
  {
    ignores: ["node_modules/**", "pnpm-lock.yaml"],
  },
  js.configs.recommended,
  tseslint.configs.recommended,
);
