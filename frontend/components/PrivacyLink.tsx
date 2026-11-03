"use client";

/** Adatkezelési tájékoztató linkje a nyilvános oldalakon (belépő, kioszk).
 *  A backend WFM_PRIVACY_URL változójából jön (/api/settings/branding);
 *  üresen semmit nem rajzol. */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function PrivacyLink({ className = "" }: { className?: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .get<{ privacy_url?: string | null }>("/api/settings/branding")
      .then((b) => { if (alive) setUrl(b.privacy_url || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={`block text-center text-xs text-slate-400 hover:text-indigo-600 ${className}`}
    >
      Adatkezelési tájékoztató
    </a>
  );
}
