import { defineConfig } from 'astro/config';

// GitHub Pages project site: https://<user>.github.io/python101/consulting/
// Build output lands in ../docs/consulting so it can sit alongside the
// existing docs/ site (the workout PWA) on the same Pages deployment.
export default defineConfig({
  site: 'https://pollya32.github.io',
  base: '/python101/consulting',
  outDir: '../docs/consulting',
  trailingSlash: 'always',
});
