/**
 * Карточка донора: откуда последняя цена и «Указать цену», которую человек
 * знает сам (07.10.2026).
 *
 * Проверяется то, ради чего раздел: ручная цена видна как ручная — кто вписал
 * и откуда знает, — а цена из ответа так и названа; вписанное уходит на сервер
 * строкой, как набрано, и карточка берёт ответ сервера целиком; отказ — словами
 * сервера, вписанное остаётся на месте; цену указывают только донору и только
 * с правом, а почему нельзя — сказано на месте.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorFullCard, Me } from '../api/types';
import { CARD } from '../test/donorFixtures';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

const WHO = 'anna@ours.example.test';

/** Цена, вписанная руками, — как её отдаёт сервер. */
const MANUAL: Partial<DonorFullCard> = {
  last_price: '150.00',
  last_price_currency: 'EUR',
  last_price_at: '2026-10-07T10:00:00+00:00',
  last_price_source: 'manual',
  last_price_note: 'прайс агентства',
  last_price_by: WHO,
};

async function openCard(
  card: DonorFullCard,
  routes: Record<string, Answer> = {},
  me: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: me },
    [`GET /api/donors/${card.id}`]: { body: card },
    ...routes,
  });
  renderWith(<AppRoutes />, `/donors/${card.id}`);
  await screen.findByRole('heading', { name: card.host });
  return recorded;
}

function priceCard(): HTMLElement {
  return screen.getByRole('heading', { name: 'Последняя цена' }).closest('.mantine-Card-root')!;
}

/** Текст без неразрывных пробелов разрядов и валюты: «150,00 €». */
function plain(element: HTMLElement): string {
  return (element.textContent ?? '').replace(/[\u00a0\u202f]/g, ' ');
}

describe('карточка донора: откуда цена', () => {
  it('ручная — «Вручную: кто · откуда цена», дата — в строке цены', async () => {
    await openCard({ ...CARD, ...MANUAL });

    expect(plain(priceCard())).toContain('150,00 € · 07.10.2026');
    expect(within(priceCard()).getByText(`Вручную: ${WHO} · прайс агентства`)).toBeVisible();
  });

  it('ручная без заметки — кто вписал, без пустого «·»', async () => {
    await openCard({ ...CARD, ...MANUAL, last_price_note: null });

    expect(within(priceCard()).getByText(`Вручную: ${WHO}`)).toBeVisible();
  });

  it.each([
    ['из ответа', 'reply' as const],
    ['записанная до 07.10.2026, без источника', null],
  ])('%s — «Из ответа донора»', async (_, source) => {
    await openCard({
      ...CARD,
      last_price: '120.00',
      last_price_currency: 'USD',
      last_price_at: '2026-09-20T10:00:00+00:00',
      last_price_source: source,
    });

    expect(within(priceCard()).getByText('Из ответа донора')).toBeVisible();
    expect(within(priceCard()).queryByText(/Вручную/)).toBeNull();
  });

  it('заведённый вручную — так и сказано в шапке, без очереди прогона', async () => {
    await openCard({
      ...CARD,
      ...MANUAL,
      review_by: WHO,
      review_at: '2026-10-07T10:00:00+00:00',
      review_run: null,
      entered_by: WHO,
    });

    expect(screen.getByText(/^Донор: заведён вручную/)).toHaveTextContent(
      `Донор: заведён вручную ${WHO} 07.10.2026.`,
    );
  });
});

