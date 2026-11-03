import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // A React Compiler-lint "setState közvetlenül effect-ben" szabálya
      // teljesítmény-ajánlás, nem hibás működés. A kódbázisban ~30 ilyen
      // "prop változott → állapot nullázása" minta van; figyelmeztetésként
      // látszik, de nem buktatja a CI-t. Új kódban kerülendő.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
