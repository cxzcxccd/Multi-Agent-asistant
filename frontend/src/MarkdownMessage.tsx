import Markdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import { productHref, productIdFromHref } from './productLinks';
import { answerSections } from './answerSections';

export default function MarkdownMessage({
  content,
  modular = false,
}: {
  content: string;
  modular?: boolean;
}) {
  return (
    <Markdown
      skipHtml
      remarkPlugins={[remarkGfm, remarkBreaks]}
      rehypePlugins={modular ? [answerSections] : []}
      components={{
        a: ({ href, children }) => {
          const productId = href ? productIdFromHref(href) : undefined;
          return productId ? (
            <a href={productHref(productId)}>{children}</a>
          ) : (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          );
        },
      }}
    >
      {content}
    </Markdown>
  );
}
