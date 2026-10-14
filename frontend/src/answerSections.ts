import type { Element, Root, RootContent } from 'hast';

function textContent(node: RootContent): string {
  if (node.type === 'text') return node.value;
  return 'children' in node ? node.children.map(textContent).join('') : '';
}

/** 在解析后的 Markdown 上分区，保留代码块、表格和链接的完整结构。 */
export function answerSections() {
  return (tree: Root) => {
    const children: RootContent[] = [];
    let current: Element | undefined;
    for (const node of tree.children) {
      if (node.type === 'element' && /^h[1-4]$/.test(node.tagName)) {
        const title = textContent(node);
        const tone = /补充|原因|注意|提醒|限制|风险/.test(title)
          ? 'amber'
          : /下一步|确认|提交|操作|建议|流程/.test(title)
            ? 'green'
            : 'blue';
        current = {
          type: 'element',
          tagName: 'section',
          properties: { className: ['answer-module', `answer-module-${tone}`] },
          children: [node],
        };
        children.push(current);
      } else if (current && node.type !== 'doctype') current.children.push(node);
      else children.push(node);
    }
    tree.children = children;
  };
}
