<template>
  <section class="coverage-panel" data-testid="coverage-panel">
    <header class="coverage-head">
      <div>
        <h3>实验覆盖视图</h3>
        <p class="page-desc">按时段展示对照组和实验组的覆盖情况，共同覆盖区间才会进入实验结论</p>
      </div>
      <div class="coverage-actions">
        <select v-model.number="selectedId" data-testid="coverage-experiment" @change="reload">
          <option v-for="exp in experiments" :key="exp.id" :value="Number(exp.id)">
            {{ exp['实验名称'] }}
          </option>
        </select>
        <button class="btn" type="button" data-testid="coverage-refresh" @click="reload">刷新覆盖视图</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <table v-if="coverage" class="data-table" data-testid="coverage-table">
      <thead>
        <tr>
          <th>时段</th>
          <th>覆盖类型</th>
          <th>对照组样本</th>
          <th>实验组样本</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="interval in coverage.intervals" :key="interval.period" :data-coverage="interval.coverage">
          <td>{{ interval.period }}</td>
          <td>
            <span class="coverage-badge" :class="coverageClass(interval.coverage)">{{ interval.coverage }}</span>
          </td>
          <td>{{ interval.control_count }}</td>
          <td>{{ interval.treatment_count }}</td>
        </tr>
        <tr v-if="!coverage.intervals.length">
          <td colspan="4" class="empty-state">该实验还没有观测样本</td>
        </tr>
      </tbody>
    </table>

    <p v-if="coverage" class="coverage-summary" data-testid="coverage-summary">
      已具备共同覆盖时段：
      <strong v-if="coverage.common_periods.length">{{ coverage.common_periods.join('、') }}</strong>
      <span v-else>暂无（单组数据不会进入实验结论）</span>
    </p>

    <div v-if="report" class="coverage-result" data-testid="effect-result">
      <div class="analysis-summary">
        <article class="analysis-card">
          <span class="stat-label">对照组均值（{{ report.metric }}）</span>
          <strong class="stat-value">{{ report.effect.mean_a ?? '—' }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">实验组均值（{{ report.metric }}）</span>
          <strong class="stat-value">{{ report.effect.mean_b ?? '—' }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">差异</span>
          <strong class="stat-value" :class="report.effect.significant ? 'trend-up' : ''">
            {{ formatDiff(report) }}
          </strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">结论</span>
          <strong class="stat-value">{{ conclusionText(report) }}</strong>
        </article>
      </div>

      <div class="coverage-detail">
        <div>
          <h4>采样覆盖</h4>
          <p>
            共同覆盖 {{ report.sampling_coverage.common_period_count }}/{{ report.sampling_coverage.total_periods }} 个时段，
            入算样本：对照组 {{ report.sampling_coverage.included_control_count }} 条、
            实验组 {{ report.sampling_coverage.included_treatment_count }} 条
          </p>
        </div>
        <div>
          <h4>排除原因</h4>
          <ul v-if="report.exclusions.length" class="exclusion-list" data-testid="exclusion-list">
            <li v-for="item in report.exclusions" :key="item.period">
              {{ item.reason }}（{{ item.excluded_count }} 条）
            </li>
          </ul>
          <p v-else>无排除样本，全部时段均为共同覆盖</p>
        </div>
        <div>
          <h4>监控结论（有效时段：{{ monitoringPeriods(report) }}）</h4>
          <p v-if="report.monitoring.ok">
            共同覆盖区间内异常 {{ report.monitoring.total_anomalies ?? 0 }} 处
            <template v-if="report.monitoring.results?.length">
              （{{ report.monitoring.results.map((r) => `${r.rule_name}：${r.anomaly_count}`).join('，') }}）
            </template>
          </p>
          <p v-else>{{ report.monitoring.message ?? '监控接口不可用' }}</p>
        </div>
      </div>
    </div>

    <form class="analysis-form backfill-form" data-testid="backfill-form" @submit.prevent="submitBackfill">
      <span class="form-title">补入观测记录</span>
      <label>
        时段
        <input v-model="backfill.period" data-testid="backfill-period" placeholder="如 2026-11-21 下午" required />
      </label>
      <label>
        组别
        <select v-model="backfill.group" data-testid="backfill-group">
          <option>对照组</option>
          <option>实验组</option>
        </select>
      </label>
      <label class="backfill-values">
        观测值（逗号分隔）
        <input v-model="backfill.valuesText" data-testid="backfill-values" placeholder="如 0.432,0.445,0.428" required />
      </label>
      <button class="btn primary" type="submit" data-testid="backfill-submit" :disabled="backfilling">
        {{ backfilling ? '补入中…' : '补入并重算' }}
      </button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import type { EntryRow } from '@/data/types'
import {
  addObservations,
  computeExperimentEffect,
  fetchCoverageView,
  type CoverageView,
  type EffectReport,
} from '@/domain/ab_analysis'

const props = defineProps<{ experiments: EntryRow[] }>()

const selectedId = ref<number>(0)
const coverage = ref<CoverageView | null>(null)
const report = ref<EffectReport | null>(null)
const errorMessage = ref('')
const backfilling = ref(false)
const backfill = ref({ period: '', group: '对照组', valuesText: '' })

function coverageClass(kind: string): string {
  if (kind === '共同覆盖') return 'coverage-both'
  if (kind === '仅对照组') return 'coverage-control'
  return 'coverage-treatment'
}

function formatDiff(value: EffectReport): string {
  const diff = value.effect.diff
  if (diff === undefined) return value.effect.error ?? '—'
  const pct = value.effect.diff_pct
  return `${diff >= 0 ? '+' : ''}${diff}（${pct !== undefined ? `${pct >= 0 ? '+' : ''}${pct}%` : '—'}）`
}

function conclusionText(value: EffectReport): string {
  if (value.effect.error) return value.effect.error
  return value.effect.significant ? '差异显著' : '差异不显著'
}

function monitoringPeriods(value: EffectReport): string {
  const periods = value.monitoring.valid_periods ?? []
  return periods.length ? periods.join('、') : '无'
}

async function reload() {
  if (!selectedId.value) return
  errorMessage.value = ''
  try {
    const [coverageData, effectData] = await Promise.all([
      fetchCoverageView(selectedId.value),
      computeExperimentEffect(selectedId.value),
    ])
    if (!coverageData.ok) throw new Error(coverageData.message ?? '覆盖视图读取失败')
    coverage.value = coverageData
    report.value = effectData.ok ? effectData : null
    if (!effectData.ok) errorMessage.value = effectData.message ?? '实验效果计算失败'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '覆盖视图读取失败'
  }
}

async function submitBackfill() {
  if (!selectedId.value) return
  const values = backfill.value.valuesText
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number)
  if (!values.length || values.some((item) => Number.isNaN(item))) {
    errorMessage.value = '观测值需要是逗号分隔的数字'
    return
  }
  backfilling.value = true
  errorMessage.value = ''
  try {
    const result = await addObservations(selectedId.value, {
      group: backfill.value.group,
      period: backfill.value.period,
      values,
    })
    if (!result.ok) throw new Error(result.message)
    backfill.value.valuesText = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补入观测失败'
  } finally {
    backfilling.value = false
  }
}

watch(
  () => props.experiments,
  (list) => {
    if (!selectedId.value && list.length) {
      selectedId.value = Number(list[0].id)
      void reload()
    }
  },
)

onMounted(() => {
  if (props.experiments.length && !selectedId.value) {
    selectedId.value = Number(props.experiments[0].id)
    void reload()
  }
})
</script>
