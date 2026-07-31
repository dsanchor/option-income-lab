import Link from "next/link";
import ActivityChat from "@/components/ActivityChat";
import { apiFetch } from "@/lib/api";
import type { ActivityDetail } from "@/types/activity-detail";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ activityId: string }>;
}) {
  const { activityId } = await params;
  return { title: `Discuss Activity ${activityId} — Option Income Lab` };
}

export default async function ActivityChatPage({
  params,
}: {
  params: Promise<{ activityId: string }>;
}) {
  const { activityId } = await params;
  let data: ActivityDetail | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<ActivityDetail>(`/api/activities/${encodeURIComponent(activityId)}`);
    if (data?.error) {
      error = data.error;
      data = null;
    }
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load activity";
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold">Discuss Activity</h1>
        <Link
          href="/"
          className="inline-block rounded-[var(--radius-pill)] border border-border bg-bg-input px-4 py-2 text-sm text-text-muted transition-colors hover:bg-hover"
        >
          ← Back to Dashboard
        </Link>
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error ?? "Activity not found"}
        </div>
      </div>
    );
  }

  return (
    <ActivityChat activityId={activityId} symbol={data.symbol} displayName={data.display_name} />
  );
}
