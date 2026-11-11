/**
 * «Судья сам, каждую ночь»: строки над очередью и переключатель полосы в окне применения.
 * Проверяется то, чем экран может соврать о работе автомата: показать выключенную полосу
 * включённой; выдать ноль выключенной полосы за совет судьи; спрятать, почему шаг встал; включить
 * автоприменение без вопроса о последствиях; заставить подтверждать выключение.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { JudgeNight, JudgeNightSummary } from '../api/advertisersJudgeNight';
import { OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import { JudgeApplyButton } from './JudgeApply';
import { bandsWords, enableWarning, JudgeNightLines, nightWords } from './JudgeNight';

const URL = '/api/advertisers/judge-night';
const APPLY = '/api/advertisers/judge-apply';

const OFF: JudgeNight = {
  bands: [
    { band: 'skip', enabled: false, set_by: null, set_at: null },
    { band: 'write', enabled: false, set_by: null, set_at: null },
  ],
  last: null,
  ran_today: false,
  awaiting_promotion: 0,
  cap: 300,
};

const SKIP_ON: JudgeNight = {
  ...OFF,
  bands: [
    { band: 'skip', enabled: true, set_by: 'оператор@site.com', set_at: '2026-11-04T10:00:00Z' },
    { band: 'write', enabled: false, set_by: null, set_at: null },
  ],
};

const LAST: JudgeNightSummary = {
  night: '2026-11-05',
  status: 'done',
  judged: 34,
  applied: { skip: 20, write: 9 },
  unclear: 5,
  going: null,
  why: null,
  lines: ['рассужены: 34', 'токенов: 140 000', '«не пишем»: применено 20', '«пишем»: применено 9'],
};

const STANDING = {
  waiting: 10,
  skip: 3,
  write: 5,
  waiting_dr: 0,
  unclear: 2,
  stale: 0,
  without: 0,
};

const PLAN = {
  band: 'skip',
  pairs: 3,
  donors: 2,
  rules: [{ rule: 'free_directory', words: 'бесплатная карточка каталога', pairs: 3 }],
  examples: [],
  standing: STANDING,
  takes_off: 0,
  launch: null,
  portion: 200,
};

function posts(calls: Call[]): unknown[] {
  return calls.filter((call) => call.method === 'POST').map((call) => call.body);
}

function showLines(night: JudgeNight) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({ 'GET /api/auth/me': { body: OPERATOR }, [`GET ${URL}`]: { body: night } });
  renderWith(<JudgeNightLines />);
}

async function openDialog(night: JudgeNight, routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: OPERATOR },
    [`GET ${APPLY}`]: { body: STANDING },
    [`GET ${APPLY}/plan?band=skip`]: { body: PLAN },
    [`GET ${APPLY}/plan?band=write`]: { body: { ...PLAN, band: 'write' } },
    [`GET ${APPLY}/batches`]: { body: [] },
    [`GET ${URL}`]: { body: night },
    ...(routes as Record<string, never>),
  });
  renderWith(<JudgeApplyButton />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Применить советы…' }));
  const dialog = await screen.findByRole('dialog', { name: 'Применить советы судьи' });
  const block = await within(dialog).findByRole('group', {
    name: 'Автоприменение полосы каждую ночь',
  });
  return { recorded, user, dialog, block };
}

describe('строки судьи над очередью', () => {
  it('полосы — как стоят; шага ещё не было — второй строки нет', async () => {
    showLines(OFF);

    expect(
      await screen.findByText('Судья сам, каждую ночь: не пишем — выкл · пишем — выкл'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Ночью /)).toBeNull();
    expect(screen.queryByText(/ждут перевода/)).toBeNull();
  });

  it('итог ночи — одной строкой, строки разбора и токены — в «i»', async () => {
    showLines({ ...SKIP_ON, last: LAST, awaiting_promotion: 9 });
    const user = userEvent.setup();

    expect(
      await screen.findByText('Судья сам, каждую ночь: не пишем — вкл · пишем — выкл'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Ночью 05.11: разобрано 34 · не пишем 20 · пишем 9 · не понять 5'),
    ).toBeInTheDocument();
    expect(screen.queryByText('токенов: 140 000')).toBeNull();
    // Что остаётся человеку после «пишем»: ночь никого не переводит.
    expect(screen.getByRole('button', { name: 'К письму ждут перевода: 9' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Итог ночи целиком' }));

    const report = await screen.findByRole('dialog', { name: 'Итог ночи целиком' });
    expect(within(report).getByText('токенов: 140 000')).toBeInTheDocument();
    expect(within(report).getByText('«не пишем»: применено 20')).toBeInTheDocument();
  });

  it('выключенной той ночью полосы в строке нет — её ноль не выдан за совет судьи', () => {
    expect(nightWords({ ...LAST, applied: { skip: 20 } })).toMatch(
      /^Ночью 05\.11: разобрано\s34 · не\sпишем\s20 · не\sпонять\s5$/,
    );
  });

  it('шаг идёт, встал или не дошёл до конца — словами в той же строке', () => {
    expect(nightWords({ ...LAST, status: 'judging', going: 'идёт разбор спорных' })).toBe(
      'Ночью 05.11: идёт разбор спорных',
    );
    expect(nightWords({ ...LAST, why: 'разбор встал: потолок судьи выбран' })).toMatch(
      / · разбор встал раньше конца$/,
    );
    expect(nightWords({ ...LAST, status: 'failed', why: 'шаг упал: сбой' })).toBe(
      'Ночью 05.11: шаг не дошёл до конца',
    );
    expect(bandsWords(SKIP_ON.bands)).toBe('не пишем — вкл · пишем — выкл');
  });
});

describe('автоприменение в окне', () => {
  it('включение — с вопросом о последствиях, под планом той же полосы', async () => {
    const { recorded, user, block } = await openDialog(OFF, {
      [`POST ${URL}/bands`]: { body: SKIP_ON },
    });

    await user.click(await within(block).findByRole('button', { name: 'Применять каждую ночь…' }));

    // Первое нажатие ничего не включает: вопрос называет потолок ночи и что останется человеку.
    const question = await screen.findByText(/Судья будет ставить «не пишем» сам каждую ночь/);
    expect(question).toHaveTextContent('не больше 300 пар за ночь');
    expect(question).toHaveTextContent('сняло бы рекламодателя, останутся человеку');
    expect(question).toHaveTextContent('Сегодняшний шаг ещё не шёл');
    expect(posts(recorded.calls)).toEqual([]);

    await user.click(await screen.findByRole('button', { name: 'Включить' }));

    expect(await within(block).findByText(/Судья ставит «не пишем» сам/)).toBeInTheDocument();
    expect(posts(recorded.calls)).toEqual([{ band: 'skip', enabled: true }]);
    expect(within(block).getByText(/включил оператор@site\.com/)).toBeInTheDocument();
  });

  it('выключение — одним нажатием', async () => {
    const { recorded, user, block } = await openDialog(SKIP_ON, {
      [`POST ${URL}/bands`]: { body: OFF },
    });

    await user.click(await within(block).findByRole('button', { name: 'Выключить' }));

    expect(
      await within(block).findByRole('button', { name: 'Применять каждую ночь…' }),
    ).toBeInTheDocument();
    expect(posts(recorded.calls)).toEqual([{ band: 'skip', enabled: false }]);
  });

  it('полосы включаются по одной: у «пишем» — свой переключатель и «ждут перевода»', async () => {
    const { user, dialog, block } = await openDialog({ ...SKIP_ON, awaiting_promotion: 4 });

    expect(await within(block).findByRole('button', { name: 'Выключить' })).toBeInTheDocument();

    await user.click(within(dialog).getByRole('radio', { name: /^Пишем · / }));

    expect(
      await within(block).findByRole('button', { name: 'Применять каждую ночь…' }),
    ).toBeInTheDocument();
    expect(within(block).getByText(/К письму ждут перевода: 4/)).toBeInTheDocument();
  });

  it('отказ сервера — словами у переключателя', async () => {
    const { user, block } = await openDialog(SKIP_ON, {
      [`POST ${URL}/bands`]: {
        status: 403,
        body: { detail: 'Действие «запускать прогоны» не выдано этой учётке' },
      },
    });

    await user.click(await within(block).findByRole('button', { name: 'Выключить' }));

    const alert = await within(block).findByRole('alert');
    expect(alert).toHaveTextContent('Полоса не переключена');
    expect(alert).toHaveTextContent('не выдано этой учётке');
  });

  it('вопрос у «пишем» говорит про DR и перевод, а после шага дня — про ближайшую ночь', () => {
    const words = enableWarning('write', { ...OFF, ran_today: true });

    expect(words).toContain('Только парам с известным DR');
    expect(words).toContain('после «Перевести» — ночь этого не делает');
    expect(words).toContain('Первый раз — ближайшей ночью.');
  });
});
