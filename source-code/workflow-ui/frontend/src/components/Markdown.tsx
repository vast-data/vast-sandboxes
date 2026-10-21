import type { ReactNode } from "react";


function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const regex = /\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_|`(.+?)`/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    if (match[1] !== undefined) nodes.push(<strong key={key++}>{match[1]}</strong>);
    else if (match[2] !== undefined || match[3] !== undefined) {
      nodes.push(<em key={key++}>{match[2] ?? match[3]}</em>);
    } else if (match[4] !== undefined) nodes.push(<code key={key++}>{match[4]}</code>);
    last = regex.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Markdown({ text }: { text: string }) {
  const lines = (text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let quote: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      blocks.push(<p key={key++}>{inline(para.join(" "))}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((it, i) => <li key={i}>{inline(it)}</li>);
      blocks.push(list.ordered ? <ol key={key++}>{items}</ol> : <ul key={key++}>{items}</ul>);
      list = null;
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      blocks.push(
        <blockquote key={key++}>
          <p>{inline(quote.join(" "))}</p>
        </blockquote>
      );
      quote = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const trimmed = line.trim();

    if (trimmed.startsWith(">")) {
      flushPara();
      flushList();
      quote.push(trimmed.replace(/^>\s?/, ""));
      continue;
    }
    if (quote.length) flushQuote();

    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);
    const hrule = /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed);

    if (heading) {
      flushPara();
      flushList();
      const level = Math.min(6, heading[1].length);
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      blocks.push(<Tag key={key++}>{inline(heading[2])}</Tag>);
    } else if (hrule) {
      flushPara();
      flushList();
      blocks.push(<hr key={key++} />);
    } else if (bullet) {
      flushPara();
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(numbered[1]);
    } else if (trimmed === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushQuote();
  flushPara();
  flushList();

  return <div className="markdown">{blocks}</div>;
}
