import type { Metadata } from "next";
import { Michroma, Orbitron } from "next/font/google";
import { LanguageProvider } from "@/lib/i18n";
import { PermissionsProvider } from "@/lib/perms";
import { UIProvider } from "@/lib/ui";
import "./globals.css";

// Márka-betűtípusok az X-admin logóhoz (build-időben letöltve, self-hosted).
const orbitron = Orbitron({ subsets: ["latin"], weight: "900", variable: "--font-orbitron" });
const michroma = Michroma({ subsets: ["latin"], weight: "400", variable: "--font-michroma" });

export const metadata: Metadata = {
  title: "X-admin — X-Presso management",
  description: "Workforce management: employees, schedules, attendance, payroll export",
  manifest: "/manifest.json",
};

export const viewport = {
  themeColor: "#E31E24",
};

// Villanásmentes téma-init: a React hidratálás ELŐTT beállítja a .dark
// osztályt a tárolt preferencia (vagy rendszer-beállítás) alapján.
const THEME_INIT = `(function(){try{var t=localStorage.getItem('iwfm-theme');var d=t==='dark'||((!t||t==='system')&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

// Service worker regisztráció (PWA): statikus asset-gyorsítótár + offline
// tartalék. Az /api forgalomhoz a SW nem nyúl.
// Localhoston (dev) nem regisztrálunk: a dev-chunkok nem hash-eltek, a
// cache-first SW régi kódot szolgálna ki szerkesztés után.
const SW_INIT = `if('serviceWorker' in navigator && location.hostname!=='localhost' && location.hostname!=='127.0.0.1'){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){});});}`;

// Preloader-eltüntetés: az oldal betöltésekor elhalványul, biztonsági
// határidővel (ha a load-esemény bármiért elmaradna). FONTOS: a React által
// renderelt csomópontot NEM távolítjuk el (az felborítaná a hidratálást),
// csak elrejtjük.
const PRELOADER_HIDE = `(function(){function h(){var e=document.getElementById('app-preloader');if(e){e.style.opacity='0';setTimeout(function(){e.style.display='none'},450);}}window.addEventListener('load',function(){setTimeout(h,150)});setTimeout(h,6000);})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="hu" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        <script dangerouslySetInnerHTML={{ __html: SW_INIT }} />
      </head>
      <body className={`${orbitron.variable} ${michroma.variable} min-h-screen antialiased`}>
        {/* Betöltő-képernyő: azonnal megjelenik (szerver-renderelt), és az
            app betöltésekor elhalványul — mobilon fedi le a fehér villanást. */}
        <div id="app-preloader">
          <div className="preloader-logo">
            <div className="preloader-line1">
              <span className="preloader-x" style={{ fontFamily: "var(--font-orbitron)" }}>X</span>
              <span className="preloader-rest" style={{ fontFamily: "var(--font-michroma)" }}>-admin</span>
            </div>
            <div className="preloader-sub" style={{ fontFamily: "var(--font-michroma)" }}>
              X-Presso management
            </div>
          </div>
        </div>
        <script dangerouslySetInnerHTML={{ __html: PRELOADER_HIDE }} />
        <LanguageProvider>
          <UIProvider>
            <PermissionsProvider>{children}</PermissionsProvider>
          </UIProvider>
        </LanguageProvider>
      </body>
    </html>
  );
}
