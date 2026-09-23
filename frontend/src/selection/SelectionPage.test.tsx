/**
 * Экран отбора.
 *
 * Экран сделан ради двух чисел — доли ложно принятых и ложно отсеянных, —
 * и оба считаются по расхождению человека с машиной. Поэтому проверяется
 * не «рисуется ли таблица», а то, ради чего она нарисована: видны ли
 * автор и основание отказа, виден ли отрезанный до Ahrefs домен, уходит
 * ли решение человека на сервер и остаётся ли рядом вердикт машины.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { SelectionCard, SelectionView } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const MACHINE_NONE = {
  intent: null,
  recommendation: null,
  decided_by: null,
  quote: null,
  reason: null,
  source_url: null,
  home_shop: [],
  home_reached: null,
  judged_at: null,
};

const NOBODY = { intent: null, note: null, decided_at: null };

const ACCEPTED = 'GET /api/selection?tab=accepted&limit=50&offset=0';
const REJECTED = 'GET /api/selection?tab=rejected&limit=50&offset=0';

const BRAND: SelectionCard = {
  domain_id: 7,
  host: 'brand.test',
  tab: 'rejected',
  donor_id: 70,
  status: 'suitable',
  reject_reason: null,
  dr: 55,
  org_traffic: 12000,
  machine: {
    ...MACHINE_NONE,
    intent: 'sells_own',
    recommendation: 'reject',
    decided_by: 'rule',
    quote: 'Kaffee online kaufen',
    reason: 'продаёт своё',
    source_url: 'https://brand.test/ratgeber',
    home_shop: ['cart:/warenkorb'],
    home_reached: true,
    judged_at: '2026-09-23T10:00:00+00:00',
  },
  human: NOBODY,
  seller: { answer: null, answered_at: null, price: null, currency: null },
  disagrees: false,
};

const WEAK: SelectionCard = {
  ...BRAND,
  domain_id: 8,
  host: 'weak.test',
  donor_id: 80,
  status: 'unsuitable',
  reject_reason: 'органический трафик 100 ниже 500',
  machine: { ...BRAND.machine, recommendation: 'accept', decided_by: 'model', quote: 'Test 2026' },
};

const CUT: SelectionCard = {
  ...BRAND,
  domain_id: 9,
  host: 'cut.test',
  donor_id: null,
  status: null,
  dr: null,
};

function view(rows: SelectionCard[], extra: Partial<SelectionView> = {}): SelectionView {
  return {
    rows,
    total: rows.length,
    tabs: { accepted: 12, review: 2, rejected: 3 },
    reviewed: 4,
    disagreements: 1,
    layers: { rule: { checked: 2, agreed: 2 }, model: { checked: 2, agreed: 1 } },
    answered: 3,
    answer_layers: { rule: { checked: 1, agreed: 1 }, model: { checked: 2, agreed: 1 } },
    ...extra,
  };
}

async function openScreen(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    [ACCEPTED]: { body: view([BRAND, WEAK, CUT]) },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/selection');
  await screen.findByText('brand.test');
  return recorded;
}

function rowOf(host: string): HTMLElement {
  const cell = screen.getByText(host).closest('tr');
  if (cell === null) throw new Error(`строки ${host} нет`);
  return cell;
}

describe('экран отбора', () => {
  it('у отказа судьи видны автор, цитата и главная', async () => {
    await openScreen();

    const row = within(rowOf('brand.test'));
    expect(row.getByText('не площадка')).toBeInTheDocument();
    expect(row.getByText('правило')).toBeInTheDocument();
    expect(row.getByText('«Kaffee online kaufen»')).toBeInTheDocument();
    expect(row.getByText(/главная: cart:\/warenkorb/)).toBeInTheDocument();
    expect(row.getByRole('link', { name: 'страница' })).toHaveAttribute(
      'href',
      'https://brand.test/ratgeber',
    );
  });

  it('у отказа порогов видны оба числа', async () => {
    await openScreen();

    expect(
      within(rowOf('weak.test')).getByText('органический трафик 100 ниже 500'),
    ).toBeInTheDocument();
  });

  it('отрезанный до Ahrefs домен виден и назван', async () => {
    // Во включённом судье донором он не становится; без этой строки
    // отклонённый исчезал бы без следа.
    await openScreen();

    expect(within(rowOf('cut.test')).getByText('до Ahrefs не дошёл')).toBeInTheDocument();
  });

  it('сводка показывает сходимость по слоям судьи', async () => {
    await openScreen();

    expect(screen.getByText(/Сходится с человеком/)).toHaveTextContent(
      'правило 2 из 2 · модель 1 из 2 · арбитр — не проверяли',
    );
  });

  it('решение человека уходит на сервер типом сайта', async () => {
    const recorded = await openScreen({
      'POST /api/selection/7/decide': {
        body: {
          ...BRAND,
          tab: 'accepted',
          disagrees: true,
          human: { ...NOBODY, intent: 'publisher' },
        },
      },
    });
    const user = userEvent.setup();

    await user.click(within(rowOf('brand.test')).getByRole('button', { name: 'Площадка' }));

    await screen.findByText('brand.test: Площадка');
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ intent: 'publisher', note: null });
  });

  it('повторное нажатие на своё решение его снимает', async () => {
    const decided = { ...BRAND, human: { ...NOBODY, intent: 'sells_own' as const } };
    const recorded = await openScreen({
      [ACCEPTED]: { body: view([decided]) },
      'POST /api/selection/7/decide': { body: BRAND },
    });
    const user = userEvent.setup();

    const pressed = within(rowOf('brand.test')).getByRole('button', { name: 'Продаёт своё' });
    expect(pressed).toHaveAttribute('aria-pressed', 'true');
    await user.click(pressed);

    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ intent: null, note: null });
  });

  it('вкладка уходит в запрос с первой страницы', async () => {
    const recorded = await openScreen({ [REJECTED]: { body: view([CUT]) } });
    const user = userEvent.setup();

    await user.click(screen.getByText('Отклонены — 3'));

    await screen.findByText('cut.test');
    expect(recorded.calls.some((call: Call) => call.path.endsWith(REJECTED.slice(4)))).toBe(true);
  });

  it('отказ сервера виден человеку', async () => {
    await openScreen({
      'POST /api/selection/7/decide': { status: 404, body: { detail: 'Домена №7 в отборе нет' } },
    });
    const user = userEvent.setup();

    await user.click(within(rowOf('brand.test')).getByRole('button', { name: 'Площадка' }));

    expect(await screen.findByText(/Не записали/)).toBeInTheDocument();
  });

  it('оператор решает — это его работа', async () => {
    await openScreen({}, OPERATOR);

    expect(
      within(rowOf('brand.test')).getByRole('button', { name: 'Площадка' }),
    ).toBeInTheDocument();
  });

  it('без права решения видно только отбор', async () => {
    await openScreen({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(screen.queryByRole('button', { name: 'Площадка' })).not.toBeInTheDocument();
    expect(within(rowOf('brand.test')).getByText('не смотрел')).toBeInTheDocument();
  });

  it('пустая вкладка объясняет, что на ней было бы', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      [ACCEPTED]: { body: view([], { tabs: { accepted: 0, review: 0, rejected: 0 } }) },
    });
    renderWith(<AppRoutes />, '/selection');

    expect(await screen.findByText('Принятых под фильтр нет.')).toBeInTheDocument();
  });

  it('ответ донора виден в строке и в сходимости судьи', async () => {
    // Для гест-постинга ответ сайта — правда первого сорта: «не продаёт»
    // должно быть видно рядом с вердиктом судьи, а не только в переписке.
    const declined = {
      ...BRAND,
      seller: {
        answer: 'declines' as const,
        answered_at: '2026-09-23T10:00:00+00:00',
        price: null,
        currency: null,
      },
    };
    const sold = {
      ...WEAK,
      seller: { answer: 'sells' as const, answered_at: null, price: '250.00', currency: 'EUR' },
    };
    await openScreen({ [ACCEPTED]: { body: view([declined, sold]) } });

    expect(within(rowOf('brand.test')).getByText('не продаёт')).toBeInTheDocument();
    expect(within(rowOf('weak.test')).getByText('продаёт')).toBeInTheDocument();
    expect(within(rowOf('weak.test')).getByText(/250.00 EUR/)).toBeInTheDocument();
    expect(screen.getByText(/Судья угадал по ответам доноров/)).toHaveTextContent(
      'правило 1 из 1 · модель 1 из 2 · арбитр — ответов нет',
    );
  });
});
