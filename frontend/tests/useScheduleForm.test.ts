import assert from 'node:assert/strict'
import test from 'node:test'

import { useScheduleForm } from '../src/composables/useScheduleForm.ts'

test('getMorningRunTimes supports select string values', () => {
  const { scheduleSettings, getMorningRunTimes } = useScheduleForm()

  scheduleSettings.morning.runCount = '3' as unknown as number

  assert.equal(getMorningRunTimes(), '09:30, 10:15, 11:00')
})

test('buildPayload creates correct morning run count from select string values', () => {
  const { scheduleSettings, buildPayload } = useScheduleForm()

  scheduleSettings.morning.runCount = '4' as unknown as number

  const payload = buildPayload([])
  const morningRuns = payload.filter((item) => item.name.startsWith('上午运行'))

  assert.equal(morningRuns.length, 4)
  assert.equal(morningRuns.every((item) => item.run_type === 'trade'), true)
})

test('buildPayload marks fixed tasks as analysis', () => {
  const { buildPayload } = useScheduleForm()

  const payload = buildPayload([])
  const fixedRuns = payload.filter((item) => ['盘前分析', '午间复盘', '收盘分析'].includes(item.name))

  assert.equal(fixedRuns.length, 3)
  assert.equal(fixedRuns.every((item) => item.run_type === 'analysis'), true)
})

test('buildPayload preserves disabled session schedules', () => {
  const { scheduleSettings, syncFromSchedules, buildPayload } = useScheduleForm()

  syncFromSchedules([
    {
      id: 11,
      name: '上午运行1号',
      cron_expression: '0 10 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
    {
      id: 12,
      name: '上午运行2号',
      cron_expression: '0 11 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  const payload = buildPayload([
    {
      id: 11,
      name: '上午运行1号',
      cron_expression: '0 10 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
    {
      id: 12,
      name: '上午运行2号',
      cron_expression: '0 11 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  const morningRuns = payload.filter((item) => item.name.startsWith('上午运行'))
  assert.equal(morningRuns.every((item) => item.enabled === false), true)
})

test('buildPayload creates ETF trade schedules without the obsolete fixed cap', () => {
  const { scheduleSettings, buildPayload } = useScheduleForm()

  scheduleSettings.etfMorning.enabled = true
  scheduleSettings.etfMorning.runCount = 3

  const payload = buildPayload([])
  const etfRuns = payload.filter((item) => item.name.startsWith('ETF上午运行'))

  assert.equal(etfRuns.length, 3)
  assert.equal(etfRuns.every((item) => item.run_type === 'trade'), true)
  assert.equal(etfRuns.every((item) => item.task_prompt.includes('买入不得超过可用资金')), true)
  assert.equal(etfRuns.every((item) => item.task_prompt.includes('不设单次或单日固定金额上限')), true)
  assert.equal(etfRuns.every((item) => !item.task_prompt.includes('1000元')), true)
})

test('syncFromSchedules keeps stock and ETF session schedules separate', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 21,
      name: '上午运行1号',
      run_type: 'trade',
      cron_expression: '0 10 * * 1-5',
      task_prompt: 'stock',
      timeout_seconds: 1800,
      enabled: true,
    },
    {
      id: 31,
      name: 'ETF上午运行1号',
      run_type: 'trade',
      cron_expression: '30 10 * * 1-5',
      task_prompt: 'etf',
      timeout_seconds: 1800,
      enabled: false,
    },
    {
      id: 32,
      name: 'ETF上午运行2号',
      run_type: 'trade',
      cron_expression: '0 11 * * 1-5',
      task_prompt: 'etf',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  assert.equal(scheduleSettings.morning.runCount, 1)
  assert.equal(scheduleSettings.morning.prompt, 'stock')
  assert.equal(scheduleSettings.etfMorning.runCount, 2)
  assert.equal(scheduleSettings.etfMorning.prompt, 'etf')
  assert.equal(scheduleSettings.etfMorning.enabled, false)
})

test('syncFromSchedules normalizes pre-market times to supported button options', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 1,
      name: '盘前分析',
      cron_expression: '30 7 * * 1-5',
      task_prompt: 'a',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('pre-market default display time is 08:00', () => {
  const { scheduleSettings } = useScheduleForm()

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('syncFromSchedules migrates legacy pre-market default 07:15 to 08:00', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 9,
      name: '盘前分析',
      cron_expression: '15 7 * * 1-5',
      task_prompt: 'legacy',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('syncFromSchedules normalizes midday times to 12:00/15/30/45 options', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 2,
      name: '午间复盘',
      cron_expression: '45 11 * * 1-5',
      task_prompt: 'b',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.midday.hour, 12)
  assert.equal(scheduleSettings.midday.minute, 0)
})

test('syncFromSchedules normalizes post-market times to supported button options', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 3,
      name: '收盘分析',
      cron_expression: '15 16 * * 1-5',
      task_prompt: 'c',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.postMarket.hour, 16)
  assert.equal(scheduleSettings.postMarket.minute, 0)
})