describe('карточка донора: «Указать цену»', () => {
  it('донору без цены — «цены нет» и кнопка', async () => {
    await openCard(CARD);

    expect(
      within(priceCard()).getByText('Цены нет — по донору без цены Этап 2 не запускается.'),
    ).toBeVisible();
    expect(within(priceCard()).getByRole('button', { name: 'Указать цену' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('вписанное уходит строкой, как набрано, и карточка — из ответа сервера', async () => {
    const recorded = await openCard(CARD, {
      'POST /api/donors/7/price': { body: { ...CARD, ...MANUAL } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Указать цену' }));
    const save = screen.getByRole('button', { name: 'Записать цену' });
    // Без цены сервер только сказал бы, что её нет.
    expect(save).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '150');
    const currency = screen.getByRole('textbox', { name: 'Валюта' });
    expect(currency).toHaveValue('USD');
    await user.clear(currency);
    await user.type(currency, '€');
    await user.type(screen.getByRole('textbox', { name: 'Откуда цена' }), ' прайс агентства ');
    await user.click(save);

    expect(await within(priceCard()).findByText(`Вручную: ${WHO} · прайс агентства`)).toBeVisible();
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => [call.path, call.body])).toEqual([
      ['/api/donors/7/price', { price: '150', currency: '€', note: 'прайс агентства' }],
    ]);
    expect(await screen.findByText(/^Цена записана: 150,00/)).toBeInTheDocument();
    // Записано — поля свёрнуты.
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Цена' })).toBeNull());
  });

  it('донор, не годный по порогам, цену получает — и сказано, что обхода не будет', async () => {
    await openCard(
      { ...CARD, status: 'unsuitable', reject_reason: 'DR 8 ниже 20' },
      {
        'POST /api/donors/7/price': {
          body: { ...CARD, ...MANUAL, status: 'unsuitable', reject_reason: 'DR 8 ниже 20' },
        },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Указать цену' }));
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '150{Enter}');

    expect(
      await screen.findByText(/По порогам отбора донор не годен — обход Этапа 2 его не возьмёт\.$/),
    ).toBeInTheDocument();
  });

  it('пустая заметка уходит «не сказали», а не пустой строкой', async () => {
    const recorded = await openCard(CARD, {
      'POST /api/donors/7/price': { body: { ...CARD, ...MANUAL, last_price_note: null } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Указать цену' }));
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '150{Enter}');

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        price: '150',
        currency: 'USD',
        note: null,
      }),
    );
  });

  it('отказ сервера — его словами над полями, вписанное на месте', async () => {
    const refusal = '«1,200» — не цена: впишите число больше нуля, например 150 или 150.50.';
    await openCard(CARD, {
      'POST /api/donors/7/price': { status: 400, body: { detail: refusal } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Указать цену' }));
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '1,200');
    await user.click(screen.getByRole('button', { name: 'Записать цену' }));

    expect(await screen.findByText(refusal)).toBeVisible();
    expect(screen.getByText('Цену не записали')).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Цена' })).toHaveValue('1,200');
    // Исправил — отказ уходит: он был про прежнее число.
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '0');
    expect(screen.queryByText(refusal)).toBeNull();
  });

  it('«Отмена» сворачивает поля, ничего не отправив', async () => {
    const recorded = await openCard(CARD);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Указать цену' }));
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '150');
    await user.click(screen.getByRole('button', { name: 'Отмена' }));

    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Цена' })).toBeNull());
    expect(recorded.calls.filter((call) => call.method === 'POST')).toEqual([]);
  });

  it('без права — объяснение на месте, а не кнопка', async () => {
    await openCard({ ...CARD, ...MANUAL }, {}, { ...OPERATOR, permissions: ['view'] });

    expect(
      within(priceCard()).getByText('Указать цену может сотрудник с правом «запускать прогоны».'),
    ).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Указать цену' })).toBeNull();
  });

  it('кандидату цену здесь не указывают: без цены раздела нет вовсе', async () => {
    await openCard({ ...CARD, review: null, review_run: 18 });

    expect(screen.queryByRole('heading', { name: 'Последняя цена' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Указать цену' })).toBeNull();
  });

  it('отклонённому — цена видна, кнопки нет', async () => {
    await openCard({ ...CARD, ...MANUAL, review: 'rejected', review_run: 21 });

    expect(within(priceCard()).getByText(`Вручную: ${WHO} · прайс агентства`)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Указать цену' })).toBeNull();
  });
});
