/**
 * «Перезапросить цену» на карточке донора (12.10.2026).
 *
 * Проверяется то, ради чего раздел: он есть у донора с ценой и не спрашивает сервер,
 * пока окно не открыли; в окне — куда уйдёт письмо и его текст целиком, или почему
 * перезапрос сейчас не уйдёт, словами сервера; отправка — номером донора, итог — в окне;
 * без права писать наружу — посмотреть можно, отправить нельзя; окно закрыли, пока письмо
 * уходит, — открыли снова, и кнопка заперта до итога.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { RecheckCard, RecheckResult } from '../api/priceRecheck';
import type { DonorFullCard, Me } from '../api/types';
import { CARD } from '../test/donorFixtures';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

const PRICED: DonorFullCard = {
  ...CARD,
  last_price: '90.00',
  last_price_currency: 'USD',
  last_price_at: '2026-03-20T10:00:00+00:00',
  last_price_source: 'reply',
};

const BODY =
  "Hi,\n\nWe're planning a new round of sponsored articles, and good.example.test is on our list.\n\nBest regards,\nAnna Ro";

const RECHECK: RecheckCard = {
  donor_id: PRICED.id,
  host: PRICED.host,
  price: 90,
  currency: 'USD',
  source: 'reply',
  priced_at: '2026-03-20T10:00:00+00:00',
  asked_at: null,
  refusal: null,
  way: 'thread',
  thread_id: 12,
  email: 'boss@good.example.test',
  mailbox: 'anna@mail.example.test',
  subject: 'Re: Guest article on good.example.test',
  body: BODY,
};

/** Перезапрос ушёл ответом в переписку №12. */
const ASKED: RecheckResult = {
  sent: [
    {
      donor_id: PRICED.id,
      host: PRICED.host,
      message_id: 40,
      thread_id: 12,
      way: 'thread',
      sender_email: 'anna@mail.example.test',
      real: true,
    },
  ],
  refused: [],
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

async function dialog(button = 'Перезапросить цену'): Promise<HTMLElement> {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: button }));
  const window = await screen.findByRole('dialog', { name: `Перезапрос цены · ${PRICED.host}` });
  // Окно открывается переходом (`opacity` от нуля): текст в нём бывает уже на месте,
  // а видимым он становится, когда переход кончился, — иначе «виден ли» решала скорость.
  await waitFor(() => expect(window).toBeVisible());
  return window;
}

describe('карточка донора: перезапрос цены', () => {
  it('у донора без цены раздела нет', async () => {
    await openCard(CARD);

    expect(screen.queryByRole('heading', { name: 'Перезапрос цены' })).toBeNull();
  });

  it('сервер спрашивают, когда окно открыли, — и показывают письмо целиком', async () => {
    const recorded = await openCard(PRICED, {
      [`GET /api/price-rechecks/${PRICED.id}`]: { body: RECHECK },
    });

    expect(screen.getByRole('heading', { name: 'Перезапрос цены' })).toBeVisible();
    expect(recorded.calls.some((call) => call.path.startsWith('/api/price-rechecks'))).toBe(false);
    const window = await dialog();

    expect(await within(window).findByText(RECHECK.subject!)).toBeVisible();
    expect(within(window).getByText(/We're planning a new round/)).toBeVisible();
    expect(within(window).getByRole('link', { name: 'переписку №12' })).toHaveAttribute(
      'href',
      '/threads/12',
    );
    expect(window).toHaveTextContent('на boss@good.example.test, с ящика anna@mail.example.test');
  });

  it('отправка — номером донора; итог — в том же окне', async () => {
    const recorded = await openCard(PRICED, {
      [`GET /api/price-rechecks/${PRICED.id}`]: { body: RECHECK },
      'POST /api/price-rechecks': { body: ASKED },
    });
    const user = userEvent.setup();
    const window = await dialog();

    await user.click(await within(window).findByRole('button', { name: 'Отправить перезапрос' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        donor_ids: [PRICED.id],
      }),
    );
    expect(
      await within(window).findByText(`Перезапрос цены ушёл: ${PRICED.host}.`),
    ).toBeInTheDocument();
  });

  it('нельзя — словами сервера, и кнопки отправки нет', async () => {
    const refusal =
      'Цена good.example.test свежая: названа 07.10.2026, перезапрос понадобится с 06.03.2027, когда ей исполнится 150 дней.';
    await openCard(PRICED, {
      [`GET /api/price-rechecks/${PRICED.id}`]: {
        body: { ...RECHECK, refusal, way: null, subject: null, body: null },
      },
    });
    const window = await dialog();

    expect(await within(window).findByText(refusal)).toBeVisible();
    expect(within(window).queryByRole('button', { name: 'Отправить перезапрос' })).toBeNull();
  });

  it('без права писать наружу — посмотреть можно, отправить нельзя', async () => {
    await openCard(
      PRICED,
      { [`GET /api/price-rechecks/${PRICED.id}`]: { body: RECHECK } },
      OPERATOR,
    );
    const window = await dialog('Можно ли перезапросить');

    expect(await within(window).findByText(RECHECK.subject!)).toBeVisible();
    expect(within(window).queryByRole('button', { name: 'Отправить перезапрос' })).toBeNull();
  });
});

/** Держит ответы на POST, пока тест не отпустит: письмо «в пути». */
function holdPosts(): () => void {
  const served = vi.mocked(globalThis.fetch).getMockImplementation();
  if (served === undefined) throw new Error('сервер теста не записан');
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    if ((init?.method ?? 'GET').toUpperCase() === 'POST') await gate;
    return served(input, init);
  });
  return release;
}

describe('перезапрос в пути', () => {
  it('окно закрыли, пока письмо уходит, и открыли снова — кнопка заперта до итога', async () => {
    const recorded = await openCard(PRICED, {
      [`GET /api/price-rechecks/${PRICED.id}`]: { body: RECHECK },
      'POST /api/price-rechecks': { body: ASKED },
    });
    const release = holdPosts();
    const user = userEvent.setup();
    const first = await dialog();

    await user.click(await within(first).findByRole('button', { name: 'Отправить перезапрос' }));
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: `Перезапрос цены · ${PRICED.host}` }),
      ).not.toBeInTheDocument(),
    );
    const again = await dialog();

    expect(
      await within(again).findByRole('button', { name: 'Отправить перезапрос' }),
    ).toBeDisabled();
    release();
    expect(
      await within(again).findByText(`Перезапрос цены ушёл: ${PRICED.host}.`),
    ).toBeInTheDocument();
    expect(recorded.calls.filter((call) => call.method === 'POST')).toHaveLength(1);
  });
});
