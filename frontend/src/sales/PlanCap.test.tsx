/**
 * Окно «Потолок плана, $» у строки плана (`PlanCap`). Ответы сервера — записанные (`serve()`); имена
 * и деньги выдуманы.
 *
 * Проверяется: потолок уходит числом, а в поле — как набрано: точка и запятая не стираются на ходу
 * (урок 19.11.2026); удалось — уведомление и закрытое окно; тот же потолок — окно закрыто без
 * запроса; пустое поле — «Сохранить» закрыт и слова у поля после ухода из него; отказ сервера (ниже израсходованного) — плашкой словами
 * сервера, набранное на месте; каркас — заголовок короткий, имя плана строкой под ним, фокус — на
 * поле. Израсходованное под полем — словами отказа сервера и с суммой, от которой можно
 * (`capFloorWords`, стенд 20.11: «1,73 $» под полем и отказ потолку 1,73). Кнопка «Потолок» у строки
 * плана открывает окно — `PlanCell.test.tsx`.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';
import type { PlanLive, PlanRow } from './planApi';
import { PlanCap } from './PlanCap';
import { capFloorWords } from './PlanNumberField';

const ROUTE = 'PATCH /api/sales/plans/7/cap';

const ROW: PlanRow = {
  id: 1,
  hypothesis_id: 11,
  name: 'Логистика — пример',
  position: 1,
  sheet: 'Пилот 1',
  markets: ['kz'],
  language: 'ru',
  target: 5000,
  max_pages: 2,
  cap_usd: 50,
  stage: 'collecting',
  found: 1240,
  spent_usd: 12.4,
  keywords: 3,
  bought: 3,
  wave_page: 2,
  wave_bought: 1,
  reserved_usd: 0,
  problems: [],
};

const PLAN: PlanLive = {
  id: 7,
  name: 'План недели',
  mode: 'economy',
  target_default: null,
  cap_usd: 100,
  status: 'paused',
  problem: 'потолок плана 100,00 $: израсходовано 99,60 $, следующий прогон ≈ 0,90 $',
  created_by: 'admin@ours.example.test',
  created_at: '2026-11-16T12:00:00+00:00',
  hypotheses: [ROW, { ...ROW, id: 2, hypothesis_id: 12, position: 2, spent_usd: 7.6 }],
};

function setUp(routes: Record<string, Answer> = {}): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  return serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/plans': { body: { plans: [] } },
    ...routes,
  });
}

function sent(recorded: Recorded) {
  return recorded.calls
    .filter((call) => call.path === '/api/sales/plans/7/cap' && call.method === 'PATCH')
    .map((call) => call.body);
}

describe('«Потолок плана»', () => {
  it('каркас: заголовок короткий, имя плана — строкой под ним; израсходованное названо; фокус — на поле', async () => {
    setUp();
    renderWith(<PlanCap plan={PLAN} onClose={vi.fn()} />);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAccessibleName(`Потолок плана «${PLAN.name}»`);
    const heading = within(dialog).getByRole('heading', { level: 2 });
    expect(heading.firstChild?.textContent).toBe('Потолок плана');
    expect(screen.getByText(/Израсходовано: 20,00\s\$/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Потолок плана, $')).toHaveFocus());
  });

  it('пустой или нулевой потолок — словами у поля после ухода из него, «Сохранить» закрыт', async () => {
    setUp();
    renderWith(<PlanCap plan={PLAN} onClose={vi.fn()} />);
    const user = userEvent.setup();
    const cap = screen.getByLabelText('Потолок плана, $');

    await user.clear(cap);
    // На ходу слов нет: «0» перед «0,5» не мигает отказом.
    expect(screen.queryByText('Потолок — в долларах, больше нуля')).not.toBeInTheDocument();
    await user.tab();
    expect(screen.getByText('Потолок — в долларах, больше нуля')).toBeVisible();
    expect(cap).toBeInvalid();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    await user.type(cap, '0');
    expect(screen.getByText('Потолок — в долларах, больше нуля')).toBeVisible();
    await user.type(cap, ',5');
    expect(screen.queryByText('Потолок — в долларах, больше нуля')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeEnabled();
  });

  it('поднятый потолок уходит числом, в поле — как набрано; окно закрывается', async () => {
    const onClose = vi.fn();
    const recorded = setUp({
      [ROUTE]: { body: { ...PLAN, cap_usd: 150.5, status: 'running', problem: null } },
    });
    renderWith(<PlanCap plan={PLAN} onClose={onClose} />);
    const user = userEvent.setup();
    const cap = screen.getByLabelText('Потолок плана, $');
    expect(cap).toHaveValue('100');

    await user.clear(cap);
    await user.type(cap, '1.0');
    expect(cap).toHaveValue('1.0');
    await user.clear(cap);
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    await user.type(cap, '150,50');
    expect(cap).toHaveValue('150.50');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sent(recorded)).toEqual([{ cap_usd: 150.5 }]);
  });

  it('тот же потолок — окно закрыто, запроса нет', async () => {
    const onClose = vi.fn();
    const recorded = setUp();
    renderWith(<PlanCap plan={PLAN} onClose={onClose} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(onClose).toHaveBeenCalled();
    expect(sent(recorded)).toEqual([]);
  });

  it('ниже израсходованного — отказ сервера плашкой словами, набранное на месте', async () => {
    const words =
      'потолок плана «План недели» не может быть ниже израсходованного: израсходовано 20,00 $ — ' +
      'задайте от 20,00 $';
    setUp({ [ROUTE]: { status: 409, body: { detail: words } } });
    renderWith(<PlanCap plan={PLAN} onClose={vi.fn()} />);
    const user = userEvent.setup();
    const cap = screen.getByLabelText('Потолок плана, $');

    await user.clear(cap);
    await user.type(cap, '19.5');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(`Потолок не сохранён: ${words}`);
    expect(cap).toHaveValue('19.5');
  });

  it('расход с долей цента — под полем «больше» и сумма, от которой можно, как в отказе сервера', () => {
    setUp();
    const rows = [
      { ...ROW, spent_usd: 1.0312 },
      { ...ROW, id: 2, hypothesis_id: 12, position: 2, spent_usd: 0.7 },
    ];
    renderWith(<PlanCap plan={{ ...PLAN, hypotheses: rows }} onClose={vi.fn()} />);

    expect(
      screen.getByText(/^Израсходовано: больше 1,73\s\$ — потолок от 1,74\s\$\. Поднятый потолок/),
    ).toBeInTheDocument();
  });
});

describe('capFloorWords — израсходованное под полем потолка', () => {
  const NBSP = '\u00a0';

  it.each([
    ['целые центы', 12.4, `Израсходовано: 12,40${NBSP}$ — потолок от 12,40${NBSP}$`],
    ['доля цента', 1.7312, `Израсходовано: больше 1,73${NBSP}$ — потолок от 1,74${NBSP}$`],
    // Округлённое «1,74 $» здесь не ошибка, но отказ сервера говорит «больше 1,73 $».
    [
      'доля цента у верхнего края',
      1.7362,
      `Израсходовано: больше 1,73${NBSP}$ — потолок от 1,74${NBSP}$`,
    ],
    ['меньше цента', 0.0024, `Израсходовано: меньше 0,01${NBSP}$ — потолок от 0,01${NBSP}$`],
    ['ничего', 0, `Израсходовано: 0,00${NBSP}$`],
    // Сумма гипотез плана в двоичных дробях: 0,1 + 0,2 = 0,30000000000000004 — целые центы.
    ['хвост суммы дробей', 0.1 + 0.2, `Израсходовано: 0,30${NBSP}$ — потолок от 0,30${NBSP}$`],
  ])('%s: %s', (_, spent, words) => {
    expect(capFloorWords(spent)).toBe(words);
  });
});
