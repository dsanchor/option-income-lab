import { proxyBackupRequest } from "@/lib/backupProxy";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  return proxyBackupRequest(
    request,
    `/api/backups/import/runs/${encodeURIComponent(runId)}`,
  );
}
