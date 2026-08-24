"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { PatternRobotPage } from "@/components/PatternRobotPage";

export default function StandalonePatternRobotPage() {
  const router = useRouter();
  const [userId, setUserId] = useState("");
  useEffect(() => {
    let id = localStorage.getItem("moneymoney-user-id");
    if (!id) {
      id = globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      localStorage.setItem("moneymoney-user-id", id);
    }
    setUserId(id);
  }, []);
  return <main className="standalone-pattern-main"><PatternRobotPage userId={userId} onSelectStock={(symbol) => router.push(`/?symbol=${encodeURIComponent(symbol)}&view=analysis`)} /></main>;
}
