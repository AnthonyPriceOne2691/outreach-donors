/**
 * Плашка черновика агента в переписке — через настоящий экран переписки.
 *
 * Проверяется обещанное Spec 5.1: готовый черновик уходит как есть или
 * с правкой (A1, A2), отклонить без причины нельзя (A3), у отданного человеку
 * «как есть» нет (A4), «ответ не нужен» виден и оспаривается ответом (A5).
 * И условие соседей: «черновик устарел» говорит сервер — экран показывает его
 * словами, а ответить своими словами черновик не мешает.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DraftCard } from '../api/agent';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Call } from '../test/server';

const LETTER = {
  id: 1,
  step: 0,
  status: 'delivered',
  subject: 'Advertising rates',
  body: 'Good afternoon,',
  sent_at: '2026-09-18T10:00:00+00:00',
  uniqueness: null,
  answers_reply_id: null,
};

const ASKS = {
  id: 7,
  kind: 'human',
  raw_body: 'Our price is $90. Which topic?',
  received_at: '2026-09-19T10:00:00+00:00',
  from_email: 'boss@donor.example',
  subject: 'Re: Advertising rates',
  attachments: [],
  price_white: null,
  price_grey: null,
  currency: null,
  payment_methods: null,
  confidence: 0.9,
  placement: 'sells',
  needs_review: false,
  reviewed_by: null,
  reviewed_at: null,
  lead: false,
};

const DRAFT: DraftCard = {
  id: 11,
  reply_id: 7,
  thread_id: 3,
  status: 'drafted',
  body: 'Thanks! A guide on home repair works.',
  reason: null,
  settings_version: 2,
  written_at: '2026-09-19T10:05:00+00:00',
  decided_by: null,
  decided_at: null,
  verdict: 'allow',
  attempts: 1,
};

const REASONS = ['не о том', 'длинно'];

function viewWith(drafts: DraftCard[]) {
  return {
    card: {
      id: 3,
      host: 'donor.example',
      contact_email: 'editor@donor.example',
      campaign: 'Проверка',
      stage: 'donors',
      state: 'replied',
      messages_sent: 1,
      last_event_at: '2026-09-19T10:00:00+00:00',
      last_reply_at: '2026-09-19T10:00:00+00:00',
      price_white: null,
      price_grey: null,
      currency: null,
    },
    letters: [LETTER],
    incoming: [ASKS],
    corridor: { min: 0.15, max: 0.25 },
    drafts,
    agent_writes: true,
    agent_reasons: REASONS,
  };
}

const SENT = { id: 40, sender_email: 'anna@mail.example', real: true };

type Routes = (decide: () => void) => Record<string, unknown>;

/** Переписка с черновиком; после `decide` сервер отдаёт её уже без живого черновика. */
async function openThread(draft: DraftCard, routes: Routes = () => ({}), who = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  let decided = false;
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/threads/3': () => ({
      body: viewWith([decided ? { ...draft, status: 'sent' } : draft]),
    }),
    ...(routes(() => {
      decided = true;
    }) as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/threads/3');
  await screen.findByRole('heading', { name: 'donor.example' });
  return recorded;
}

function banner() {
  return screen.getByRole('region', { name: 'Черновик агента' });
}

function posted(calls: Call[], path: string) {
  return calls.find((call) => call.method === 'POST' && call.path === path);
}

