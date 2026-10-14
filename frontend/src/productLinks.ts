import type { ProductLink } from './types';

export const productHref = (id: string) => `#shop?product=${encodeURIComponent(id)}`;

export function productIdFromHash(hash: string): string | undefined {
  const [view, query = ''] = hash.replace(/^#/, '').split('?');
  if (view !== 'shop') return undefined;
  const id = new URLSearchParams(query).get('product')?.toLowerCase();
  return id && /^p\d{2}$/.test(id) ? id : undefined;
}

export function productIdFromHref(href: string): string | undefined {
  try {
    const url = new URL(href, window.location.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname) return undefined;
    return productIdFromHash(url.hash);
  } catch {
    return undefined;
  }
}

export function productLinksFromTools(
  results: Array<{ name: string; output: unknown }>,
  answer: string,
): ProductLink[] {
  const links = new Map<string, ProductLink>();
  const normalized = answer.normalize('NFKC').toLowerCase().replace(/\s/g, '');
  for (const result of results) {
    if (
      !['search_products', 'get_product'].includes(result.name) ||
      !result.output ||
      typeof result.output !== 'object'
    )
      continue;
    const output = result.output as { success?: boolean; items?: unknown; product?: unknown };
    if (output.success === false) continue;
    const items = result.name === 'search_products' ? output.items : [output.product];
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      if (!item || typeof item !== 'object') continue;
      const product = item as { id?: unknown; name?: unknown };
      if (
        typeof product.id !== 'string' ||
        !/^p\d{2}$/.test(product.id) ||
        typeof product.name !== 'string' ||
        !product.name.trim()
      )
        continue;
      const name = product.name.trim();
      const prefix = name
        .match(/^[A-Za-z0-9][A-Za-z0-9\s+./_-]*/)?.[0]
        .replace(/\s/g, '')
        .toLowerCase();
      const mentioned =
        normalized.includes(name.normalize('NFKC').toLowerCase().replace(/\s/g, '')) ||
        new RegExp(`\\b${product.id}\\b`, 'i').test(answer) ||
        (prefix && prefix.length >= 4 && normalized.includes(prefix));
      if (mentioned && !answer.includes(productHref(product.id)))
        links.set(product.id, { id: product.id, name });
    }
  }
  return [...links.values()];
}
