"use client";

/** Élő címke-előnézet (174. kör): ugyanazzal az elrendezési logikával, mint a
 *  backend `warehouse_labels.draw_label` — méretarányos SVG mm-egységekben,
 *  valódi QR-kóddal és Code128 vonalkóddal. A betűszélesség közelítés
 *  (Helvetica ~0,55 em), ezért a végleges PDF hajszálnyit eltérhet. */

import { useMemo } from "react";
import QRCode from "qrcode";
import type { LabelCfg } from "@/components/LabelPrintDialog";
import { code128Modules } from "@/lib/code128";

export interface PreviewLoc { code: string; row_code: string; bay: number; level: number; slot: number; label: string | null }

const PT = 0.3528; // 1 pt mm-ben

function qrPath(text: string, x: number, y: number, size: number): string {
  let qr: ReturnType<typeof QRCode.create>;
  try { qr = QRCode.create(text || " ", { errorCorrectionLevel: "M" }); } catch { return ""; }
  const n = qr.modules.size;
  const m = size / n;
  const parts: string[] = [];
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      if (qr.modules.get(r, c)) parts.push(`M${(x + c * m).toFixed(3)} ${(y + r * m).toFixed(3)}h${m.toFixed(3)}v${m.toFixed(3)}h-${m.toFixed(3)}z`);
    }
  }
  return parts.join("");
}

function barcodePath(text: string, x: number, y: number, maxW: number, h: number): string {
  const mods = code128Modules(text);
  const total = mods.reduce((a, b) => a + b, 0);
  const bw = Math.min(0.5, maxW / Math.max(total, 1));
  let cx = x + (maxW - total * bw) / 2;
  const parts: string[] = [];
  mods.forEach((wd, i) => {
    if (i % 2 === 0) parts.push(`M${cx.toFixed(3)} ${y.toFixed(3)}h${(wd * bw).toFixed(3)}v${h.toFixed(3)}h-${(wd * bw).toFixed(3)}z`);
    cx += wd * bw;
  });
  return parts.join("");
}

export default function LabelPreview({ cfg, loc, warehouseName, link, maxWidth = 340, maxHeight = 220 }: {
  cfg: LabelCfg;
  loc: PreviewLoc;
  warehouseName: string;
  link: string;
  maxWidth?: number;
  maxHeight?: number;
}) {
  const svg = useMemo(() => {
    const w = cfg.label_w, h = cfg.label_h;
    const f = new Set(cfg.fields);
    const pad = Math.min(Math.max(0.06 * Math.min(w, h), 1.2), 4);
    const innerX = pad, innerW = w - 2 * pad;
    let innerH = h - 2 * pad;
    const parts: React.ReactNode[] = [];
    if (f.has("barcode")) {
      const bcH = Math.min(Math.max(0.22 * h, 5), 14);
      parts.push(<path key="bc" d={barcodePath(loc.code, innerX, h - pad - bcH, innerW, bcH)} fill="#000" />);
      innerH -= bcH + pad * 0.6;
    }
    let textX = innerX, textW = innerW;
    if (f.has("qr")) {
      const q = Math.min(innerH, 0.42 * innerW);
      parts.push(<path key="qr" d={qrPath(cfg.qr_content === "link" ? link : loc.code, innerX, pad + (innerH - q) / 2, q)} fill="#000" />);
      textX += q + pad;
      textW -= q + pad;
    }
    const lines: { text: string; bold: boolean; wt: number }[] = [];
    if (f.has("group") && cfg.group_text.trim()) lines.push({ text: cfg.group_text.trim(), bold: true, wt: 1.4 });
    if (f.has("code")) lines.push({ text: loc.code, bold: true, wt: 2.4 });
    if (f.has("coords")) lines.push({ text: `sor ${loc.row_code} · állás ${loc.bay} · szint ${loc.level}${loc.slot ? ` · fakk ${loc.slot}` : ""}`, bold: false, wt: 1 });
    if (f.has("warehouse")) lines.push({ text: warehouseName.slice(0, 40), bold: false, wt: 1 });
    if (f.has("note") && loc.label) lines.push({ text: loc.label.slice(0, 40), bold: false, wt: 1 });
    if (lines.length && textW > 4) {
      const total = lines.reduce((a, l) => a + l.wt, 0);
      const unit = innerH / total;
      let top = pad;
      lines.forEach((l, i) => {
        const slotH = unit * l.wt;
        let size = slotH * 0.72 * cfg.font_scale; // mm
        const approxW = l.text.length * size * (l.bold ? 0.6 : 0.55);
        if (approxW > textW) size = Math.max(3 * PT, size * textW / approxW);
        size = Math.min(size, slotH * 0.85);
        parts.push(
          <text key={`t${i}`} x={textX} y={top + slotH * 0.5 + size * 0.35} fontSize={size} fontFamily="Helvetica, Arial, sans-serif" fontWeight={l.bold ? 700 : 400} fill="#000">{l.text}</text>,
        );
        top += slotH;
      });
    }
    return parts;
  }, [cfg, loc, warehouseName, link]);

  const scale = Math.min(maxWidth / cfg.label_w, maxHeight / cfg.label_h);
  return (
    <svg
      viewBox={`0 0 ${cfg.label_w} ${cfg.label_h}`}
      width={cfg.label_w * scale}
      height={cfg.label_h * scale}
      className="shadow"
      style={{ background: "#ffffff", outline: cfg.border ? "1px solid #cbd5e1" : "1px dashed #cbd5e1", colorScheme: "light" }}
      aria-label="Címke-előnézet"
    >
      <rect x={0} y={0} width={cfg.label_w} height={cfg.label_h} fill="#ffffff" />
      {svg}
    </svg>
  );
}
