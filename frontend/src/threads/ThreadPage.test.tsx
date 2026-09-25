/**
 * Карточка переписки: разбор цены под текстом ответа.
 *
 * Проверяется главное обещание экрана — **спорный разбор человек видит
 * рядом с исходным текстом и решает сам**. Без этой ветки приёмка
 * в 5% ошибок не держится: ровно поэтому она и есть в требованиях.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const LETTER = {
  id: 1,
  step: 0,
  status: 'delivered',
  subject: 'Advertising rates',
  body: 'Good afternoon,',
  sent_at: '2026-09-18T10:00:00+00:00',
  // Доля 0–1, как её отдаёт сервер: письмо с отличием 19%.
  uniqueness: 0.19,
};

const UNSURE = {
  id: 7,
  kind: 'human',
  raw_body: 'Hi! A post is around 250 EUR, I think. Let me double-check with the editor.',
  received_at: '2026-09-19T10:00:00+00:00',
  from_email: 'elena@donor.example.test',
  subject: 'Re: Advertising rates',
  attachments: null,
  price_white: '250',
  price_grey: null,
  currency: 'EUR',
  payment_methods: null,
  confidence: 0.4,
  placement: 'unclear',
  needs_review: true,
  reviewed_by: null,
  reviewed_at: null,
};

const VIEW = {
  card: {
    id: 3,
    host: 'donor.example.test',
    contact_email: 'editor@donor.example.test',
    campaign: 'Демонстрация',
    state: 'needs_review',
    messages_sent: 1,
    last_event_at: '2026-09-19T10:00:00+00:00',
    last_reply_at: '2026-09-19T10:00:00+00:00',
    price_white: null,
    price_grey: null,
    currency: null,
  },
  letters: [LETTER],
  incoming: [UNSURE],
  corridor: { min: 0.15, max: 0.25 },
};

async function openThread(
  view: Record<string, unknown> = {},
  routes: Record<string, unknown> = {},
  who = ADMIN,
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/threads/3': { body: { ...VIEW, ...view } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/threads/3');
  await screen.findByRole('heading', { name: 'donor.example.test' });
  return recorded;
}

describe('карточка переписки', () => {
  it('исходный текст ответа виден рядом с разобранным', async () => {
    await openThread();

    // Проверить спорный разбор можно только сравнением: карточка,
    // показывающая число вместо письма, проверке не поддаётся.
    expect(screen.getByText(/around 250 EUR/)).toBeInTheDocument();
    expect(screen.getByLabelText('Белая цена')).toHaveValue('250');
  });

  it('неуверенный разбор объясняет, почему цена не в базе', async () => {
    await openThread();

    expect(screen.getByText('Цену подтверждает человек')).toBeInTheDocument();
    expect(screen.getByText(/уверенность разбора 40%/)).toBeInTheDocument();
  });

  it('адрес ответившего виден: он может отличаться от того, кому писали', async () => {
    await openThread();

    expect(screen.getByText('от elena@donor.example.test')).toBeInTheDocument();
  });

  it('подтверждение уходит на сервер с поправленной ценой', async () => {
    const recorded = await openThread(
      {},
      {
        'PATCH /api/replies/7': {
          body: { id: 7, reviewed_by: 'админ@site.com', stored_price: true },
        },
      },
    );
    const user = userEvent.setup();

    const white = screen.getByLabelText('Белая цена');
    await user.clear(white);
    await user.type(white, '300');
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));

    await screen.findByText(/записана в карточку донора/);
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.path).toBe('/api/replies/7');
    expect((patch?.body as { price_white: string }).price_white).toBe('300');
  });

  it('подтверждение без цены говорит, что в карточку ничего не пошло', async () => {
    await openThread(
      {},
      {
        'PATCH /api/replies/7': {
          body: { id: 7, reviewed_by: 'админ@site.com', stored_price: false },
        },
      },
    );
    const user = userEvent.setup();

    await user.clear(screen.getByLabelText('Белая цена'));
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));

    expect(await screen.findByText(/в карточку донора ничего не пошло/)).toBeInTheDocument();
  });

  it('«не продаёт размещения» уходит одним нажатием, без цены', async () => {
    // Для гест-постинга это ответ на главный вопрос письма: «цены нет»
    // и «не продаём» — разные ответы, и второй убирает домен из отбора.
    const recorded = await openThread(
      {},
      {
        'PATCH /api/replies/7': {
          body: {
            id: 7,
            reviewed_by: 'админ@site.com',
            stored_price: false,
            seller_answer: 'declines',
          },
        },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не продаёт размещения' }));

    await screen.findByText(/донор не продаёт размещения/);
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.body).toMatchObject({ declines: true, price_white: null, price_grey: null });
  });

  it('у автоответчика разбирать нечего', async () => {
    await openThread({
      incoming: [
        {
          ...UNSURE,
          kind: 'auto_reply',
          raw_body: 'I am out of the office until Monday.',
          price_white: null,
          currency: null,
          confidence: null,
          needs_review: false,
        },
      ],
    });

    expect(screen.queryByRole('button', { name: 'Подтвердить' })).not.toBeInTheDocument();
  });

  it('вложение видно, а сам файл не показывается', async () => {
    await openThread({
      incoming: [
        {
          ...UNSURE,
          raw_body: 'Our rates are attached.',
          attachments: [
            { имя: 'price.pdf', байт: 9000, тип: 'application/pdf', принято: true },
            { имя: 'macro.exe', байт: 100, тип: null, принято: false },
          ],
        },
      ],
    });

    // Прайс приходит файлом чаще, чем текстом: ответ с вложением
    // не должен выглядеть пустым.
    expect(screen.getByText('price.pdf')).toBeInTheDocument();
    expect(screen.getByText('macro.exe — не принят')).toBeInTheDocument();
  });

  it('подтверждённый разбор называет, кто его подтвердил', async () => {
    await openThread({
      incoming: [{ ...UNSURE, needs_review: false, reviewed_by: 'оператор@site.com' }],
    });

    expect(screen.getByText('подтвердил оператор@site.com')).toBeInTheDocument();
    expect(screen.queryByText('Цену подтверждает человек')).not.toBeInTheDocument();
  });

  it('без права подтверждать поля видны, но не нажимаются', async () => {
    await openThread({}, {}, { ...OPERATOR, permissions: ['view'] });

    expect(screen.getByLabelText('Белая цена')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Подтвердить' })).toBeDisabled();
    expect(screen.getByText(/отдельное действие/)).toBeInTheDocument();
  });

  it('состояние «ждёт разбора» видно в шапке', async () => {
    await openThread();

    const header = screen.getByRole('heading', { name: 'donor.example.test' }).parentElement;
    expect(within(header as HTMLElement).getByText('ждёт разбора')).toBeInTheDocument();
  });
});

/** Учётка, у которой право разбирать ответы отобрано точечно. */
const OPERATOR_WITHOUT_PRICES = {
  ...OPERATOR,
  permissions: OPERATOR.permissions.filter((one) => one !== 'prices'),
};

