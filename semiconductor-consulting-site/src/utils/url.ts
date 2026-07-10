// Astro's `base` config is not auto-prepended to hand-written <a href> links,
// so every internal link must go through this helper.
export function withBase(path: string): string {
  const base = import.meta.env.BASE_URL;
  return base + path.replace(/^\//, '');
}
