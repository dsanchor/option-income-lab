export type BackupPreset =
  | "recommended_full_user_backup"
  | "portfolio_only"
  | "symbols_and_configuration"
  | "custom"
  | string;

export interface BackupPresetDefinition {
  id: BackupPreset;
  name?: string;
  label?: string;
  description?: string;
  recommended?: boolean;
  sections?: string[];
}

export interface BackupSectionDefinition {
  id?: string;
  name: string;
  label?: string;
  description?: string;
  default_selected?: boolean;
  required?: boolean;
}

export interface BackupExportOptions {
  presets: BackupPresetDefinition[];
  sections: Array<BackupSectionDefinition | string>;
  filters?: string[];
  supported_filters?: string[] | Record<string, boolean>;
  permanent_exclusions?: string[];
  dependency_rules?: string[];
}

export interface BackupExportSelection {
  preset: BackupPreset;
  sections?: string[];
  filters?: {
    symbols?: string[];
    account_ids?: string[];
    date_from?: string;
    date_to?: string;
  };
  include_paper_positions?: boolean;
  include_source_row?: boolean;
}

export interface BackupDependencyAddition {
  section?: string;
  name?: string;
  reason?: string;
  count?: number;
}

export interface BackupExportPreview {
  requested_sections: string[];
  effective_sections: string[];
  added_dependencies:
    | Array<BackupDependencyAddition | string>
    | Record<string, number>;
  counts: Record<string, number>;
  warnings: string[];
  exclusions: string[];
  selection_fingerprint: string;
  include_source_row?: boolean;
  include_paper_positions?: boolean;
}

export type BackupImportRecordStatus =
  | "CREATE"
  | "SKIP_IDENTICAL"
  | "CONFLICT_REQUIRES_CHOICE"
  | "BLOCKED_MISSING_REFERENCE"
  | "BLOCKED_INVARIANT"
  | "REDACTED_IGNORED"
  | string;

export interface BackupManifestSummary {
  format?: string;
  archive_version?: number;
  schema_version?: number;
  export_id?: string;
  exported_at?: string;
  preset?: string;
}

export interface BackupValidationReport {
  valid: boolean;
  archive_sha256?: string;
  manifest?: BackupManifestSummary;
  manifest_summary?: BackupManifestSummary;
  compatibility?: string | { compatible?: boolean; message?: string };
  compatible?: boolean;
  checksums_valid?: boolean;
  section_counts?: Record<string, number>;
  warnings?: string[];
  dependency_errors?: string[];
  collisions?: Array<string | Record<string, unknown>>;
  errors?: string[];
  tampered?: boolean;
  future_schema?: boolean;
}

export interface BackupDryRunRecord {
  section?: string;
  logical_key?: string;
  id?: string;
  status: BackupImportRecordStatus;
  reason?: string;
}

export interface BackupDryRunPlan {
  valid?: boolean;
  dry_run_fingerprint: string;
  destination_snapshot_digest?: string;
  records?: BackupDryRunRecord[];
  per_record_statuses?: BackupDryRunRecord[];
  ordering?: string[];
  predicted_controls?: Record<string, unknown>;
  controls?: Record<string, unknown>;
  summary?: string;
  human_summary?: string;
  warnings?: string[];
  dependency_errors?: string[];
  collisions?: Array<string | Record<string, unknown>>;
  blocking_errors?: string[];
  errors?: string[];
}

export interface BackupImportResult {
  import_run_id?: string;
  status: "COMPLETED" | "ROLLED_BACK" | "PARTIAL_REQUIRES_ATTENTION" | string;
  summary?: string;
  human_summary?: string;
  created_count?: number;
  skipped_count?: number;
  counts?: Record<string, number>;
  created?: Record<string, number>;
  skipped?: Record<string, number>;
  warnings?: string[];
  errors?: string[];
  controls?: Record<string, unknown>;
}