const LEAD = {
  ...UNSURE,
  id: 21,
  raw_body: 'Interesting. We pay about $300 per article at the moment.',
  from_email: 'marketing@brand.example.test',
  price_white: null,
  currency: null,
  confidence: null,
  placement: null,
  needs_review: false,
  lead: true,
};

const LEAD_VIEW = {
  card: {
    ...VIEW.card,
    stage: 'advertisers',
    state: 'lead',
    campaign: 'Сентябрь',
  },
  letters: [LETTER],
  incoming: [LEAD],
};

describe('ответ рекламодателя', () => {
  it('это лид: вместо формы цены — одно действие', async () => {
    await openThread(LEAD_VIEW);

    // «Мы платим $300» — его расход, а не цена площадки: формы разбора нет.
    expect(screen.queryByLabelText('Белая цена')).not.toBeInTheDocument();
    expect(screen.getByText(/цену в нём не разбираем/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Взять в работу' })).toBeInTheDocument();
    expect(screen.getByText(/· рекламодатель/)).toBeInTheDocument();
  });

  it('«взять в работу» уходит на сервер', async () => {
    const recorded = await openThread(LEAD_VIEW, {
      'POST /api/replies/21/lead': {
        body: { id: 21, reviewed_by: 'админ@site.com', reviewed_at: '2026-09-24T16:00:00Z' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Взять в работу' }));

    expect(recorded.calls.some((call: Call) => call.path === '/api/replies/21/lead')).toBe(true);
  });

  it('взятый лид говорит, кто его ведёт, и кнопки больше нет', async () => {
    await openThread({
      ...LEAD_VIEW,
      card: { ...LEAD_VIEW.card, state: 'lead_taken' },
      incoming: [
        { ...LEAD, reviewed_by: 'anna@parsingprices.com', reviewed_at: '2026-09-24T16:00:00Z' },
      ],
    });

    expect(screen.getByText(/В работе: anna@parsingprices\.com/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument();
  });

  it('без права разбирать ответы лид виден, а кнопки нет', async () => {
    await openThread(LEAD_VIEW, {}, OPERATOR_WITHOUT_PRICES);

    expect(screen.getByText(/цену в нём не разбираем/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument();
  });
});

describe('числа в переписке', () => {
  it('отличие письма — долей с сервера и с коридором с сервера', async () => {
    // Сервер отдаёт долю 0–1. Раньше карточка читала её процентами и
    // печатала «0%» у письма с отличием 19%, а коридор был вшит.
    await openThread({ corridor: { min: 0.1, max: 0.3 } });

    expect(screen.getByText('Отличие от шаблона 19% — коридор 10–30%')).toBeInTheDocument();
  });

  it('разобранная цена — деньгами: разряды, запятая, знак валюты', async () => {
    await openThread({
      incoming: [{ ...UNSURE, price_white: '1250.00', price_grey: '180.00' }],
    });

    expect(screen.getByText('белая 1 250,00 €')).toBeInTheDocument();
    expect(screen.getByText('серая 180,00 €')).toBeInTheDocument();
    expect(screen.queryByText(/1250\.00 EUR|180\.00 EUR/)).not.toBeInTheDocument();
  });
});
