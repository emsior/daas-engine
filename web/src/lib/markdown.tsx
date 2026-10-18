import { Fragment, type ReactNode } from "react";

/**
 * Prosta, bezpieczna konwersja podzbioru Markdown do JSX (bez dangerouslySetInnerHTML):
 * nagłówki `#`..`###`, akapity, listy `-`/`*` i `1.`, **pogrubienie**, `kod`.
 * Cały tekst jest renderowany jako węzły tekstowe React, więc znaczniki HTML w treści nie działają.
 */

function inline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={`${keyPrefix}-b${index}`}>{token.slice(2, -2)}</strong>);
    } else {
      nodes.push(<code key={`${keyPrefix}-c${index}`}>{token.slice(1, -1)}</code>);
    }
    last = start + token.length;
    index += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function renderMarkdown(source: string): ReactNode {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let key = 0;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const id = `p${key++}`;
    blocks.push(<p key={id}>{inline(paragraph.join(" "), id)}</p>);
    paragraph = [];
  };

  const flushList = () => {
    if (!list) return;
    const id = `l${key++}`;
    const items = list.items.map((item, i) => (
      <li key={`${id}-${i}`}>{inline(item, `${id}-${i}`)}</li>
    ));
    blocks.push(list.ordered ? <ol key={id}>{items}</ol> : <ul key={id}>{items}</ul>);
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);

    if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      const level = (heading[1] ?? "#").length;
      const id = `h${key++}`;
      const content = inline(heading[2] ?? "", id);
      // h1 strony jest renderowany osobno; nagłówki treści zaczynają się od h2
      blocks.push(level === 3 ? <h3 key={id}>{content}</h3> : <h2 key={id}>{content}</h2>);
    } else if (bullet || numbered) {
      flushParagraph();
      const ordered = !bullet;
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push((bullet ?? numbered)?.[1] ?? "");
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  flushList();

  return (
    <>
      {blocks.map((block, i) => (
        <Fragment key={i}>{block}</Fragment>
      ))}
    </>
  );
}
