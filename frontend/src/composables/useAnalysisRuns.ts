import { computed, ref, watch } from 'vue'

import type { ApiDetail, JevAssessment, RawToolPreview, RawToolPreviewDetail, RunDetail, RunSummary, RunSummaryPage, TradeDetail, TradeOrder } from '@/types'
import { resolveTradeStatus } from '@/utils/tradeStatus'

export interface AnalysisRunViewModel {
  id: number
  analysisType: string
  accountScope: 'stock' | 'etf'
  startTime: string
  endTime: string | null
  duration: string
  status: string
  apiCalls: number
  tradeCount: number
  inputTokens: string
  outputTokens: string
  totalTokens: string
  apiDetails: ApiDetail[]
  rawToolPreviews: RawToolPreview[]
  tradeDetails: TradeDetail[]
  jevAssessments: JevAssessment[]
  output: string | null
  summary: string
  detailLoaded: boolean
}

const RUNS_PAGE_SIZE = 100

export type AnalysisRunScopeFilter = 'all' | 'stock' | 'etf'

function formatTokenValue(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? String(value) : '--'
}

function getTokenUsage(detail: RunDetail) {
  const responseUsage = extractUsage(detail.llm_response_payload)
  const requestUsage = extractUsage(detail.llm_request_payload)

  const promptTokens = Number(responseUsage?.prompt_tokens ?? requestUsage?.prompt_tokens ?? 0)
  const completionTokens = Number(responseUsage?.completion_tokens ?? requestUsage?.completion_tokens ?? 0)
  const totalTokens = Number(responseUsage?.total_tokens ?? requestUsage?.total_tokens ?? promptTokens + completionTokens)

  return {
    input: promptTokens > 0 ? String(promptTokens) : '--',
    output: completionTokens > 0 ? String(completionTokens) : '--',
    total: totalTokens > 0 ? String(totalTokens) : '--',
  }
}