describe('плашка черновика агента', () => {
  it('A1: готовый черновик уходит как есть, и плашка исчезает', async () => {
    const recorded = await openThread(DRAFT, (decide) => ({
      'POST /api/agent/drafts/11/send': () => {
        decide();
        return { body: SENT };
      },
    }));
    const user = userEvent.setup();

    expect(within(banner()).getByText('черновик агента')).toBeInTheDocument();
    expect(within(banner()).getByText('ход 1')).toBeInTheDocument();
    expect(within(banner()).getByText('судья: пропустил')).toBeInTheDocument();
    expect(within(banner()).getByText('попыток: 1')).toBeInTheDocument();
    await user.click(within(banner()).getByRole('button', { name: 'Подходит · отправить' }));

    expect(await screen.findByText('Ответ отправлен с anna@mail.example')).toBeInTheDocument();
    expect(posted(recorded.calls, '/api/agent/drafts/11/send')?.body).toEqual({});
    await waitFor(() => {
      expect(screen.queryByRole('region', { name: 'Черновик агента' })).not.toBeInTheDocument();
    });
  });

  it('A2: правка уходит своим текстом', async () => {
    const recorded = await openThread(DRAFT, () => ({
      'POST /api/agent/drafts/11/send': { body: SENT },
    }));
    const user = userEvent.setup();

    await user.click(within(banner()).getByRole('button', { name: 'Править' }));
    const field = within(banner()).getByLabelText('Текст ответа по черновику');
    expect(field).toHaveValue(DRAFT.body);
    await user.clear(field);
    await user.type(field, 'Thanks! 1500 words on home repair.');
    await user.click(within(banner()).getByRole('button', { name: 'Отправить правку' }));

    await screen.findByText('Ответ отправлен с anna@mail.example');
    expect(posted(recorded.calls, '/api/agent/drafts/11/send')?.body).toEqual({
      body: 'Thanks! 1500 words on home repair.',
    });
  });

  it('A3: без причины отклонить нельзя; причина — из списка этапа или словами', async () => {
    const recorded = await openThread(DRAFT, () => ({
      'POST /api/agent/drafts/11/reject': { body: { ...DRAFT, status: 'rejected' } },
    }));
    const user = userEvent.setup();

    await user.click(within(banner()).getByRole('button', { name: 'Отклонить' }));
    const reject = within(banner()).getByRole('button', { name: 'Отклонить' });
    expect(reject).toBeDisabled();
    expect(within(banner()).getByRole('radio', { name: 'длинно' })).toBeInTheDocument();
    await user.click(within(banner()).getByRole('radio', { name: 'другое — словами' }));
    expect(reject).toBeDisabled(); // «другое» без слов — не причина
    await user.type(within(banner()).getByLabelText('Причина словами'), 'цена не та');
    expect(reject).toBeEnabled();
    await user.click(reject);

    await screen.findByText('Черновик отклонён, причина записана');
    expect(posted(recorded.calls, '/api/agent/drafts/11/reject')?.body).toEqual({
      reason: 'другое: цена не та',
    });
  });

  it('A4: отданный человеку — розой, без «как есть», прежний текст правкой не уходит', async () => {
    await openThread({
      ...DRAFT,
      status: 'escalated',
      reason: 'цена за пределом',
      verdict: null,
      attempts: 0,
    });
    const user = userEvent.setup();

    expect(within(banner()).getByText('агент отдал ответ человеку')).toBeInTheDocument();
    expect(within(banner()).getByText(/Почему: цена за пределом/)).toBeInTheDocument();
    expect(within(banner()).queryByRole('button', { name: /Подходит/ })).not.toBeInTheDocument();
    await user.click(within(banner()).getByRole('button', { name: 'Править' }));
    const send = within(banner()).getByRole('button', { name: 'Отправить правку' });
    expect(send).toBeDisabled();
    await user.type(within(banner()).getByLabelText('Текст ответа по черновику'), ' Or $80.');
    expect(send).toBeEnabled();
  });

  it('A5: «ответ не нужен» виден, «Ответить всё же» — пустой черновик обычным ответом', async () => {
    const recorded = await openThread(
      { ...DRAFT, status: 'skipped', body: '', reason: 'собеседник поблагодарил' },
      () => ({ 'POST /api/threads/3/answer': { body: SENT } }),
    );
    const user = userEvent.setup();

    expect(screen.getByText('агент: ответ не нужен')).toBeInTheDocument();
    expect(screen.getByText('собеседник поблагодарил')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ответить всё же' }));
    const field = screen.getByLabelText('Текст ответа по черновику');
    expect(field).toHaveValue('');
    await user.type(field, 'Glad to help!');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await screen.findByText('Ответ отправлен с anna@mail.example');
    expect(posted(recorded.calls, '/api/threads/3/answer')?.body).toEqual({
      reply_id: 7,
      body: 'Glad to help!',
    });
  });

  it('устаревший черновик: отказ сервера словами, черновик на месте', async () => {
    await openThread(DRAFT, () => ({
      'POST /api/agent/drafts/11/send': {
        status: 409,
        body: { detail: 'Черновик №11 устарел: собеседник написал ещё (ответ №8)' },
      },
    }));
    const user = userEvent.setup();

    await user.click(within(banner()).getByRole('button', { name: 'Подходит · отправить' }));

    expect(await screen.findByText(/Черновик №11 устарел/)).toBeInTheDocument();
    expect(banner()).toBeInTheDocument();
  });

  it('у поля ответа — подсказка о черновике, ответить своими словами можно', async () => {
    await openThread(DRAFT);

    expect(screen.getByText(/Есть черновик агента выше/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ответить' })).toBeEnabled();
  });

  it('без права отправки — черновик виден, решать нельзя', async () => {
    await openThread(DRAFT, () => ({}), OPERATOR);

    expect(within(banner()).getByText(DRAFT.body)).toBeInTheDocument();
    expect(within(banner()).queryByRole('button')).not.toBeInTheDocument();
    expect(within(banner()).getByText(/у кого есть право отправки/)).toBeInTheDocument();
  });

  it('показан только последний черновик и только над своим письмом', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const later = { ...ASKS, id: 8, received_at: '2026-09-20T10:00:00+00:00' };
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/3': {
        body: {
          ...viewWith([DRAFT, { ...DRAFT, id: 12, reply_id: 8, body: 'Second draft.' }]),
          incoming: [ASKS, later],
        },
      },
    });
    renderWith(<AppRoutes />, '/threads/3');
    await screen.findByRole('heading', { name: 'donor.example' });

    expect(screen.getAllByRole('region', { name: 'Черновик агента' })).toHaveLength(1);
    expect(within(banner()).getByText('Second draft.')).toBeInTheDocument();
    expect(within(banner()).getByText('ход 2')).toBeInTheDocument();
  });
});
