import type { components } from "./generated/api";
export type Definition = components["schemas"]["WorkflowDefinition"];
export type WorkflowNode = components["schemas"]["WorkflowNode"];
export type EvalCase = components["schemas"]["EvaluationCase"];
export interface User {
  id: string;
  name: string;
  email: string;
  is_system_admin: boolean;
  active: boolean;
}
export interface Workspace {
  id: string;
  name: string;
  description: string;
  revision: number;
  role: "admin" | "member";
}
export interface Document {
  id: string;
  name: string;
  family_id: string;
  version: number;
  size: number;
  status: string;
  error: string | null;
  chunk_count: number;
  introduced_revision: number | null;
  superseded_revision: number | null;
  created_at: string;
}
export type Version = components["schemas"]["WorkflowVersionContract"];
export type Workflow = components["schemas"]["WorkflowContract"];
export type Source = components["schemas"]["SourceContract"];
export type Answer = components["schemas"]["Answer"];
export type Run = components["schemas"]["RunContract"];
export type Trace = components["schemas"]["StepTraceContract"];
export interface Conversation {
  id: string;
  workflow_id: string;
  title: string;
  created_at: string;
  runs?: Run[];
}
export type Suite = components["schemas"]["EvaluationSuiteContract"];
export type Evaluation = components["schemas"]["EvaluationReportContract"];
export interface Configuration {
  environment: string;
  model_profile: Record<string, string | number>;
  job_mode: string;
  max_upload_mb: number;
  run_deadline_seconds: number;
  trace_retention_days: number;
}
export interface Audit {
  id: string;
  action: string;
  target_id: string;
  created_at: string;
  detail: Record<string, unknown>;
}
export interface Feedback {
  id: string;
  run_id: string;
  question: string;
  rating: number;
  comment: string;
  promoted_suite_id: string | null;
}
