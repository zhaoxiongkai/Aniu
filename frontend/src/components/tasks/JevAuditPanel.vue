<script setup lang="ts">
import type { JevAssessment } from '@/types'

defineProps<{ assessments: JevAssessment[] }>()
defineEmits<{ refresh: [] }>()

const questionLabels: Record<string, string> = {
  evidence_sufficient: '证据充分',
  contradictory_evidence: '存在矛盾证据',
  requires_review: '需要人工复核',
}

function statusText(status: string) {
  const labels: Record<string, string> = {
    queued: '排队中',
    ok: '已评估',
    disabled: '未启用',
    error: '评估失败',
    skipped: '未执行',
  }
  return labels[status] ?? status
}

function probabilityText(value: number) {
  return Number.isFinite(value) && value >= 0 && value <= 1
    ? `${(value * 100).toFixed(1)}%`
    : '无效概率'
}
</script>

<template>
  <section class="jev-audit" aria-label="Jev 影子评估审计">
    <div class="jev-header">
      <h4>Jev 影子评估 · 仅供审计</h4>
      <button type="button" class="button ghost small" @click="$emit('refresh')">刷新审计结果</button>
    </div>
    <p class="jev-disclaimer">以下概率仅评估提案证据，不是收益预测或交易批准；不参与委托准入、拒绝或成交判定。</p>
    <p v-if="!assessments.length" class="jev-empty">暂无 Jev 影子评估记录；可能尚未入队或审计队列已满，请查看服务端日志。</p>
    <article v-for="assessment in assessments" :key="assessment.id" class="jev-entry">
      <div class="jev-entry-head">
        <strong>{{ assessment.action }} {{ assessment.symbol }}</strong>
        <span>{{ statusText(assessment.status) }}</span>
        <small>委托意图 #{{ assessment.intent_id }}</small>
      </div>
      <div class="jev-meta">
        <span>行情时间 {{ assessment.as_of }}</span>
        <span>证据 {{ assessment.evidence_count }} 条</span>
        <span>模型 {{ assessment.model_used || assessment.model_requested }}</span>
        <span>问题集 {{ assessment.question_set }}</span>
        <span>记录时间 {{ assessment.created_at }}</span>
        <span v-if="assessment.finished_at">完成时间 {{ assessment.finished_at }}</span>
        <span v-if="assessment.latency_ms !== null">耗时 {{ assessment.latency_ms }} ms</span>
        <span v-if="assessment.usage">用量 {{ assessment.usage.input_tokens }} 输入 / {{ assessment.usage.output_tokens }} 输出 token</span>
        <span v-if="assessment.error_code">错误码 {{ assessment.error_code }}</span>
      </div>
      <div v-if="assessment.probabilities" class="jev-probabilities">
        <span v-for="(value, question) in assessment.probabilities" :key="question">
          {{ questionLabels[question] || question }}：{{ probabilityText(value) }}
        </span>
      </div>
    </article>
  </section>
</template>

<style scoped>
.jev-audit {
  display: grid;
  gap: 10px;
  margin-top: 16px;
  padding: 14px;
  border: 1px solid rgba(145, 170, 214, 0.2);
  border-radius: 8px;
  color: #c6d5eb;
}

.jev-audit h4,
.jev-audit p {
  margin: 0;
}

.jev-disclaimer,
.jev-empty,
.jev-meta {
  color: #9ab0cf;
  font-size: 11px;
}

.jev-entry {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid rgba(145, 170, 214, 0.12);
  border-radius: 6px;
}

.jev-entry-head,
.jev-header,
.jev-meta,
.jev-probabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.jev-header {
  align-items: center;
  justify-content: space-between;
}

.jev-probabilities {
  font-size: 12px;
}
</style>
