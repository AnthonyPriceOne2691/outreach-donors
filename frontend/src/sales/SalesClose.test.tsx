/**
 * «Закрыть без ответа» под лентой переписки продаж — через весь экран переписки (`AppRoutes`).
 *
 * Слово владельца 09.11.2026: подтверждение заявки службы поддержки, принятое ответом человека, и
 * пустой ответ снимались с ожидания только передачей телемаркетологу. Сервер — записанные ответы;
 * письма выдуманные, домены — `*.example.test`.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import { reply, threadWith } from '../test/threadFixtures';
import type { ClosedWithoutAnswer } from './handoffApi';
import { closedOn } from './SalesClose';
import type { CloseMutation } from './SalesClose';

const PATH = '/api/sales/threads/8/close';
const BUTTON = 'Закрыть без ответа';
const WHO = 'админ@site.com';
const REASON = 'модель: интересуется, уверенность 0% ниже порога 80% — решает человек';

/** Подтверждение заявки службы поддержки, принятое ответом человека до правила. */
const RECEIPT = reply({
  id: 61,
  from_email: 'support@zayavka.example.test',
  raw_body: 'Здравствуйте. Ваше обращение передано в ответственный отдел. Оцените качество.',
  subject: 'Re: Кто у вас отвечает за SEO?',
  attachments: [],
  confidence: null,
  currency: null,
  placement: null,
  price_white: null,
  received_at: '2026-11-06T08:23:00+00:00',
  needs_review: true,
  review_reason: REASON,
});

/** Ответ лида, пришедший позже, — его человек не видел. */
const LATER = reply({
  ...RECEIPT,
  id: 62,
  from_email: 'olga@zayavka.example.test',
  raw_body: 'Добрый день! Пришлите, пожалуйста, предложение.',
  received_at: '2026-11-09T09:05:00+00:00',
});

const CARD = threadWith().card;

const WAITING = {
  ...threadWith({
    card: {
      ...CARD,
      host: 'zayavka.example.test',
      contact_email: RECEIPT.from_email,
      stage: 'sales',
      state: 'sales_pending',
    },
    incoming: [RECEIPT],
  }),
  handoff: null,
};

const CLOSED_RECEIPT = {
  ...RECEIPT,
  needs_review: false,
  review_reason: `закрыт без ответа: ${WHO}`,
  reviewed_by: WHO,
  reviewed_at: '2026-11-09T10:20:00+00:00',
};

const CLOSED = {
  ...WAITING,
  card: { ...WAITING.card, state: 'replied' },
  incoming: [CLOSED_RECEIPT],
};

const DONE: ClosedWithoutAnswer = {
  released: [61],
  later: 0,
  said: 'Закрыто без ответа: переписка больше не ждёт человека.',
};

async function opened() {
  renderWith(<AppRoutes />, '/threads/8');
  await screen.findByRole('heading', { name: 'zayavka.example.test' });
}

async function pressClose() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: BUTTON }));
  const ask = await screen.findByRole('dialog');
  // Подтверждение называет последствия: ждать перестанет, наружу ничего не уйдёт.
  expect(ask).toHaveTextContent('Переписка перестанет ждать человека');
  expect(ask).toHaveTextContent('Сделки в Kommo и сообщения в Telegram не будет');
  await user.click(within(ask).getByRole('button', { name: 'Закрыть' }));
}

function posts(calls: Call[], path: string): Call[] {
  return calls.filter((call) => call.method === 'POST' && call.path === path);
}

describe('«Закрыть без ответа» у ответа продаж', () => {
  beforeEach(() => localStorage.setItem(TOKEN_KEY, 'пропуск'));

  it('закрывает ответ, который видно, — карточка уходит, под ответом итог словами', async () => {
    let pressed = false;
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/8': () => ({ body: pressed ? CLOSED : WAITING }),
      [`POST ${PATH}`]: () => {
        pressed = true;
        return { body: DONE };
      },
    });
    await opened();

    await pressClose();

    expect(await screen.findByText(DONE.said)).toBeInTheDocument();
    expect(await screen.findByText(`Закрыт без ответа: ${WHO}.`)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Ответ ждёт человека' })).not.toBeInTheDocument();
    expect(posts(recorded.calls, PATH).map((call) => call.body)).toEqual([{ reply_id: 61 }]);
    // Передачи нет: ни сделки, ни сообщения.
    expect(posts(recorded.calls, '/api/sales/threads/8/handoff')).toHaveLength(0);
  });

  it('ответ, пришедший позже, ждёт дальше: карточка — уже о нём, итог — под кнопками', async () => {
    let pressed = false;
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/8': () => ({
        body: pressed ? { ...WAITING, incoming: [CLOSED_RECEIPT, LATER] } : WAITING,
      }),
      [`POST ${PATH}`]: () => {
        pressed = true;
        return {
          body: {
            released: [61],
            later: 1,
            said: 'Закрыто без ответа: новый ответ лида пришёл позже и ждёт человека.',
          },
        };
      },
    });
    await opened();

    await pressClose();

    const said = 'Закрыто без ответа: новый ответ лида пришёл позже и ждёт человека.';
    await waitFor(() => expect(screen.getAllByText(said)).toHaveLength(2));
    const card = screen.getByRole('region', { name: 'Ответ ждёт человека' });
    expect(within(card).getByText(/olga@zayavka\.example\.test/)).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: BUTTON })).toBeEnabled();
  });

  it('отказ сервера — словами под кнопками и уведомлением; кнопки остаются', async () => {
    const said = 'в диалоге №8 ответ №61 и ответы до него уже не ждут человека — закрывать нечего';
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/8': { body: WAITING },
      [`POST ${PATH}`]: { status: 409, body: { detail: said } },
    });
    await opened();

    await pressClose();

    await waitFor(() => expect(screen.getAllByText(said)).toHaveLength(2));
    const card = screen.getByRole('region', { name: 'Ответ ждёт человека' });
    expect(within(card).getByText(said)).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: BUTTON })).toBeEnabled();
    expect(within(card).getByRole('button', { name: 'Передать телемаркетологу' })).toBeEnabled();
  });
});

describe('итог закрытия — только своей переписки', () => {
  it('нажатие в соседней переписке здесь не показывается', () => {
    const pressed = {
      variables: { threadId: 8, replyId: 61 },
      isPending: true,
      data: DONE,
      error: new Error('отказ'),
    };
    const close = pressed as unknown as CloseMutation;

    expect(closedOn(close, 9)).toEqual({ busy: false, said: null, refusal: null });
    expect(closedOn(close, 8)).toEqual({ busy: true, said: DONE.said, refusal: pressed.error });
  });
});
