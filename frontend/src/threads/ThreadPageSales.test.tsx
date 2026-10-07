/**
 * Переписка продаж: почта продажи ещё не ведёт.
 *
 * Сервер отдаёт такой диалог как есть — этап `sales`, своё состояние и причину
 * ожидания словами. Экран не делает из него донора: ни формы цены, ни «взять
 * лид», ни ответа — только пометка этапа и причина, почему ответ ждёт человека.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const REASON = 'ответ продаж ждёт разбора: продажи к почте ещё не подключены';

const VIEW = {
  card: {
    id: 5,
    host: 'lead.example.test',
    contact_email: 'ceo@lead.example.test',
    campaign: 'Продажи',
    stage: 'sales',
    state: 'sales_pending',
    messages_sent: 1,
    last_event_at: '2026-10-05T10:00:00+00:00',
    last_reply_at: '2026-10-05T10:00:00+00:00',
    price_white: null,
    price_grey: null,
    currency: null,
  },
  letters: [
    {
      id: 11,
      step: 0,
      status: 'delivered',
      subject: 'A question about your team',
      body: 'Hi,',
      sent_at: '2026-10-04T10:00:00+00:00',
      uniqueness: 0.2,
      answers_reply_id: null,
    },
  ],
  incoming: [
    {
      id: 21,
      kind: 'human',
      raw_body: 'We pay $300 a month for this today. Call me tomorrow.',
      received_at: '2026-10-05T10:00:00+00:00',
      from_email: 'boss@lead.example.test',
      subject: 'Re: A question about your team',
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
    },
  ],
  corridor: { min: 0.15, max: 0.25 },
};

describe('переписка продаж', () => {
  it('помечена этапом и говорит, почему ответ ждёт человека, — без донорских действий', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': { body: VIEW },
    });

    renderWith(<AppRoutes />, '/threads/5');
    await screen.findByRole('heading', { name: 'lead.example.test' });

    expect(screen.getByText(/кампания «Продажи» · продажи/)).toBeInTheDocument();
    expect(screen.getByText('ответ продаж — ждёт человека')).toBeInTheDocument();
    expect(screen.getByText(`Ждёт человека: ${REASON}.`)).toBeInTheDocument();
    expect(screen.queryByLabelText('Белая цена')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ответить' })).not.toBeInTheDocument();
  });
});
