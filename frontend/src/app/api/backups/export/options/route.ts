import { proxyBackupRequest } from "@/lib/backupProxy";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  return proxyBackupRequest(request, "/api/backups/export/options");
}
