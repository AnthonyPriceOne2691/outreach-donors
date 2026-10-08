/**
 * Переписка продаж: вид ответа лида разбирает модуль продаж, ответ уходит мостом почты.
 *
 * Сервер отдаёт такой диалог как есть — этап `sales`, своё состояние и причину
 * ожидания словами. Экран не делает из него донора: ни формы цены, ни «взять
 * лид» — пометка этапа и причина, почему ответ ждёт человека. Ответить лиду можно
 * своими словами (подпись и адрес допишет модуль продаж) или черновиком агента
 * продаж — плашкой черновика; отказ моста почты — словами сервера.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
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
  });

  it('лиду отвечают своими словами — подпись и адрес допишет сервер', async () => {
    // До 08.10.2026 формы ответа у лида продаж не было: с выключенным агентом продаж
    // на вопрос лида ответить было нечем.
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': { body: VIEW },
      'POST /api/threads/5/answer': {
        body: { id: 12, sender_email: 'sales@mail.example.test', real: true },
      },
    });
    const user = userEvent.setup();

    renderWith(<AppRoutes />, '/threads/5');
    await screen.findByRole('heading', { name: 'lead.example.test' });
    await user.click(screen.getByRole('button', { name: 'Ответить' }));

    const field = screen.getByLabelText('Текст ответа');
    expect(field).toHaveAccessibleDescription(
      /Подпись и физический адрес из настроек «Отправителя» допишутся сами/,
    );
    await user.type(field, 'Sure, tomorrow at 10 works.');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(
      await screen.findByText('Ответ отправлен с sales@mail.example.test'),
    ).toBeInTheDocument();
    const call = recorded.calls.find((sent: Call) => sent.path === '/api/threads/5/answer');
    expect(call?.body).toEqual({ reply_id: 21, body: 'Sure, tomorrow at 10 works.' });
  });

  it('отказ моста почты — словами сервера, а не «что-то пошло не так»', async () => {
    const said =
      'Ответ в переписке №5: продажи не подключены к почте — в настройках «Отправителя» нет ' +
      'физического адреса';
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': { body: VIEW },
      'POST /api/threads/5/answer': { status: 409, body: { detail: said } },
    });
    const user = userEvent.setup();

    renderWith(<AppRoutes />, '/threads/5');
    await screen.findByRole('heading', { name: 'lead.example.test' });
    await user.click(screen.getByRole('button', { name: 'Ответить' }));
    await user.type(screen.getByLabelText('Текст ответа'), 'Sure.');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(await screen.findByText(said)).toBeInTheDocument();
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
    // Подпись донору пишет человек сам: её дописывает только модуль продаж.
    expect(screen.getByLabelText('Текст ответа')).not.toHaveAccessibleDescription(
      /Подпись и физический адрес/,
    );
    expect(screen.queryByText(/· продажи|Ждёт человека:/)).not.toBeInTheDocument();
  });
});
