import type { TradeDetail } from '@/types'

type TradeStatus = Pick<TradeDetail, 'status' | 'ok' | 'status_text'>

const orderStatusText: Record<string, string> = {
  '1': '未报',
  '2': '已报',
  '3': '部分成交',
  '4': '已成交',
  '5': '部分成交待撤',
  '6': '已报待撤',
  '7': '部分撤单',
  '8': '已撤单',
  '9': '废单',
  '10': '撤单失败',
}

// Legacy records and stream events may not contain the backend's trade_details.
// Only an explicit full-fill status is evidence that the order was filled.
export function resolveTradeStatus(value: unknown): TradeStatus {
  const text = String(value ?? '').trim().toLowerCase()
  if (text === '9') return { status: 'failed', ok: false, status_text: '废单' }
  if (text && ['fail', 'error', 'reject'].some((flag) => text.includes(flag))) {
    return { status: 'failed', ok: false, status_text: '提交失败' }
  }
  if (['4', 'filled', 'fully_filled', '已成交', '已成'].includes(text)) {
    return { status: 'done', ok: true, status_text: '已成交' }
  }
  if (['reserved', 'submitting'].includes(text)) {
    return { status: 'running', ok: null, status_text: '提交中' }
  }
  if (['ambiguous', 'unknown'].includes(text)) {
    return { status: 'done', ok: null, status_text: '提交结果未知·需人工核对' }
  }
  if (['submitted', 'response_received', 'accepted'].includes(text)) {
    return { status: 'done', ok: null, status_text: '已提交·成交待核实' }
  }
  if (orderStatusText[text]) {
    return { status: 'done', ok: null, status_text: orderStatusText[text] }
  }
  return { status: 'done', ok: null, status_text: '状态待核实' }
}
