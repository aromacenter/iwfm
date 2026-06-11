"use client";

/** Belépési pont: szerepkör szerint irányít a megfelelő oldalra. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    api
      .get<AuthUser>("/api/auth/me")
      .then((user) =>
        router.replace(user.role === "employee" ? "/beosztasom" : "/beosztas")
      )
      .catch(() => router.replace("/login"));
  }, [router]);
  return <div className="p-10 text-slate-500">Betöltés…</div>;
}
