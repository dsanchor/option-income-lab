import BackupImportView from "@/components/BackupImportView";

export const metadata = { title: "Import backup — Portfolio Income Lab" };

export default function BackupImportPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Import user-data backup</h1>
        <p className="text-sm text-text-muted">
          Validate and dry-run an .oil-backup.zip archive before a create-only restore.
        </p>
      </div>
      <BackupImportView />
    </div>
  );
}
