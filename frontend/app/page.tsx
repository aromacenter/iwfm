"use client";

/** Belépési pont: szerepkör szerint irányít a megfelelő oldalra. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { AuthUser } from "@/lib/types";

export default function Home() {
  const router = useRouter();
  const { t } = useT();
  useEffect(() => {
    api
      .get<AuthUser>("/api/auth/me")
      .then((user) =>
        router.replace(user.role === "employee" ? "/beosztasom" : "/vezerlopult")
      )
      .catch(() => router.replace("/login"));
  }, [router]);
  return <div className="p-10 text-slate-500">{t("common.loading")}</div>;
}
