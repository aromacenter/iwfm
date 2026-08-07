import type { Metadata } from "next";
import { LanguageProvider } from "@/lib/i18n";
import { UIProvider } from "@/lib/ui";
import "./globals.css";

export const metadata: Metadata = {
  title: "iwfm — Intelligence Workforce Management",
  description: "Workforce management: employees, schedules, attendance, payroll export",
  manifest: "/manifest.json",
};

export const viewport = {
  themeColor: "#4f46e5",
};

// Villanásmentes téma-init: a React hidratálás ELŐTT beállítja a .dark
// osztályt a tárolt preferencia (vagy rendszer-beállítás) alapján.
const THEME_INIT = `(function(){try{var t=localStorage.getItem('iwfm-theme');var d=t==='dark'||((!t||t==='system')&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

// Service worker regisztráció (PWA): statikus asset-gyorsítótár + offline
// tartalék. Az /api forgalomhoz a SW nem nyúl.
const SW_INIT = `if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="hu" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        <script dangerouslySetInnerHTML={{ __html: SW_INIT }} />
      </head>
      <body className="min-h-screen antialiased">
        <LanguageProvider>
          <UIProvider>{children}</UIProvider>
        </LanguageProvider>
      </body>
    </html>
  );
}
