"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import EconomicsOverviewView from "@/components/EconomicsOverviewView";

export default function EconomicsOverviewPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("type") || params.get("status")) {
      const qs = params.toString();
      router.replace(qs ? `/economics/options?${qs}` : "/economics/options");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) {
    return (
      <div className="surface px-4 py-12 text-center text-text-muted">
        Loading economics overview…
      </div>
    );
  }

  return <EconomicsOverviewView />;
}
