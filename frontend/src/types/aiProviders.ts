export type AiSettingSource = "override" | "legacy" | "inherited";

export interface AiFunctionSetting {
  id: string;
  label: string;
  group: string;
  description: string;
  provider: string;
  model: string;
  effective_provider: string;
  effective_model: string;
  inherited_provider: string;
  inherited_model: string;
  provider_source: AiSettingSource;
  model_source: AiSettingSource;
}

export interface AiProvidersConfig {
  providers: string[];
  functions: AiFunctionSetting[];
}
