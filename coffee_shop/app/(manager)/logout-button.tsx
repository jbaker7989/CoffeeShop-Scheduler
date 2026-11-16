"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export function LogoutButton() {
  const router = useRouter();
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  async function logout() {
    setIsLoggingOut(true);

    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
      });

      if (response.ok) {
        router.replace("/login");
        router.refresh();
      }
    } finally {
      setIsLoggingOut(false);
    }
  }

  return (
    <button
      className="rounded-lg border border-[#cbd4c7] px-3 py-1.5 text-sm font-medium text-[#40503d] transition hover:border-[#82967d] hover:bg-[#eff4eb] focus:outline-none focus:ring-4 focus:ring-[#dce8d5] disabled:cursor-not-allowed disabled:opacity-60"
      disabled={isLoggingOut}
      onClick={logout}
      type="button"
    >
      {isLoggingOut ? "Signing out…" : "Log out"}
    </button>
  );
}
