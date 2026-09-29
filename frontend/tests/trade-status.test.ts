import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveTradeStatus } from '../src/utils/tradeStatus.ts'

test('only explicit full-fill evidence is presented as a completed fill', () => {
  assert.deepEqual(resolveTradeStatus('4'), { status: 'done', ok: true, status_text: '已成交' })
  assert.deepEqual(resolveTradeStatus('filled'), { status: 'done', ok: true, status_text: '已成交' })
  assert.deepEqual(resolveTradeStatus('submitted'), { status: 'done', ok: null, status_text: '已提交·成交待核实' })
  assert.deepEqual(resolveTradeStatus('ambiguous'), { status: 'done', ok: null, status_text: '提交结果未知·需人工核对' })
})

test('unfilled, partial, canceled and unknown orders are not marked as fills', () => {
  assert.deepEqual(resolveTradeStatus('3'), { status: 'done', ok: null, status_text: '部分成交' })
  assert.deepEqual(resolveTradeStatus('8'), { status: 'done', ok: null, status_text: '已撤单' })
  assert.deepEqual(resolveTradeStatus('10'), { status: 'done', ok: null, status_text: '撤单失败' })
  assert.deepEqual(resolveTradeStatus('n/a'), { status: 'done', ok: null, status_text: '状态待核实' })
  assert.deepEqual(resolveTradeStatus(null), { status: 'done', ok: null, status_text: '状态待核实' })
})

test('submission and rejected states remain distinct', () => {
  assert.deepEqual(resolveTradeStatus('reserved'), { status: 'running', ok: null, status_text: '提交中' })
  assert.deepEqual(resolveTradeStatus('9'), { status: 'failed', ok: false, status_text: '废单' })
  assert.deepEqual(resolveTradeStatus('rejected'), { status: 'failed', ok: false, status_text: '提交失败' })
})
