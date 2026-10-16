import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "playwright-report/**",
    "test-results/**",
    "coverage/**",
    "src/lib/api/schema.d.ts",
  ]),
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "dangerouslySetInnerHTML jest zabroniony (CSP strict, XSS). Renderuj JSX.",
        },
        {
          selector: "JSXAttribute[name.name='style']",
          message:
            "Atrybut style jest zabroniony (CSP style-src-attr). Użyj klas z design systemu.",
        },
      ],
    },
  },
]);
