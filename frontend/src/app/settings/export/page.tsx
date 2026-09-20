import BackupExportView from "@/components/BackupExportView";
import { apiFetch } from "@/lib/api";
import type { BackupExportOptions } from "@/types/backup";

export const dynamic = "force-dynamic";
export const metadata = { title: "Export backup — Portfolio Income Lab" };

const emptyOptions: BackupExportOptions = { presets: [], sections: [] };

export default async function BackupExportPage() {
  let options = emptyOptions;
  let optionsError: string | null = null;

  try {
    options = await apiFetch<BackupExportOptions>("/api/backups/export/options");
  } catch (error) {
    optionsError = error instanceof Error ? error.message : "Backup options are unavailable";
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Export user-data backup</h1>
        <p className="text-sm text-text-muted">
          Preview the server-authoritative scope, then download a versioned backup archive.
        </p>
      </div>
      <BackupExportView
        options={options}
        optionsError={optionsError}
      />
    </div>
  );
}
