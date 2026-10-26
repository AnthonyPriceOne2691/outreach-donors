/**
 * «Передать телемаркетологу» под лентой переписки продаж — через весь экран переписки
 * (`AppRoutes`): кнопку подключает сборка экранов, «Диалоги» экранов продаж не знают.
 *
 * Сервер — записанные ответы. Ответы лидов — выдуманные, с номерами разных стран и форм записи.
 * Часы закреплены: строка состояния печатает дату передачи, а перечитывание переписки идёт, пока
 * передаче меньше двух минут.
 */

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { formatDateTime } from '../format';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import type { HandedOver, SalesHandoff } from './handoffApi';
import { pressedOn, SETTLE_FOR_MS, settling } from './SalesHandoff';
import type { HandMutation } from './SalesHandoff';

const NOW = new Date('2026-10-26T09:41:30+00:00');
const PATH = '/api/sales/threads/5/handoff';
const BUTTON = 'Передать телемаркетологу';
const REASON = 'модель: назвал другого человека, уверенность 61% ниже порога 80% — решает человек';

const REPLY = {
  id: 21,
  kind: 'human',
  raw_body: 'Merhaba, beni arayın: +90 212 000 00 00 — Deniz',
  fresh_body: 'Merhaba, beni arayın: +90 212 000 00 00 — Deniz',
  received_at: '2026-10-26T08:12:00+00:00',
  from_email: 'deniz@liman.example.test',
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
};

const WAITING = {
  card: {
    id: 5,
    host: 'liman.example.test',
    contact_email: 'deniz@liman.example.test',
    campaign: 'Продажи',
    stage: 'sales',
    state: 'sales_pending',
    messages_sent: 1,
    last_event_at: REPLY.received_at,
    last_reply_at: REPLY.received_at,
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
      sent_at: '2026-10-25T10:00:00+00:00',
      uniqueness: 0.2,
      answers_reply_id: null,
    },
  ],
  incoming: [REPLY],
  corridor: { min: 0.15, max: 0.25 },
  handoff: null,
};

const PENDING: SalesHandoff = {
  id: 3,
  at: '2026-10-26T09:41:00+00:00',
  kommo: 'pending',
  kommo_words: 'запись ждёт задачи',
  telegram: 'pending',
  telegram_words: 'ещё не отправлено',
};

const SETTLED: SalesHandoff = {
  ...PENDING,
  kommo: 'off',
  kommo_words: 'только ссылка на диалог: Kommo не подключён',
  telegram: 'sent',
  telegram_words: 'отправлено',
};

const DONE: HandedOver = {
  handoff: PENDING,
  queued: true,
  released: [21],
  said:
    'Лид передан телемаркетологу: сделка в Kommo и сообщение в Telegram уходят задачей ' +
    'очереди продаж; ответ больше не ждёт человека.',
};

/** Переписка после передачи: ответ больше не ждёт, у переписки — передача. */
function handed(handoff: SalesHandoff) {
  return {
    ...WAITING,
    card: { ...WAITING.card, state: 'replied' },
    incoming: [
      {
        ...REPLY,
        needs_review: false,
        review_reason: 'передан телемаркетологу вручную: админ@site.com',
        reviewed_by: 'админ@site.com',
        reviewed_at: PENDING.at,
      },
    ],
    handoff,
  };
}

function line(handoff: SalesHandoff): string {
  return (
    `Передан телемаркетологу ${formatDateTime(handoff.at)}: Kommo — ${handoff.kommo_words}, ` +
    `Telegram — ${handoff.telegram_words}.`
  );
}

async function opened() {
  renderWith(<AppRoutes />, '/threads/5');
  await screen.findByRole('heading', { name: 'liman.example.test' });
}

function posts(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'POST' && call.path === PATH);
}

