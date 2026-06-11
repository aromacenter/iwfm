import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Iwfm — Munkaerő-kezelés",
  description: "Dolgozói nyilvántartás, heti beosztás, jelenlét és bérexport",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="hu">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        {children}
      </body>
    </html>
  );
}
