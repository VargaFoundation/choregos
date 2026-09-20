// Tailwind 4 : le plugin PostCSS est un paquet séparé (`@tailwindcss/postcss`). Passer
// `tailwindcss` directement fait échouer la construction avec un message explicite.
// `autoprefixer` n'est plus nécessaire — Tailwind 4 préfixe ce qu'il produit.
const config = { plugins: { "@tailwindcss/postcss": {} } };

export default config;
