import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('tasks view provides history pagination and shows submitted orders separately from fills', () => {
  const source = readFileSync(new URL('../src/views/TasksView.vue', import.meta.url), 'utf-8')

  assert.match(source, /historyHasMore/)
  assert.match(source, /loadMoreHistoryRuns/)
  assert.match(source, /加载更多历史记录/)
  assert.match(source, /getTradeItemStatusText\(trade\)/)
  assert.match(source, /trade\.ok === true \? 'is-success' : 'is-pending'/)
  assert.match(source, /manualStartError/)
  assert.doesNotMatch(source, /loadMoreTodayRuns/)
})

test('tasks view keeps the today run group visible while a live placeholder card exists', () => {
  const source = readFileSync(new URL('../src/views/TasksView.vue', import.meta.url), 'utf-8')

  assert.match(source, /todayRuns\.length \|\| livePlaceholderVisible/)
})

test('analysis runs composable pages through history with the backend cursor', () => {
  const source = readFileSync(new URL('../src/composables/useAnalysisRuns.ts', import.meta.url), 'utf-8')

  assert.match(source, /const RUNS_PAGE_SIZE = 100/)
  assert.match(source, /listRunsPage\(\{ limit: RUNS_PAGE_SIZE \}\)/)
  assert.match(source, /async function loadMoreHistoryRuns/)
  assert.match(source, /date, beforeId/)
  assert.doesNotMatch(source, /async function loadMoreTodayRuns/)
})

test('manual trade run bypasses disabled schedules for both A-shares and ETFs', () => {
  const source = readFileSync(new URL('../src/views/TasksView.vue', import.meta.url), 'utf-8')
  const handler = source.match(/async function handleManualTrade\(\) \{([\s\S]*?)\n\}/)?.[1] ?? ''

  assert.match(handler, /runType: 'trade'/)
  assert.doesNotMatch(handler, /scheduleId:/)
  assert.doesNotMatch(handler, /tradeScheduleId/)
  assert.match(source, /支持 A 股与已验证 ETF/)
})

test('run stream reconciles an unexpected EOF against persisted run details', () => {
  const source = readFileSync(new URL('../src/composables/useRunStream.ts', import.meta.url), 'utf-8')

  assert.match(source, /await recoverUnexpectedStreamEnd\(id,/)
  assert.match(source, /const detail = await api\.getRun\(id\)/)
  assert.match(source, /applyTerminalDetail\(detail\)/)
})

test('task detail maps Jev assessments to a separate read-only audit section', () => {
  const view = readFileSync(new URL('../src/views/TasksView.vue', import.meta.url), 'utf-8')
  const runs = readFileSync(new URL('../src/composables/useAnalysisRuns.ts', import.meta.url), 'utf-8')
  const panel = readFileSync(new URL('../src/components/tasks/JevAuditPanel.vue', import.meta.url), 'utf-8')

  assert.match(runs, /jevAssessments: Array\.isArray\(detail\.jev_assessments\)/)
  assert.match(view, /<JevAuditPanel/)
  assert.match(view, /:assessments="selectedRun\.jevAssessments"/)
  assert.match(panel, /仅供审计/)
  assert.match(panel, /不参与委托准入、拒绝或成交判定/)
  assert.match(panel, /assessment\.error_code/)
  assert.match(panel, /刷新审计结果/)
  assert.match(view, /@refresh="refreshJevAudit"/)
})
