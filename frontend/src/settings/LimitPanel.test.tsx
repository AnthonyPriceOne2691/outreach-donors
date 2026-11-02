/**
 * Панель лимитов на «Расходе»: третья вкладка только с правом «менять потолки расхода», без права нет
 * ни вкладки, ни запроса. Строки — по задачам, чего нет — прочерк, а не ноль; у строки только для
 * чтения кнопки нет, причина — в текущем ограничении. Окно: «Подтвердить» — только после просчёта
 * именно этого значения, смена числа сбрасывает просчёт, отказ остаётся словами, а смена уходит с тем,
 * что человек видел. Числа — выдуманные.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { LimitQuote, LimitRow } from '../api/limitPanel';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR } from '../test/fixtures';
import { openLimitPanel, openUsage } from '../test/usageFixtures';
import type { Answer } from '../test/server';

/** Числа экрана — с неразрывным пробелом разрядов (`format.ts`); в тексте теста — обычный. */
function spaced(words: string): RegExp {
  return new RegExp(words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));
}

const MODEL: LimitRow = {
  key: 'sales.llm',
  task: 'Продажи',
  service: 'Модель',
  action: 'Все вызовы продаж',
  scope: 'все вызовы продаж',
  unit: 'токенов',
  per_session: null,
  session_hours: null,
  per_day: 10000000,
  per_month: null,
  used_day: 300,
  used_month: 4500,
  spent_month_usd: null,
  cost_usd: '232.50',
  cost_note: 'Оценка снизу: 30 дней по смешанной цене',
  now: '20 % общего, свободно 30 %',
  source: 'поле: вкладка «Лимиты»',
  editable: ['per_day'],
};

const BOX: LimitRow = {
  ...MODEL,
  key: 'sales.mail.followup',
  service: 'Почта',
  action: 'Добивки с ящика',
  scope: 'один ящик',
  unit: 'писем',
  per_session: 10,
  session_hours: 1,
  per_day: null,
  used_day: null,
  used_month: null,
  cost_usd: null,
  cost_note: 'Платят тарифом почтовой платформы',
  now: 'Не меняется: это репутация доменов',
  source: '.env OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR',
  editable: [],
};

const SERP: LimitRow = {
  ...BOX,
  key: 'outreach.donors.serp.buy',
  task: 'Аутрич',
  service: 'Выдача',
  action: 'Покупка выдачи в прогоне',
  scope: 'один прогон',
  unit: 'ключей',
  per_session: 100,
  session_hours: null,
  spent_month_usd: '12.40',
  now: 'Меняется настройкой сервера',
  source: '.env SERP_MAX_KEYWORDS_PER_RUN',
};

const ROWS = [MODEL, BOX, SERP];
const PANEL_URL = 'GET /api/usage/limits';
const QUOTE_URL = 'POST /api/usage/limits/quote';
const CHANGE_URL = 'POST /api/usage/limits/change';

const QUOTED: LimitQuote = {
  cost_usd: '348.75',
  words: '«Продажи»: 30 % общего — 15 000 000 токенов в сутки, было 20 %.',
  refusal: null,
  accepted: 15000000,
};

async function openPanel(routes: Record<string, Answer | (() => Answer)> = {}) {
  const recorded = await openUsage(undefined, {
    routes: { [PANEL_URL]: { body: { rows: ROWS } }, ...routes },
  });
  await userEvent.click(screen.getByRole('radio', { name: 'Панель лимитов' }));
  await screen.findByRole('table', { name: 'Панель лимитов' });
  return recorded;
}

async function openDialog() {
  await userEvent.click(
    screen.getByRole('button', { name: 'Изменить: Модель, Все вызовы продаж' }),
  );
  const dialog = await screen.findByRole('dialog');
  return {
    dialog,
    field: within(dialog).getByRole('textbox'),
    quote: within(dialog).getByRole('button', { name: 'Просчитать' }),
    confirm: within(dialog).getByRole('button', { name: 'Подтвердить' }),
  };
}

const NO_SPEND: Me = { ...ADMIN, permissions: ADMIN.permissions.filter((one) => one !== 'spend') };

