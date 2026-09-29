import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('settings default trading off and preserve backend risk policy on load', () => {
  const store = readFileSync(new URL('../src/stores/legacy.ts', import.meta.url), 'utf-8')

  assert.match(store, /trade_enabled: false/)
  assert.match(store, /risk_cash_only: false/)
  assert.match(store, /risk_max_order_value: null/)
  assert.match(store, /risk_max_daily_value: null/)
  assert.match(store, /settings\.trade_enabled = payload\.trade_enabled === true/)
  assert.match(store, /settings\.risk_cash_only = payload\.risk_cash_only === true/)
  assert.match(store, /settings\.risk_max_order_value = payload\.risk_max_order_value \?\? null/)
  assert.match(store, /settings\.risk_max_daily_value = payload\.risk_max_daily_value \?\? null/)
  assert.match(store, /risk_max_order_value: riskMaxOrderValue/)
  assert.match(store, /risk_max_daily_value: riskMaxDailyValue/)
  assert.match(store, /risk_cash_only: settings\.risk_cash_only/)
  assert.match(store, /savedTradePolicy\.value = \{/)
})

test('settings cash-only mode clears amount caps; capped mode requires positive limits', () => {
  const view = readFileSync(new URL('../src/views/SettingsView.vue', import.meta.url), 'utf-8')
  const store = readFileSync(new URL('../src/stores/legacy.ts', import.meta.url), 'utf-8')

  assert.match(view, /v-model="settings\.risk_cash_only" type="checkbox" @change="onRiskModeChange"/)
  assert.match(view, /if \(settings\.value\.risk_cash_only\) \{\s+settings\.value\.risk_max_order_value = null\s+settings\.value\.risk_max_daily_value = null/)
  assert.match(view, /v-model\.number="settings\.risk_max_order_value"/)
  assert.match(view, /v-model\.number="settings\.risk_max_daily_value"/)
  assert.match(view, /v-model="settings\.trade_enabled" type="checkbox"/)
  assert.match(view, /:disabled="busy \|\| \(settings\.trade_enabled && !riskPolicyValid\)"/)
  assert.match(view, /settings\.value\.risk_cash_only \|\| \(/)
  assert.match(view, /settings\.value\.risk_cash_only !== saved\.risk_cash_only/)
  assert.match(view, /仅以可用资金和可卖持仓约束/)
  assert.match(view, /默认暂停交易/)
  assert.match(view, /服务器配置：交易权限已启用/)
  assert.match(view, /当前修改尚未保存/)
  assert.match(view, /当前版本尚无可信行情时间戳来源/)
  assert.doesNotMatch(view, /交易待保存：保存设置后才生效/)
  assert.match(store, /!settings\.risk_cash_only && Number\.isFinite\(maxOrder\)/)
  assert.match(store, /!settings\.risk_cash_only && Number\.isFinite\(maxDaily\)/)
  assert.match(store, /settings\.trade_enabled && !settings\.risk_cash_only && \(riskMaxOrderValue === null \|\| riskMaxDailyValue === null\)/)
})
