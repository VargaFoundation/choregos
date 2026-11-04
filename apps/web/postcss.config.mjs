// Tailwind 4: the PostCSS plugin is a separate package (`@tailwindcss/postcss`). Passing
// `tailwindcss` directly makes the build fail with an explicit message.
// `autoprefixer` is no longer needed — Tailwind 4 prefixes what it produces.
const config = { plugins: { "@tailwindcss/postcss": {} } };

export default config;
