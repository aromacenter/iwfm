import type { Metadata } from "next";
import { LanguageProvider } from "@/lib/i18n";
import { UIProvider } from "@/lib/ui";
import "./globals.css";

export const metadata: Metadata = {
  title: "iwfm — Intelligence Workforce Management",
  description: "Workforce management: employees, schedules, attendance, payroll export",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="hu">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <LanguageProvider>
          <UIProvider>{children}</UIProvider>
        </LanguageProvider>
      </body>
    </html>
  );
}
