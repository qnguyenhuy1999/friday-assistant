import { defineConfig } from "eslint/config";
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default defineConfig(
  {
    ignores: [
      "node_modules/**",
      "pnpm-lock.yaml",
      "**/dist/**",
      ".venv/**",
      "htmlcov/**",
    ],
  },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    // React rules are scoped to apps/web: it is the only React package, and
    // eslint-plugin-react-hooks' shared config carries no `files` of its own.
    // The rules are listed explicitly rather than spread off
    // `reactHooks.configs.recommended`, whose shape changed between majors (v6
    // exports an array of flat configs, so spreading `.rules` from it would
    // silently enable nothing).
    files: ["apps/web/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": "warn",
    },
  },
);
