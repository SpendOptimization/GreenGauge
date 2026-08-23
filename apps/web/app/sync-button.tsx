"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function SyncButton() {
  const router = useRouter();
  const [status, setStatus] = useState<"idle" | "syncing" | "done" | "error">("idle");

  async function syncIssues() {
    setStatus("syncing");
    try {
      const response = await fetch(`${API_URL}/api/v1/github/sync`, { method: "POST" });
      if (!response.ok) throw new Error("Sync failed");
      setStatus("done");
      router.refresh();
      window.setTimeout(() => setStatus("idle"), 1800);
    } catch {
      setStatus("error");
    }
  }

  const labels = {
    idle: "Sync GitHub",
    syncing: "Syncing…",
    done: "Synced",
    error: "Retry sync",
  };

  return (
    <button className={`sync-button ${status}`} type="button" onClick={syncIssues} disabled={status === "syncing"}>
      <span className="sync-icon">↻</span>{labels[status]}
    </button>
  );
}

