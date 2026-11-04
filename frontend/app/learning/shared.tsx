"use client";

/** X-Learning közös típusok, állapot-színek és egy kicsi, biztonságos
 *  Markdown-megjelenítő (címsor, lista, félkövér/dőlt, link — HTML nélkül). */

import type { ReactNode } from "react";

export interface MyItem {
  id: string;
  material_id: string;
  title: string;
  category: string | null;
  has_video: boolean;
  has_test: boolean | null;
  due_date: string | null;
  status: "open" | "overdue" | "done" | "expired";
  completed_at: string | null;
  valid_until: string | null;
  attempts: number;
  last_score: number | null;
  source: "manual" | "role";
}

export const STATUS_CLS: Record<MyItem["status"], string> = {
  open: "bg-sky-100 text-sky-800",
  overdue: "bg-rose-100 text-rose-800",
  done: "bg-emerald-100 text-emerald-800",
  expired: "bg-amber-100 text-amber-800",
};

export interface Material {
  id: string;
  title: string;
  category: string | null;
  body: string | null;
  video_url: string | null;
  video_id: string | null;
  validity_months: number | null;
  random_count: number | null;
  external_ok: boolean;
  auto_roles: string[];
  default_due_days: number | null;
  is_active: boolean;
  created_by_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  question_count: number;
  attachments: AttachmentMeta[];
  assigned?: number;
  done?: number;
  overdue?: number;
  expired?: number;
}

export interface AttachmentMeta {
  id: string;
  filename: string;
  mime: string;
  size: number;
}

export function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Soron belüli jelölések: **félkövér**, *dőlt*, [szöveg](url). */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("[")) {
      const label = tok.slice(1, tok.indexOf("]"));
      out.push(
        <a key={k} href={m[2]} target="_blank" rel="noopener noreferrer" className="text-sky-700 underline">
          {label}
        </a>,
      );
    } else out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Képhivatkozás feloldása: `att:<id>` → a csatolmány URL-je (az oldal adja meg). */
export type ImageResolver = (ref: string) => string | null;

export function renderMarkdown(body: string | null | undefined, resolveImage?: ImageResolver): ReactNode {
  if (!body) return null;
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let k = 0;
  const flushPara = () => {
    if (para.length) {
      blocks.push(
        <p key={`p${k++}`} className="my-2 leading-relaxed">
          {inline(para.join(" "), `p${k}`)}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((it, i) => <li key={i}>{inline(it, `li${k}-${i}`)}</li>);
      blocks.push(
        list.ordered ? (
          <ol key={`l${k++}`} className="my-2 list-decimal space-y-1 pl-6">
            {items}
          </ol>
        ) : (
          <ul key={`l${k++}`} className="my-2 list-disc space-y-1 pl-6">
            {items}
          </ul>
        ),
      );
      list = null;
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    // kép a saját sorában: ![felirat](att:<id>) vagy ![felirat](https://…)
    const img = /^!\[([^\]]*)\]\(([^)\s]+)\)$/.exec(line.trim());
    if (img) {
      flushPara();
      flushList();
      const src = img[2].startsWith("att:") ? (resolveImage ? resolveImage(img[2].slice(4)) : null) : (/^https?:\/\//.test(img[2]) ? img[2] : null);
      if (src) {
        blocks.push(
          <figure key={`img${k++}`} className="my-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={img[1]} className="max-h-[520px] w-auto max-w-full rounded-xl border border-slate-200 shadow-sm" />
            {img[1] && <figcaption className="mt-1 text-xs text-slate-500">{img[1]}</figcaption>}
          </figure>,
        );
      }
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    const ul = /^[-*]\s+(.*)$/.exec(line);
    const ol = /^\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      flushPara();
      flushList();
    } else if (h) {
      flushPara();
      flushList();
      const cls = h[1].length === 1 ? "mt-4 text-xl font-semibold" : h[1].length === 2 ? "mt-3 text-lg font-semibold" : "mt-2 font-semibold";
      blocks.push(
        <div key={`h${k++}`} className={cls}>
          {inline(h[2], `h${k}`)}
        </div>,
      );
    } else if (ul || ol) {
      flushPara();
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((ul ?? ol)![1]);
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return <div className="text-sm text-slate-800">{blocks}</div>;
}
