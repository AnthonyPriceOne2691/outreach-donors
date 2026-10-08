/**
 * Переписка продаж: вид ответа лида разбирает модуль продаж, ответ уходит мостом почты.
 *
 * Сервер отдаёт такой диалог как есть — этап `sales`, своё состояние и причину
 * ожидания словами. Экран не делает из него донора: ни формы цены, ни «взять
 * лид», ни формы ответа — только пометка этапа и причина, почему ответ ждёт человека
 * (лиду отвечают черновиком агента продаж — плашкой черновика).
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

// Причина ожидания — словами сервера: вид ответа ещё не разобран (`replies/outcome.SALES_WAITING`).
const REASON = 'вид ответа продаж ещё не разобран — задача в очереди продаж';

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

  it('у донора пометки этапа нет, а пояснение у поля ответа на месте', async () => {
    const donor = {
      ...VIEW,
      card: { ...VIEW.card, stage: 'donors', state: 'needs_review' },
      incoming: [{ ...VIEW.incoming[0], review_reason: null }],
    };
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/threads/5': { body: donor } });

    renderWith(<AppRoutes />, '/threads/5');
    await screen.findByRole('heading', { name: 'lead.example.test' });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Ответить' }));

    // Пометка этапа и причина продаж — только у продаж: ответ донору и его
    // пояснение, с какого ящика он уйдёт, — у самого поля, как без них.
    expect(screen.getByLabelText('Текст ответа')).toHaveAccessibleDescription(
      /с того же ящика, что вёл переписку/,
    );
    expect(screen.queryByText(/· продажи|Ждёт человека:/)).not.toBeInTheDocument();
  });
});
