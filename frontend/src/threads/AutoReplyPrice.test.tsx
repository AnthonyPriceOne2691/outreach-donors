/**
 * Автоответ с суммой в валюте: цену из него вписывает человек.
 *
 * Модель автоответы не разбирает, и до 28.09.2026 такой ответ показывался
 * без формы цены: «ждёт разбора» у диалога было бы, а вписать цену было бы
 * некуда. Сервер называет причину (`review_reason`), и по ней карточка
 * даёт форму — с объяснением, а не с неправдой про «уверенность разбора».
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const REASON = 'автоответ с суммой в валюте — модель автоответы не разбирает, цену смотрит человек';

const PRICED_AUTO_REPLY = {
  id: 8,
  kind: 'auto_reply',
  raw_body: 'Thank you for your email. Our sponsored post rate is $150. We reply within 3 days.',
  received_at: '2026-09-29T10:00:00+00:00',
  from_email: 'support@donor.example.test',
  subject: 'Re: Advertising rates',
  attachments: [],
  price_white: null,
  price_grey: null,
  currency: null,
  payment_methods: null,
  confidence: null,
  placement: null,
  needs_review: true,
  review_reason: REASON,
  reviewed_by: null,
  reviewed_at: null,
  lead: false,
};

const VIEW = {
  card: {
    id: 4,
    host: 'donor.example.test',
    contact_email: 'editor@donor.example.test',
    campaign: 'Проверка',
    stage: 'donors',
    state: 'needs_review',
    messages_sent: 1,
    last_event_at: '2026-09-29T10:00:00+00:00',
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  },
  letters: [],
  incoming: [PRICED_AUTO_REPLY],
  corridor: { min: 0.15, max: 0.25 },
};

async function openThread(incoming: Record<string, unknown>, routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/4': { body: { ...VIEW, incoming: [incoming] } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/threads/4');
  await screen.findByRole('heading', { name: 'donor.example.test' });
  return recorded;
}

describe('автоответ с суммой', () => {
  it('даёт форму цены и называет причину словами сервера', async () => {
    await openThread(PRICED_AUTO_REPLY);

    expect(screen.getByText('Цену подтверждает человек')).toBeInTheDocument();
    expect(screen.getByText(/модель автоответы не разбирает/)).toBeInTheDocument();
    // Разбора не было — и слов про его уверенность быть не должно.
    expect(screen.queryByText(/Уверенности разбора не хватило/)).not.toBeInTheDocument();
    expect(screen.queryByText(/уверенность разбора/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Белая цена')).toBeEnabled();
  });

  it('вписанная человеком цена уходит на сервер', async () => {
    const recorded = await openThread(PRICED_AUTO_REPLY, {
      'PATCH /api/replies/8': {
        body: { id: 8, reviewed_by: 'админ@site.com', stored_price: true },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Белая цена'), '150');
    await user.type(screen.getByLabelText('Валюта'), 'USD');
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));

    await screen.findByText(/записана в карточку донора/);
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.path).toBe('/api/replies/8');
    expect(patch?.body).toMatchObject({ price_white: '150', currency: 'USD' });
  });

  it('подтверждённый автоответ называет, кто вписал цену', async () => {
    await openThread({
      ...PRICED_AUTO_REPLY,
      needs_review: false,
      price_white: '150',
      currency: 'USD',
      reviewed_by: 'оператор@site.com',
      reviewed_at: '2026-09-29T11:00:00+00:00',
    });

    expect(screen.getByText('подтвердил оператор@site.com')).toBeInTheDocument();
    expect(screen.queryByText('Цену подтверждает человек')).not.toBeInTheDocument();
  });

  it('автоответ без суммы формы не даёт', async () => {
    await openThread({
      ...PRICED_AUTO_REPLY,
      raw_body: 'I am out of the office until Monday.',
      needs_review: false,
      review_reason: null,
    });

    expect(screen.queryByRole('button', { name: 'Подтвердить' })).not.toBeInTheDocument();
  });
});
