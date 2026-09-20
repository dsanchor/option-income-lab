import { proxyBackupRequest } from "@/lib/backupProxy";

export function POST(request: Request) {
  return proxyBackupRequest(request, "/api/backups/export/run");
}
