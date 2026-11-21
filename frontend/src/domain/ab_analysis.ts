/** 实验分析领域逻辑：覆盖视图、可比口径效果计算、观测补录和监控联动。 */
import { request } from '@/api/client'

export type CoverageInterval = {
  period: string
  coverage: '共同覆盖' | '仅对照组' | '仅实验组'
  control_count: number
  treatment_count: number
  control_values: number[]
  treatment_values: number[]
}

export type CoverageView = {
  ok: boolean
  message?: string
  experiment_id: number
  experiment_name: string
  metric: string
  dataset: string
  intervals: CoverageInterval[]
  common_periods: string[]
  control_only_periods: string[]
  treatment_only_periods: string[]
}

export type Exclusion = {
  period: string
  coverage: string
  excluded_group: string
  excluded_count: number
  excluded_values: number[]
  reason: string
}

export type SamplingCoverage = {
  common_periods: string[]
  common_period_count: number
  total_periods: number
  included_control_count: number
  included_treatment_count: number
}

export type EffectStats = {
  mean_a?: number
  mean_b?: number
  diff?: number
  diff_pct?: number
  ci_lower?: number
  ci_upper?: number
  significant?: boolean
  confidence?: number
  error?: string
}

export type MonitoringResult = {
  ok: boolean
  message?: string
  valid_periods?: string[]
  total_anomalies?: number
  results?: {
    rule_name: string
    algorithm: string
    data_points: number
    anomaly_count: number
    alert_level: string
    valid_periods?: string[]
    anomalies: { period?: string; group?: string; value?: number }[]
  }[]
}

export type EffectReport = {
  ok: boolean
  message?: string
  experiment_name: string
  metric: string
  effect: EffectStats
  sampling_coverage: SamplingCoverage
  exclusions: Exclusion[]
  monitoring: MonitoringResult
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
  return (await response.json()) as T
}

/** 实验覆盖视图：按时段列出共同覆盖和仅单组有值的区间。 */
export async function fetchCoverageView(experimentId: number): Promise<CoverageView> {
  const response = await request(`/api/analytics/ab-analysis/${experimentId}/coverage`)
  return (await response.json()) as CoverageView
}

/** 按可比口径重算实验效果：共同覆盖区间进入结论，结果带采样覆盖和排除原因。 */
export function computeExperimentEffect(experimentId: number): Promise<EffectReport> {
  return postJson<EffectReport>(`/api/analytics/ab-analysis/${experimentId}/effect`)
}

/** 补入观测记录（如下午的对照组），返回重算后的覆盖视图。 */
export function addObservations(
  experimentId: number,
  payload: { group: string; period: string; values: number[] },
): Promise<{ ok: boolean; message: string; coverage?: CoverageView }> {
  return postJson(`/api/analytics/ab-analysis/${experimentId}/observations`, payload)
}