function getDuration(startedAt: string, finishedAt: string | null) {
  if (!finishedAt) {
    return '进行中'
  }

  const start = new Date(startedAt).getTime()
  const end = new Date(finishedAt).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) {
    return '--'
  }

  const totalSeconds = Math.floor((end - start) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}分${String(seconds).padStart(2, '0')}秒`
}

function isEtfRun(detail: Pick<RunDetail, 'schedule_name'>) {
  return String(detail.schedule_name || '').trim().startsWith('ETF')
}

function getRunTypeText(detail: Pick<RunDetail, 'run_type' | 'trigger_source' | 'schedule_name'>) {
  if (isEtfRun(detail)) return 'ETF投资任务'
  if (detail.run_type === 'trade') return '交易任务'
  if (detail.run_type === 'analysis') return '分析任务'
  if (detail.trigger_source === 'manual') return '手动运行'
  return '任务运行'
}

function extractUsage(payload: unknown): Record<string, unknown> | undefined {
  if (!payload || typeof payload !== 'object') {
    return undefined
  }

  const directUsage = (payload as { usage?: Record<string, unknown> }).usage
  if (directUsage && typeof directUsage === 'object') {
    return directUsage
  }

  const responses = (payload as { responses?: unknown[] }).responses
  if (Array.isArray(responses)) {
    for (let index = responses.length - 1; index >= 0; index -= 1) {
      const item = responses[index]
      if (!item || typeof item !== 'object') {
        continue
      }
      const usage = (item as { usage?: Record<string, unknown> }).usage
      if (usage && typeof usage === 'object') {
        return usage
      }
    }
  }

  return undefined
}

function getApiToolText(name: string) {
  const mapping: Record<string, { label: string, summary: string }> = {
    mx_get_positions: { label: '获取持仓', summary: '读取当前账户持仓与仓位分布。' },
    mx_get_balance: { label: '获取资产', summary: '读取账户总资产、现金和收益情况。' },
    mx_get_orders: { label: '获取委托', summary: '读取近期委托和成交记录，用于判断交易状态。' },
    mx_get_self_selects: { label: '获取自选', summary: '读取当前自选股列表，辅助观察候选标的。' },
    mx_query_market: { label: '查询行情', summary: '获取目标股票的实时行情和基础市场数据。' },
    mx_search_news: { label: '搜索资讯', summary: '查询相关新闻或公告，辅助判断市场事件影响。' },
    mx_screen_stocks: { label: '筛选股票', summary: '按条件筛选候选标的，缩小分析范围。' },
    mx_manage_self_select: { label: '管理自选', summary: '增删自选股，维护后续关注列表。' },
    mx_moni_trade: { label: '提交模拟交易', summary: '向模拟交易系统提交买入或卖出指令。' },
    mx_moni_cancel: { label: '撤销委托', summary: '撤销尚未完成的模拟委托单。' },
  }
  return mapping[name] ?? { label: name || '未命名调用', summary: '执行一次系统或妙想工具调用。' }
}

function extractTradeName(payload: unknown) {
  if (!payload || typeof payload !== 'object') {
    return ''
  }

  const candidates = [
    (payload as { name?: unknown }).name,
    (payload as { stock_name?: unknown }).stock_name,
    (payload as { stockName?: unknown }).stockName,
    (payload as { security_name?: unknown }).security_name,
    (payload as { securityName?: unknown }).securityName,
  ]

  for (const candidate of candidates) {
    const value = String(candidate ?? '').trim()
    if (value) {
      return value
    }
  }

  const result = (payload as { result?: unknown }).result
  if (result && result !== payload) {
    return extractTradeName(result)
  }

  return ''
}

function getTradeSummary(action: 'buy' | 'sell', symbol: string, name: string, volume: number, price: number | null, amount: number | null) {
  void name
  void price
  void amount

  const displaySymbol = symbol || '--'
  const actionText = action === 'sell' ? '卖出' : '买入'

  return `挂单${actionText}${displaySymbol}共计${volume}股。`
}

function mapApiDetails(detail: RunDetail): ApiDetail[] {
  const tradeToolNames = new Set(['mx_moni_trade', 'mx_moni_cancel'])
  const skillPayloads = detail.skill_payloads && typeof detail.skill_payloads === 'object'
    ? detail.skill_payloads
    : null
  const decisionPayload = detail.decision_payload && typeof detail.decision_payload === 'object'
    ? detail.decision_payload
    : null

  const toolCalls = Array.isArray(skillPayloads?.tool_calls)
    ? skillPayloads?.tool_calls
    : Array.isArray(decisionPayload?.tool_calls)
      ? decisionPayload?.tool_calls
      : []

  return toolCalls
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    .filter((item) => !tradeToolNames.has(String(item.name ?? '')))
    .map((item, idx) => {
      const toolText = getApiToolText(String(item.name ?? ''))
      const result = item.result && typeof item.result === 'object'
        ? item.result as Record<string, unknown>
        : null
      const ok = typeof result?.ok === 'boolean' ? result.ok : null
      return {
        tool_name: String(item.name ?? ''),
        name: toolText.label,
        summary: toolText.summary,
        preview_index: idx,
        tool_call_id: typeof item.id === 'string' ? item.id : null,
        status: ok === false ? 'failed' : 'done',
        ok,
      }
    })
}

function mapTradeDetails(tradeOrders: TradeOrder[], executedActions: Array<Record<string, unknown>> | null): TradeDetail[] {
  if (tradeOrders.length > 0) {
    return tradeOrders.map((order) => {
      const price = order.price
      const action = String(order.action).toUpperCase() === 'SELL' ? 'sell' : 'buy'
      const name = extractTradeName(order.response_payload) || order.symbol
      const amount = price == null ? null : Number((price * order.quantity).toFixed(2))
      const tradeStatus = resolveTradeStatus(order.status)
      return {
        action,
        action_text: action === 'sell' ? '模拟卖出' : '模拟买入',
        symbol: order.symbol,
        name,
        volume: order.quantity,
        price,
        amount,
        summary: getTradeSummary(action, order.symbol, name, order.quantity, price, amount),
        tool_name: null,
        preview_index: null,
        ...tradeStatus,
      }
    })
  }

  return (executedActions ?? [])
    .filter((action) => ['BUY', 'SELL'].includes(String(action.action ?? '').toUpperCase()))
    .map((action) => {
    const actionName = String(action.action ?? '').toUpperCase()
    const actionType = actionName === 'SELL' ? 'sell' : 'buy'
    const price = action.price == null ? null : Number(action.price)
    const volume = Number(action.quantity ?? 0)
    const symbol = String(action.symbol ?? '--')
    const name = String(action.name ?? '').trim() || symbol
    const amount = price == null ? null : Number((price * volume).toFixed(2))
    const tradeStatus = resolveTradeStatus(action.status)
    return {
      action: actionType,
      action_text: actionName === 'SELL' ? '模拟卖出' : '模拟买入',
      symbol,
      name,
      volume,
      price,
      amount,
      summary: getTradeSummary(actionType, symbol, name, volume, price, amount),
      tool_name: null,
      preview_index: null,
      ...tradeStatus,
    }
    })
}

function mapRunSummaryToViewModel(summary: RunSummary): AnalysisRunViewModel {
  return {
    id: summary.id,
    analysisType: getRunTypeText(summary),
    accountScope: isEtfRun(summary) ? 'etf' : 'stock',
    startTime: summary.started_at,
    endTime: summary.finished_at,
    duration: getDuration(summary.started_at, summary.finished_at),
    status: summary.status,
    apiCalls: summary.api_call_count,
    tradeCount: summary.executed_trade_count,
    inputTokens: formatTokenValue(summary.input_tokens),
    outputTokens: formatTokenValue(summary.output_tokens),
    totalTokens: formatTokenValue(summary.total_tokens),
    apiDetails: [],
    rawToolPreviews: [],
    tradeDetails: [],
    jevAssessments: [],
    output: null,
    summary: summary.analysis_summary || '--',
    detailLoaded: false,
  }
}

function mapRunDetailToViewModel(detail: RunDetail): AnalysisRunViewModel {
  const tokenUsage = getTokenUsage(detail)
  const apiDetails = detail.api_details?.length ? detail.api_details : mapApiDetails(detail)
  const rawToolPreviews = Array.isArray(detail.raw_tool_previews) ? detail.raw_tool_previews : []
  const tradeDetails = detail.trade_details?.length ? detail.trade_details : mapTradeDetails(detail.trade_orders, detail.executed_actions)
  const output = detail.output_markdown || detail.final_answer || detail.analysis_summary || detail.error_message || '暂无分析输出'

  return {
    id: detail.id,
    analysisType: getRunTypeText(detail),
    accountScope: isEtfRun(detail) ? 'etf' : 'stock',
    startTime: detail.started_at,
    endTime: detail.finished_at,
    duration: getDuration(detail.started_at, detail.finished_at),
    status: detail.status,
    apiCalls: apiDetails.length,
    tradeCount: tradeDetails.length,
    inputTokens: tokenUsage.input,
    outputTokens: tokenUsage.output,
    totalTokens: tokenUsage.total,
    apiDetails,
    rawToolPreviews,
    tradeDetails,
    jevAssessments: Array.isArray(detail.jev_assessments) ? detail.jev_assessments : [],
    output,
    summary: detail.analysis_summary || '--',
    detailLoaded: true,
  }
}

function isSameDay(value: string, target: Date) {
  const date = new Date(value)
  return date.getFullYear() === target.getFullYear()
    && date.getMonth() === target.getMonth()
    && date.getDate() === target.getDate()
}

function getLatestRun(runs: AnalysisRunViewModel[]) {
  if (runs.length === 0) {
    return null
  }

  return runs.reduce((latest, current) => {
    const latestTime = new Date(latest.startTime).getTime()
    const currentTime = new Date(current.startTime).getTime()
    return currentTime > latestTime ? current : latest
  })
}

export function useAnalysisRuns(options: {
  listRunsPage: (options?: { limit?: number, date?: string, status?: string, beforeId?: number }) => Promise<RunSummaryPage>
  loadRunDetail: (runId: number, options?: { force?: boolean }) => Promise<RunDetail>
  loadRawToolPreview: (runId: number, previewIndex: number) => Promise<RawToolPreviewDetail>
}) {
  const selectedRun = ref<AnalysisRunViewModel | null>(null)
  const selectedRunLoading = ref(false)
  const renderedOutputHtml = ref('')
  const renderedOutputLoading = ref(false)
  const todayRuns = ref<AnalysisRunViewModel[]>([])
  const historyRuns = ref<AnalysisRunViewModel[]>([])
  const historyHasMore = ref(false)
  const historyLoading = ref(false)
  const historyCursor = ref<number | null>(null)
  const historySummaries = ref<RunSummary[]>([])
  const scopeFilter = ref<AnalysisRunScopeFilter>('all')
  const selectedDate = ref('')
  const loading = ref(false)
  const errorMessage = ref('')
  const runCache = new Map<number, AnalysisRunViewModel>()
  const markdownCache = new Map<string, string>()
  const sourceSummaries = ref<RunSummary[]>([])
  const rawToolPreviewRequests = new Map<string, Promise<RawToolPreviewDetail>>()

  let markdownRendererPromise: Promise<((content: string) => string)> | null = null
  let selectedOutputRenderTicket = 0
  let historyRequestTicket = 0

  const allRuns = computed(() => sourceSummaries.value)

  function shouldIncludeRun(run: AnalysisRunViewModel) {
    if (!run) return false
    return scopeFilter.value === 'all' || run.accountScope === scopeFilter.value
  }

  function filterVisibleRuns(runs: AnalysisRunViewModel[]) {
    return runs.filter(shouldIncludeRun)
  }

  async function hydrateSelectedRun(runId: number, force = false) {
    selectedRunLoading.value = true

    try {
      const detail = await ensureRunDetail(runId, force)
      if (selectedRun.value?.id === runId) {
        selectedRun.value = detail
      }
    } finally {
      if (selectedRun.value?.id === runId) {
        selectedRunLoading.value = false
      }
    }
  }

  async function syncSelectedRun(runs: AnalysisRunViewModel[]) {
    if (selectedRun.value && runs.some((run) => run.id === selectedRun.value?.id)) {
      selectedRun.value = runs.find((run) => run.id === selectedRun.value?.id) ?? selectedRun.value
      await hydrateSelectedRun(selectedRun.value.id)
      return
    }

    selectedRun.value = getLatestRun(runs)
    if (selectedRun.value) {
      await hydrateSelectedRun(selectedRun.value.id)
      return
    }

    selectedRunLoading.value = false
  }

  async function applyScopeFilter(nextScope: AnalysisRunScopeFilter) {
    scopeFilter.value = nextScope
    const today = new Date()
    const todaysSummaries = sourceSummaries.value.filter((item) => isSameDay(item.started_at, today))
    todayRuns.value = filterVisibleRuns(todaysSummaries.map(mapRunSummaryToViewModel))

    if (selectedDate.value) {
      historyRuns.value = filterVisibleRuns(historySummaries.value.map(mapRunSummaryToViewModel))
    } else {
      historyRuns.value = []
    }

    await syncSelectedRun(selectedDate.value ? historyRuns.value : todayRuns.value)
  }

  async function ensureRunDetail(runId: number, force = false) {
    if (!force && runCache.has(runId)) {
      return runCache.get(runId)!
    }

    const detail = await options.loadRunDetail(runId, { force })
    const mapped = mapRunDetailToViewModel(detail)
    runCache.set(runId, mapped)
    return mapped
  }

  async function refreshRunDetail(runId: number) {
    const detail = await ensureRunDetail(runId, true)
    if (selectedRun.value?.id === runId) {
      selectedRun.value = detail
    }
    return detail
  }

  async function ensureRawToolPreview(runId: number, previewIndex: number): Promise<RawToolPreview> {
    const run = runCache.get(runId)
    const cachedPreview = run?.rawToolPreviews.find((item) => item.preview_index === previewIndex) ?? null
    if (cachedPreview && !cachedPreview.truncated) {
      return cachedPreview
    }

    const requestKey = `${runId}:${previewIndex}`
    const pendingRequest = rawToolPreviewRequests.get(requestKey)
    if (pendingRequest) {
      const detail = await pendingRequest
      return applyRawToolPreviewDetail(runId, detail)
    }

    const request = options.loadRawToolPreview(runId, previewIndex)
    rawToolPreviewRequests.set(requestKey, request)
    try {
      const detail = await request
      return applyRawToolPreviewDetail(runId, detail)
    } finally {
      rawToolPreviewRequests.delete(requestKey)
    }
  }

  function applyRawToolPreviewDetail(runId: number, detail: RawToolPreviewDetail): RawToolPreview {
    const run = runCache.get(runId)
    const nextPreview: RawToolPreview = {
      preview_index: detail.preview_index,
      tool_name: detail.tool_name,
      display_name: detail.display_name,
      summary: detail.summary,
      preview: detail.full_preview,
      truncated: false,
    }

    if (!run) {
      return nextPreview
    }

    const nextRun: AnalysisRunViewModel = {
      ...run,
      rawToolPreviews: run.rawToolPreviews.map((item) => (
        item.preview_index === detail.preview_index ? nextPreview : item
      )),
    }
    runCache.set(runId, nextRun)

    if (selectedRun.value?.id === runId) {
      selectedRun.value = nextRun
    }

    todayRuns.value = todayRuns.value.map((item) => (item.id === runId ? nextRun : item))
    historyRuns.value = historyRuns.value.map((item) => (item.id === runId ? nextRun : item))

    return nextPreview
  }

  async function loadInitialRuns(config: { syncSelection?: boolean } = {}) {
    const { syncSelection = true } = config
    loading.value = true
    errorMessage.value = ''

    try {
      const page = await options.listRunsPage({ limit: RUNS_PAGE_SIZE })
      sourceSummaries.value = page.items
      const today = new Date()
      const todaysSummaries = sourceSummaries.value.filter((item) => isSameDay(item.started_at, today))
      const mappedTodayRuns = todaysSummaries.map(mapRunSummaryToViewModel)

      todayRuns.value = filterVisibleRuns(mappedTodayRuns)

      if (syncSelection) {
        await syncSelectedRun(todayRuns.value)
      }
    } catch (error) {
      errorMessage.value = (error as Error).message
      todayRuns.value = []
      selectedRun.value = null
    } finally {
      loading.value = false
    }
  }

  async function selectRun(run: AnalysisRunViewModel, options?: { force?: boolean }) {
    selectedRun.value = run
    if (run.detailLoaded && !options?.force) {
      selectedRunLoading.value = false
      return
    }

    await hydrateSelectedRun(run.id, options?.force === true)
  }

  async function loadHistoryRuns() {
    const requestTicket = ++historyRequestTicket
    const date = selectedDate.value
    historyCursor.value = null
    historyHasMore.value = false
    historySummaries.value = []
    historyRuns.value = []
    if (!selectedDate.value) {
      historyRuns.value = []
      return
    }

    errorMessage.value = ''
    historyLoading.value = true

    try {
      const page = await options.listRunsPage({
        limit: RUNS_PAGE_SIZE,
        date,
      })
      if (requestTicket !== historyRequestTicket || selectedDate.value !== date) return
      const matched = page.items
      sourceSummaries.value = mergeSourceSummaries(sourceSummaries.value, matched)
      historySummaries.value = matched
      historyCursor.value = page.next_before_id
      historyHasMore.value = page.has_more && page.next_before_id !== null
      historyRuns.value = filterVisibleRuns(matched.map(mapRunSummaryToViewModel))

      if (selectedDate.value) {
        await syncSelectedRun(historyRuns.value)
      }
    } catch (error) {
      if (requestTicket !== historyRequestTicket) return
      errorMessage.value = (error as Error).message
      historyRuns.value = []
    } finally {
      if (requestTicket === historyRequestTicket) historyLoading.value = false
    }
  }

  async function loadMoreHistoryRuns() {
    const requestTicket = historyRequestTicket
    const date = selectedDate.value
    const beforeId = historyCursor.value
    if (!date || !historyHasMore.value || beforeId === null || historyLoading.value) return
    historyLoading.value = true
    errorMessage.value = ''
    try {
      const page = await options.listRunsPage({ limit: RUNS_PAGE_SIZE, date, beforeId })
      if (requestTicket !== historyRequestTicket || selectedDate.value !== date) return
      historySummaries.value = mergeSourceSummaries(historySummaries.value, page.items)
      sourceSummaries.value = mergeSourceSummaries(sourceSummaries.value, page.items)
      historyCursor.value = page.next_before_id
      historyHasMore.value = page.has_more && page.next_before_id !== null
      historyRuns.value = filterVisibleRuns(historySummaries.value.map(mapRunSummaryToViewModel))
    } catch (error) {
      if (requestTicket === historyRequestTicket) errorMessage.value = (error as Error).message
    } finally {
      if (requestTicket === historyRequestTicket) historyLoading.value = false
    }
  }

  async function getMarkdownRenderer() {
    if (!markdownRendererPromise) {
      markdownRendererPromise = Promise.all([
        import('dompurify'),
        import('marked'),
      ]).then(([domPurifyModule, markedModule]) => {
        const DOMPurify = domPurifyModule.default
        const { marked } = markedModule
        return (content: string) => {
          const rawHtml = marked.parse(content)
          return DOMPurify.sanitize(typeof rawHtml === 'string' ? rawHtml : '')
        }
      })
    }

    return markdownRendererPromise
  }

  async function renderSelectedOutput(content: string | null) {
    const ticket = ++selectedOutputRenderTicket

    if (!content) {
      if (ticket === selectedOutputRenderTicket) {
        renderedOutputHtml.value = ''
        renderedOutputLoading.value = false
      }
      return
    }

    const cached = markdownCache.get(content)
    if (cached) {
      if (ticket === selectedOutputRenderTicket) {
        renderedOutputHtml.value = cached
        renderedOutputLoading.value = false
      }
      return
    }

    renderedOutputLoading.value = true
    try {
      const renderMarkdown = await getMarkdownRenderer()
      const sanitized = renderMarkdown(content)
      markdownCache.set(content, sanitized)
      if (ticket === selectedOutputRenderTicket) {
        renderedOutputHtml.value = sanitized
      }
    } finally {
      if (ticket === selectedOutputRenderTicket) {
        renderedOutputLoading.value = false
      }
    }
  }

  watch(
    () => selectedRun.value?.output ?? null,
    (content) => {
      void renderSelectedOutput(content)
    },
    { immediate: true },
  )

  function mergeSourceSummaries(existing: RunSummary[], incoming: RunSummary[]) {
    const merged = new Map<number, RunSummary>()
    for (const item of existing) {
      merged.set(item.id, item)
    }
    for (const item of incoming) {
      merged.set(item.id, item)
    }
    return [...merged.values()].sort((a, b) => {
      const timeDelta = new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
      if (timeDelta !== 0) {
        return timeDelta
      }
      return b.id - a.id
    })
  }

  return {
    selectedRun,
    selectedRunLoading,
    todayRuns,
    historyRuns,
    historyHasMore,
    historyLoading,
    selectedDate,
    scopeFilter,
    loading,
    errorMessage,
    renderedOutputHtml,
    renderedOutputLoading,
    loadInitialRuns,
    applyScopeFilter,
    selectRun,
    refreshRunDetail,
    ensureRawToolPreview,
    loadHistoryRuns,
    loadMoreHistoryRuns,
  }
}
