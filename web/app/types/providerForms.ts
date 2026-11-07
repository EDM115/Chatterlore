export interface LocalInferenceOptions {
  revision?: string;
  tokenizer_revision?: string;
  device?: string;
  dtype?: string;
  cache_dir?: string;
  local_files_only?: boolean;
  batch_size?: number;
  normalize_embeddings?: boolean;
  truncate_dim?: number;
  prompt_mode?: string;
  max_input_tokens?: number;
  max_new_tokens?: number;
  top_p?: number;
  queue_timeout_seconds?: number;
  inference_timeout_seconds?: number;
}

export interface ProviderForm {
  name: string;
  provider_kind: string;
  base_url: string;
  chat_model: string;
  embedding_model: string;
  chat_capable: boolean;
  embedding_capable: boolean;
  reasoning_capable: boolean;
  local: boolean;
  redact_before_ai: boolean;
  fake_embedding_dimensions: number;
  api_dialect: "auto" | "openai" | "deepseek" | "generic";
  auth_mode: "api_key" | "none";
  local_chat_options: LocalInferenceOptions;
  local_embedding_options: LocalInferenceOptions;
}
