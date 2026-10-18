const config = {
  plugins: {
    "@tailwindcss/postcss": {},
    // Régi iPad/Safari kompatibilitás (927afc93): a Tailwind v4 oklch()
    // színeihez és color-mix() hívásaihoz rgb-fallback épül a CSS-be —
    // a modern böngészők továbbra is az eredeti értéket használják.
    "postcss-preset-env": {
      stage: false,
      autoprefixer: false,
      features: {
        "oklab-function": true,
        "color-mix": true,
      },
    },
  },
};

export default config;