describe('панель лимитов: вкладка', () => {
  it('у администратора третья вкладка, и панель спрашивается только на ней', async () => {
    const recorded = await openUsage(undefined, {
      routes: { [PANEL_URL]: { body: { rows: ROWS } } },
    });

    expect(screen.getByRole('radio', { name: 'Панель лимитов' })).toBeInTheDocument();
    expect(recorded.calls.map((call) => call.path)).not.toContain('/api/usage/limits');

    await userEvent.click(screen.getByRole('radio', { name: 'Панель лимитов' }));

    expect(await screen.findByRole('table', { name: 'Панель лимитов' })).toBeInTheDocument();
    expect(recorded.calls.map((call) => call.path)).toContain('/api/usage/limits');
  });

  it('вкладка живёт в адресе', async () => {
    await openLimitPanel({ routes: { [PANEL_URL]: { body: { rows: ROWS } } } });

    expect(screen.getByRole('radio', { name: 'Панель лимитов' })).toBeChecked();
  });

  it.each([
    ['оператор', OPERATOR],
    ['администратор без права', NO_SPEND],
  ])('%s не видит вкладки, и запроса к панели нет даже по адресу', async (_, me) => {
    const recorded = await openUsage(undefined, { me, path: '/usage?view=panel' });

    expect(screen.queryByRole('radio', { name: 'Панель лимитов' })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Лимиты' })).toBeChecked();
    expect(recorded.calls.map((call) => call.path)).not.toContain('/api/usage/limits');
  });

  it('отказ сервера — словами', async () => {
    await openUsage(undefined, {
      routes: { [PANEL_URL]: { status: 403, body: { detail: 'Нет права' } } },
    });

    await userEvent.click(screen.getByRole('radio', { name: 'Панель лимитов' }));

    expect(await screen.findByText('Панель лимитов не загрузилась')).toBeInTheDocument();
    expect(screen.getByText('Нет права')).toBeInTheDocument();
  });
});

describe('панель лимитов: таблица', () => {
  it('строки — под своими задачами', async () => {
    await openPanel();
    const table = screen.getByRole('table', { name: 'Панель лимитов' });
    const lines = within(table)
      .getAllByRole('row')
      .map((row) => row.textContent ?? '');

    expect(lines[1]).toBe('Продажи 2');
    expect(lines[2]).toContain('Модель, токенов');
    expect(lines[3]).toContain('Почта, писем');
    expect(lines[4]).toBe('Аутрич 1');
    expect(lines[5]).toContain('Выдача, ключей');
  });

  it('числа и слова сервера: сессия, день, выбрано, стоимость, откуда', async () => {
    await openPanel();
    const model = screen.getByRole('row', { name: /Все вызовы продаж/ });

    expect(within(model).getByText(spaced('10 000 000'))).toBeInTheDocument();
    expect(within(model).getByText(spaced('день 300'))).toBeInTheDocument();
    expect(within(model).getByText(spaced('месяц 4 500'))).toBeInTheDocument();
    expect(within(model).getByText(/232,50/)).toBeInTheDocument();
    expect(within(model).getByText('20 % общего, свободно 30 %')).toBeInTheDocument();
    expect(within(model).getByText('поле: вкладка «Лимиты»')).toBeInTheDocument();

    const box = screen.getByRole('row', { name: /Добивки с ящика/ });
    expect(within(box).getByText('10 за 1 ч')).toBeInTheDocument();
  });

  it('чего нет — прочерк, а не ноль; деньги счётчика — в выбранном', async () => {
    await openPanel();
    const serp = screen.getByRole('row', { name: /Покупка выдачи/ });

    expect(within(serp).getByText('100')).toBeInTheDocument();
    expect(within(serp).getByText(/месяц 12,40/)).toBeInTheDocument();
    expect(within(serp).getAllByText('—').length).toBeGreaterThanOrEqual(3);
    expect(within(serp).queryByText('0')).not.toBeInTheDocument();
  });

  it('у строки только для чтения кнопки нет, а причина — в ограничении', async () => {
    await openPanel();
    const box = screen.getByRole('row', { name: /Добивки с ящика/ });

    expect(within(box).queryByRole('button', { name: /^Изменить/ })).not.toBeInTheDocument();
    expect(within(box).getByText('Не меняется: это репутация доменов')).toBeInTheDocument();
    expect(within(box).getByText('.env OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR')).toBeInTheDocument();
  });
});

describe('панель лимитов: окно', () => {
  it('открывается на действующем, «Подтвердить» заперта до просчёта', async () => {
    await openPanel();
    const { dialog, field, confirm } = await openDialog();

    expect(field).toHaveValue('10 000 000');
    expect(confirm).toBeDisabled();
    expect(
      within(dialog).getByText('Подтвердить можно после просчёта этого значения'),
    ).toBeInTheDocument();
  });

  it('просчёт называет стоимость и слова, ничего не меняя, и открывает «Подтвердить»', async () => {
    const recorded = await openPanel({ [QUOTE_URL]: { body: QUOTED } });
    const user = userEvent.setup();
    const { dialog, field, quote, confirm } = await openDialog();

    await user.clear(field);
    await user.type(field, '15000000');
    await user.click(quote);

    expect(
      await within(dialog).findByText(/Стоимость нового лимита: .*348,75/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(QUOTED.words)).toBeInTheDocument();
    expect(confirm).toBeEnabled();
    expect(recorded.calls.find((call) => call.path.endsWith('/quote'))?.body).toEqual({
      key: 'sales.llm',
      field: 'per_day',
      value: 15000000,
    });
    expect(recorded.calls.map((call) => call.path)).not.toContain('/api/usage/limits/change');
  });

  it('смена числа сбрасывает просчёт и запирает «Подтвердить»', async () => {
    await openPanel({ [QUOTE_URL]: { body: QUOTED } });
    const user = userEvent.setup();
    const { dialog, field, quote, confirm } = await openDialog();

    await user.clear(field);
    await user.type(field, '15000000');
    await user.click(quote);
    await within(dialog).findByText(QUOTED.words);

    await user.type(field, '1');

    expect(within(dialog).queryByText(QUOTED.words)).not.toBeInTheDocument();
    expect(confirm).toBeDisabled();
  });

  it('стоимость без денег — словами, а не нулём', async () => {
    await openPanel({ [QUOTE_URL]: { body: { ...QUOTED, cost_usd: null } } });
    const { dialog, quote } = await openDialog();

    await userEvent.click(quote);

    expect(
      await within(dialog).findByText('Стоимость нового лимита: деньгами не считается'),
    ).toBeInTheDocument();
  });

  it('отказ просчёта — словами, «Подтвердить» заперта, вписанное остаётся', async () => {
    const refusal = 'Свободно 30 %: лимит не выше 25 000 000 токенов в сутки';
    await openPanel({ [QUOTE_URL]: { body: { ...QUOTED, refusal, accepted: 0 } } });
    const user = userEvent.setup();
    const { dialog, field, quote, confirm } = await openDialog();

    await user.clear(field);
    await user.type(field, '49000000');
    await user.click(quote);

    expect(await within(dialog).findByText(refusal)).toBeInTheDocument();
    expect(confirm).toBeDisabled();
    expect(field).toHaveValue('49 000 000');
  });

  it('просчитать пустое нельзя', async () => {
    await openPanel();
    const user = userEvent.setup();
    const { dialog, field, quote } = await openDialog();

    await user.clear(field);

    expect(quote).toBeDisabled();
    expect(within(dialog).getByText('Впишите число')).toBeInTheDocument();
  });

  it('Enter в поле просчитывает, а не подтверждает', async () => {
    const recorded = await openPanel({ [QUOTE_URL]: { body: QUOTED } });
    const user = userEvent.setup();
    const { dialog, field } = await openDialog();

    await user.type(field, '{Enter}');

    await within(dialog).findByText(QUOTED.words);
    expect(recorded.calls.map((call) => call.path)).not.toContain('/api/usage/limits/change');
  });

  it('подтверждение уходит с тем, что видел человек, а панель перечитывается', async () => {
    let reads = 0;
    const recorded = await openPanel({
      [PANEL_URL]: () => ({ body: { rows: reads++ === 0 ? ROWS : [MODEL] } }),
      [QUOTE_URL]: { body: QUOTED },
      [CHANGE_URL]: { body: QUOTED },
    });
    const user = userEvent.setup();
    const { field, quote, confirm, dialog } = await openDialog();

    await user.clear(field);
    await user.type(field, '15000000');
    await user.click(quote);
    await within(dialog).findByText(QUOTED.words);
    await user.click(confirm);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.path.endsWith('/change'))?.body).toEqual({
      key: 'sales.llm',
      field: 'per_day',
      value: 15000000,
      quoted: 15000000,
    });
    await waitFor(() => expect(reads).toBeGreaterThan(1));
    expect(await screen.findByText(spaced('Все вызовы продаж: 15 000 000 токенов'))).toBeVisible();
  });

  it('отказ смены — над полем, окно остаётся, вписанное тоже', async () => {
    const stale =
      'Просчёт устарел: теперь подтвердится 12 000 000, а не 12 500 000 — просчитайте заново';
    await openPanel({
      [QUOTE_URL]: { body: QUOTED },
      [CHANGE_URL]: { status: 422, body: { detail: stale } },
    });
    const user = userEvent.setup();
    const { dialog, field, quote, confirm } = await openDialog();

    await user.clear(field);
    await user.type(field, '15000000');
    await user.click(quote);
    await within(dialog).findByText(QUOTED.words);
    await user.click(confirm);

    expect(await within(dialog).findByText(stale)).toBeInTheDocument();
    expect(within(dialog).getByText('Лимит не сменили')).toBeInTheDocument();
    expect(field).toHaveValue('15 000 000');
  });
});