describe('«Передать телемаркетологу» у ответа продаж', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    localStorage.setItem(TOKEN_KEY, 'пропуск');
  });
  afterEach(() => vi.useRealTimers());

  it('у ответа, который ждёт человека, — кнопка; после подтверждения — исход и строка состояния', async () => {
    let pressed = false;
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': () => ({ body: pressed ? handed(SETTLED) : WAITING }),
      [`POST ${PATH}`]: () => {
        pressed = true;
        return { body: DONE };
      },
    });
    const user = userEvent.setup();
    await opened();

    const card = screen.getByRole('region', { name: 'Передача телемаркетологу' });
    expect(
      within(card).getByText(
        `Передача телемаркетологу · ответ deniz@liman.example.test, ${formatDateTime(REPLY.received_at)}`,
      ),
    ).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: BUTTON }));
    // Подтверждение у кнопки называет последствие: лид уходит наружу.
    const ask = await screen.findByRole('dialog');
    expect(ask).toHaveTextContent('Уйдут сделка в Kommo и сообщение в Telegram');
    await user.click(within(ask).getByRole('button', { name: 'Передать' }));

    // Исход — словами под кнопкой и уведомлением.
    await waitFor(() => expect(screen.getAllByText(DONE.said)).toHaveLength(2));
    expect(await screen.findByText(line(SETTLED))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: BUTTON })).not.toBeInTheDocument();
    expect(posts(recorded.calls)).toHaveLength(1);
  });

  it('отказ сервера — словами рядом с кнопкой и уведомлением; кнопка остаётся', async () => {
    const said = 'у диалога №5 нет лида продаж: ни ссылкой на адрес, ни почтой на его домене';
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': { body: WAITING },
      [`POST ${PATH}`]: { status: 409, body: { detail: said } },
    });
    const user = userEvent.setup();
    await opened();

    await user.click(screen.getByRole('button', { name: BUTTON }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Передать' }),
    );

    await waitFor(() => expect(screen.getAllByText(said)).toHaveLength(2));
    const card = screen.getByRole('region', { name: 'Передача телемаркетологу' });
    expect(within(card).getByText(said)).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: BUTTON })).toBeEnabled();
  });

  it('повтор с устаревшей переписки не ломает: «уже передан» — и на месте кнопки строка состояния', async () => {
    const said =
      'лид уже передан телемаркетологу: Kommo — запись ждёт задачи, ' +
      'Telegram — ещё не отправлено; повтор ничего не меняет';
    let refused = false;
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': () => ({ body: refused ? handed(PENDING) : WAITING }),
      [`POST ${PATH}`]: () => {
        refused = true;
        return { status: 409, body: { detail: said } };
      },
    });
    const user = userEvent.setup();
    await opened();

    await user.click(screen.getByRole('button', { name: BUTTON }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Передать' }),
    );

    expect(await screen.findByText(line(PENDING))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: BUTTON })).not.toBeInTheDocument();
    const card = screen.getByRole('region', { name: 'Передача телемаркетологу' });
    expect(within(card).getByText(said)).toBeInTheDocument();
  });

  it('пока запрос идёт, кнопка занята — второе нажатие не уходит', async () => {
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': { body: WAITING },
    });
    const served = vi.mocked(globalThis.fetch).getMockImplementation();
    let answer: (response: Response) => void = () => undefined;
    let sent = 0;
    vi.mocked(globalThis.fetch).mockImplementation((input, init) => {
      if (init?.method !== 'POST') return served!(input, init);
      sent += 1;
      return new Promise<Response>((resolve) => (answer = resolve));
    });
    const user = userEvent.setup();
    await opened();

    await user.click(screen.getByRole('button', { name: BUTTON }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Передать' }),
    );

    const busy = await screen.findByRole('button', { name: BUTTON });
    await waitFor(() => expect(busy).toBeDisabled());
    await user.click(busy);
    expect(sent).toBe(1);
    // Ответ сервера — внутри `act`: уведомление и перерисовка — его следствия.
    await act(async () => {
      answer(
        new Response(JSON.stringify(DONE), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
      await Promise.resolve();
    });
    // Под кнопкой и в уведомлении.
    await waitFor(() => expect(screen.getAllByText(DONE.said)).toHaveLength(2));
  });

  it('передача уже есть — строка состояния вместо кнопки; сбой — янтарной плашкой', async () => {
    const failed: SalesHandoff = {
      ...PENDING,
      kommo: 'failed',
      kommo_words: 'отказ: ключ Kommo отклонён (HTTP 401) — выпустить новый',
      telegram: 'undelivered',
      telegram_words: 'не доставлено, повторов больше нет: бот заблокирован (HTTP 403)',
    };
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/5': { body: handed(failed) },
    });
    await opened();

    const card = screen.getByRole('region', { name: 'Передача телемаркетологу' });
    expect(within(card).getByRole('alert')).toHaveTextContent(line(failed));
    expect(screen.queryByRole('button', { name: BUTTON })).not.toBeInTheDocument();
  });

  it('у переписки без ждущего ответа и без передачи карточки нет', async () => {
    const answered = {
      ...WAITING,
      card: { ...WAITING.card, state: 'replied' },
      incoming: [{ ...REPLY, needs_review: false, review_reason: 'не сейчас: диалог закрыт' }],
    };
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/threads/5': { body: answered } });
    await opened();

    expect(
      screen.queryByRole('region', { name: 'Передача телемаркетологу' }),
    ).not.toBeInTheDocument();
  });

  it('у переписки доноров карточки нет', async () => {
    const donor = {
      ...WAITING,
      card: { ...WAITING.card, stage: 'donors', state: 'needs_review' },
      incoming: [{ ...REPLY, review_reason: null }],
    };
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/threads/5': { body: donor } });
    await opened();

    expect(screen.queryByRole('button', { name: BUTTON })).not.toBeInTheDocument();
  });

  it('без права отправки писем кнопка закрыта, и рядом сказано почему', async () => {
    serve({ 'GET /api/auth/me': { body: OPERATOR }, 'GET /api/threads/5': { body: WAITING } });
    await opened();

    expect(screen.getByRole('button', { name: BUTTON })).toBeDisabled();
    expect(
      screen.getByText(
        'Передаёт лида телемаркетологу тот, у кого есть право отправки писем. Смотреть можно всем.',
      ),
    ).toBeInTheDocument();
  });
});

describe('итог задачи передачи — перечитыванием переписки', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('пока Kommo или Telegram ждут задачи — перечитывать, но не дольше двух минут', () => {
    expect(settling(PENDING)).toBe(true);
    expect(settling({ ...PENDING, kommo: 'off' })).toBe(true);
    expect(settling(SETTLED)).toBe(false);
    expect(settling(null)).toBe(false);
    vi.setSystemTime(new Date(Date.parse(PENDING.at) + SETTLE_FOR_MS));
    expect(settling(PENDING)).toBe(false);
  });
});

describe('итог нажатия — только своей переписки', () => {
  it('нажатие в соседней переписке здесь не показывается: ни итог, ни отказ, ни занятая кнопка', () => {
    const pressed = { variables: 5, isPending: true, data: DONE, error: new Error('отказ') };
    const hand = pressed as unknown as HandMutation;

    expect(pressedOn(hand, 6)).toEqual({ busy: false, said: null, refusal: null, handoff: null });
    expect(pressedOn(hand, 5)).toEqual({
      busy: true,
      said: DONE.said,
      refusal: pressed.error,
      handoff: DONE.handoff,
    });
  });
});
