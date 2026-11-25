/**
 * «В отказ» у лида рекламодателя — через весь экран переписки (`AppRoutes`), и разбор лида в отказе.
 *
 * Слово владельца 25.11.2026: ответ с отказом («thanks, not interested») висел в «Диалогах
 * с рекламодателями» как «лид — ждёт человека», а снять его можно было только «Взять в работу» — и
 * лид уходил в CRM. Проверяется: кнопка стоит рядом с «Взять в работу» и сначала спрашивает; в CRM
 * ничего не уходит; кто и когда решил — видно; отказ сервера — его словами. Сервер — записанные
 * ответы; письма выдуманные, домены — `*.example.test`.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { ThreadView } from '../api/thread';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Answer, Recorded } from '../test/server';
import { serve } from '../test/server';
import { reply, threadWith } from '../test/threadFixtures';
import { ReplyDecision } from './ReplyDecision';

const PATH = '/api/replies/61/lead/refuse';
const WHO = 'anna@parsingprices.com';
const AT = '2026-11-25T09:30:00+00:00';

/** Ответ рекламодателя на оффер — лид, ждёт человека. */
const LEAD = reply({
  id: 61,
  from_email: 'partners@brand.example.test',
  raw_body: 'Thanks for the offer, but we are not interested right now.',
  subject: 'Re: Partnership',
  attachments: [],
  confidence: null,
  currency: null,
  placement: null,
  price_white: null,
  lead: true,
});

/** Тот же лид, отправленный человеком в отказ: решён, но не взят. */
const REFUSED = reply({ ...LEAD, reviewed_by: WHO, reviewed_at: AT, decision: 'refused' });

const CARD = threadWith().card;

const WAITING = threadWith({
  card: {
    ...CARD,
    host: 'brand.example.test',
    contact_email: LEAD.from_email,
    stage: 'advertisers',
    state: 'lead',
  },
  incoming: [LEAD],
});

const IN_REFUSAL: ThreadView = {
  ...WAITING,
  card: { ...WAITING.card, state: 'lead_refused' },
  incoming: [REFUSED],
};

/** Переписка лида: до нажатия ждёт человека, после — `after`; нажатие сервер встречает `answer`. */
function served(after: ThreadView, answer: Answer): Recorded {
  let pressed = false;
  return serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/8': () => ({ body: pressed ? after : WAITING }),
    [`POST ${PATH}`]: () => {
      pressed = true;
      return answer;
    },
  });
}

async function opened() {
  renderWith(<AppRoutes />, '/threads/8');
  await screen.findByRole('heading', { name: 'brand.example.test' });
}

/** Что ушло на сервер записью — пути по порядку. */
function posted(recorded: Recorded): string[] {
  return recorded.calls.filter((call) => call.method === 'POST').map((call) => call.path);
}

/** «В отказ»: первое нажатие только спрашивает — последствие словами, на кнопке глагол; запрос
 *  уходит подтверждением. */
async function pressRefuse(recorded: Recorded) {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'В отказ' }));
  const ask = await screen.findByRole('dialog');
  expect(ask).toHaveTextContent('В отказ? Лид перестанет ждать человека и в CRM не уйдёт.');
  expect(posted(recorded)).toEqual([]);
  await user.click(within(ask).getByRole('button', { name: 'Отправить в отказ' }));
}

describe('«В отказ» у лида рекламодателя', () => {
  beforeEach(() => localStorage.setItem(TOKEN_KEY, 'пропуск'));

  it('рядом с «Взять в работу»; после подтверждения — запрос, в CRM ничего, кто решил — видно', async () => {
    const recorded = served(IN_REFUSAL, { body: { id: 61, reviewed_by: WHO, reviewed_at: AT } });
    await opened();
    const card = screen.getByLabelText('Лид');
    expect(within(card).getByRole('button', { name: 'Взять в работу' })).toBeInTheDocument();

    await pressRefuse(recorded);

    expect(await screen.findByText('Лид в отказе: человека не ждёт, в CRM не ушёл')).toBeVisible();
    // Ни «взять», ни передачи в CRM — только отказ.
    expect(posted(recorded)).toEqual([PATH]);
    // Переписка перечитана: лид больше не ждёт, разбора под лентой нет, кто решил — в пузыре.
    expect(await screen.findByText(/^В отказе: anna@parsingprices\.com, /)).toBeInTheDocument();
    expect(screen.queryByLabelText('Лид')).not.toBeInTheDocument();
  });

  it('лид в отказе говорит, кто и когда, — без «взять» и «передать в CRM»', async () => {
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/threads/8': { body: IN_REFUSAL } });
    await opened();

    // Состояние переписки и значок ответа — одними словами; «в работе» нигде нет.
    expect(screen.getAllByText('лид в отказе')).toHaveLength(2);
    expect(screen.queryByText(/в работе/i)).not.toBeInTheDocument();
    expect(screen.getByText(/^В отказе: anna@parsingprices\.com, /)).toBeInTheDocument();
    // Решать больше нечего: ни разбора под лентой, ни кнопки в пузыре, которая его открывает.
    expect(screen.queryByLabelText('Лид')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Передача лида' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Взять лид' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Передать в CRM ещё раз' }),
    ).not.toBeInTheDocument();
  });

  it('отказ сервера — его словами; переписка перечитана: видно, кто успел взять лид', async () => {
    const said = `Лид по ответу №61 уже в работе: взял ${WHO} 25.11.2026 09:30 UTC`;
    const taken: ThreadView = {
      ...WAITING,
      card: { ...WAITING.card, state: 'lead_taken' },
      incoming: [{ ...LEAD, reviewed_by: WHO, reviewed_at: AT }],
    };
    const recorded = served(taken, { status: 409, body: { detail: said } });
    await opened();

    await pressRefuse(recorded);

    expect(await screen.findByText(said)).toBeInTheDocument();
    expect(screen.getByText('Не отправили в отказ')).toBeInTheDocument();
    expect(posted(recorded)).toEqual([PATH]);
    expect(await screen.findByText(/^В работе: anna@parsingprices\.com, /)).toBeInTheDocument();
  });
});

describe('разбор лида в отказе', () => {
  it('открытый — «В отказе», и в CRM его не передать, хоть право разбирать и есть', () => {
    renderWith(
      <ReplyDecision
        reply={REFUSED}
        canReview
        busy={false}
        onConfirm={vi.fn()}
        onDecline={vi.fn()}
        refusal={null}
        onTakeLead={vi.fn()}
        onRefuseLead={vi.fn()}
        onSendLead={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText(/^В отказе: anna@parsingprices\.com, /)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Передать в CRM ещё раз' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'В отказ' })).not.toBeInTheDocument();
  });

  it('взятый — по-прежнему «В работе» и «Передать в CRM ещё раз»', () => {
    renderWith(
      <ReplyDecision
        reply={reply({ ...LEAD, reviewed_by: WHO, reviewed_at: AT })}
        canReview
        busy={false}
        onConfirm={vi.fn()}
        onDecline={vi.fn()}
        refusal={null}
        onTakeLead={vi.fn()}
        onRefuseLead={vi.fn()}
        onSendLead={vi.fn()}
        onClose={null}
      />,
    );

    expect(screen.getByText(/^В работе: anna@parsingprices\.com, /)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Передать в CRM ещё раз' })).toBeInTheDocument();
  });
});
