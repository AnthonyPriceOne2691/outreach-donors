/**
 * Перезапрос цены в ленте переписки (12.10.2026): письмо названо тем, что оно есть, —
 * «перезапрос цены», а не «добивка 200», — и переписка в шапке снова ждёт донора.
 * Ответ на перезапрос без числа ждёт человека, и у него — «Цена та же»: прежняя цена
 * уходит на сервер номером ответа, после подтверждения у кнопки.
 *
 * Номер шага перезапроса у экрана — тот же, что у сервера (`PRICE_RECHECK_STEP`, сверка —
 * `tests/test_price_recheck_rules.py`).
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { PRICE_RECHECK_STEP } from '../api/priceRecheck';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { letter, reply, threadWith } from '../test/threadFixtures';
import { letterTitle } from './ThreadFeed';

/** Донор назвал цену полгода назад, цена протухла — ушёл перезапрос его же веткой. */
const ASKED = letter({
  id: 44,
  step: PRICE_RECHECK_STEP,
  subject: 'Re: Rates',
  body: 'Hi,\n\nThe rate we have on file for your site is from a while back.',
  sent_at: '2026-10-12T09:00:00+00:00',
  status: 'sent',
});

async function openThread() {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const base = threadWith();
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/8': {
      body: threadWith({
        card: { ...base.card, state: 'recheck' },
        letters: [letter({ sent_at: '2026-04-01T10:00:00+00:00' }), ASKED],
        incoming: [reply({ received_at: '2026-04-02T10:00:00+00:00' })],
      }),
    },
  });
  renderWith(<AppRoutes />, '/threads/8');
  await screen.findByRole('heading', { name: 'prices.example.test' });
}

/** Донор ответил на перезапрос «цена та же» — числа нет, ответ ждёт человека. */
const SAME = reply({
  id: 53,
  attachments: [],
  raw_body: 'Same as before, thanks.',
  price_white: null,
  currency: null,
  needs_review: true,
  review_reason:
    'ответ на перезапрос цены, а числа в нём нет: донор подтвердил прежнюю цену — «Цена та же»; назвал новую — впишите её',
  received_at: '2026-10-12T11:00:00+00:00',
  same_price: { price: '90.00', currency: 'USD' },
});

describe('перезапрос цены в переписке', () => {
  it('«Цена та же» — после подтверждения у кнопки уходит номером ответа', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const base = threadWith();
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/8': {
        body: threadWith({
          card: { ...base.card, state: 'needs_review' },
          letters: [letter({ sent_at: '2026-04-01T10:00:00+00:00' }), ASKED],
          incoming: [reply({ received_at: '2026-04-02T10:00:00+00:00' }), SAME],
        }),
      },
      'POST /api/price-rechecks/replies/53/same-price': {
        body: {
          reply_id: 53,
          price: '90.00',
          currency: 'USD',
          at: '2026-10-12T11:00:00+00:00',
          stored: true,
        },
      },
    });
    renderWith(<AppRoutes />, '/threads/8');
    await screen.findByRole('heading', { name: 'prices.example.test' });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /^Цена та же/ }));
    const ask = await screen.findByRole('dialog', {
      name: /Донор подтвердил прежнюю цену 90,00/,
      hidden: true,
    });
    expect(recorded.calls.some((call) => call.method === 'POST')).toBe(false);
    // Выпадающее окно Mantine в jsdom — `display: none`: без `hidden` кнопок не видно.
    await user.click(within(ask).getByRole('button', { name: 'Подтвердить', hidden: true }));

    await waitFor(() =>
      expect(
        recorded.calls.some((call) => call.path === '/api/price-rechecks/replies/53/same-price'),
      ).toBe(true),
    );
    expect(
      await screen.findByText('Прежняя цена подтверждена ответом донора и снова свежая'),
    ).toBeInTheDocument();
  });

  it('письмо названо перезапросом, а переписка в шапке снова ждёт ответа', async () => {
    await openThread();

    expect(screen.getByRole('article', { name: 'Наше письмо: перезапрос цены' })).toHaveTextContent(
      'The rate we have on file for your site is from a while back.',
    );
    const header = screen.getByRole('heading', { name: 'prices.example.test' }).parentElement;
    expect(
      within(header as HTMLElement).getByText('цену перезапросили — ждём ответа'),
    ).toBeInTheDocument();
  });

  it('по шагу: перезапрос — перезапросом, ответ человеку — по-прежнему ответом', () => {
    expect(letterTitle(ASKED)).toBe('перезапрос цены');
    expect(letterTitle(letter({ step: 100, answers_reply_id: 52 }))).toBe('наш ответ');
    expect(letterTitle(letter({ step: 1 }))).toBe('добивка 1');
  });
});
