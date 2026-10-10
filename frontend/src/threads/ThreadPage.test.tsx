/**
 * Карточка переписки: разбор цены под текстом ответа.
 *
 * Проверяется главное обещание экрана — **спорный разбор человек видит
 * рядом с исходным текстом и решает сам**. Без этой ветки приёмка
 * в 5% ошибок не держится: ровно поэтому она и есть в требованиях.
 */

import { cleanup, screen, waitFor, within } from '@testing-library/react';
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
  answers_reply_id: null,
};

const UNSURE = {
  id: 7,
  kind: 'human',
  raw_body: 'Hi! A post is around 250 EUR, I think. Let me double-check with the editor.',
  received_at: '2026-09-19T10:00:00+00:00',
  from_email: 'elena@donor.example.test',
  subject: 'Re: Advertising rates',
  attachments: [],
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

    // Адрес ответившего — шапка его пузыря в ленте.
    expect(
      screen.getByRole('article', { name: 'Ответ elena@donor.example.test' }),
    ).toBeInTheDocument();
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

  it('не цена — отказ сервера над полями, вписанное уходит как есть и остаётся', async () => {
    // Проверка QA 10.10.2026: «−5 EUR» ложилось ценой в карточку донора, а запятую
    // экран сам менял на точку — «1,200» уходило как 1,20.
    const refusal = '«-5» — не цена: впишите число больше нуля, например 150 или 150.50.';
    const recorded = await openThread(
      {},
      { 'PATCH /api/replies/7': { status: 400, body: { detail: refusal } } },
    );
    const user = userEvent.setup();
    const white = screen.getByLabelText('Белая цена');
    const grey = screen.getByLabelText('Серая цена');

    // Цифры — с цифровой клавиатурой на телефоне, как у «Указать цену».
    expect(white).toHaveAttribute('inputmode', 'decimal');
    await user.clear(white);
    await user.type(white, '-5');
    await user.type(grey, '1,200');
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));

    expect(await screen.findByText(`— ${refusal}`)).toBeInTheDocument();
    expect(screen.getByText('Не подтвердили')).toBeInTheDocument();
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.body).toMatchObject({ price_white: '-5', price_grey: '1,200', currency: 'EUR' });
    expect(white).toHaveValue('-5');
  });

  it('«не продаёт размещения» — только после подтверждения у кнопки, без цены', async () => {
    // Для гест-постинга это ответ на главный вопрос письма: «цены нет»
    // и «не продаём» — разные ответы, и второй убирает домен из отбора на год.
    // Кнопка стоит вплотную к «Подтвердить»: до 10.10.2026 промах срабатывал сразу.
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
    const ask = await screen.findByRole('dialog', { name: /донор не продаёт/, hidden: true });
    expect(ask).toHaveTextContent('Домен уйдёт из отбора на год');
    expect(recorded.calls.some((call: Call) => call.method === 'PATCH')).toBe(false);
    // Выпадающее окно Mantine в jsdom — `display: none`: без `hidden` кнопок не видно.
    await user.click(within(ask).getByRole('button', { name: 'Отметить', hidden: true }));

    await screen.findByText(/донор не продаёт размещения — домен уходит/);
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.body).toMatchObject({ declines: true, price_white: null, price_grey: null });
  });

  it('«не продаёт размещения»: передумал — «Отмена», и на сервер ничего не ушло', async () => {
    const recorded = await openThread();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не продаёт размещения' }));
    const ask = await screen.findByRole('dialog', { name: /донор не продаёт/, hidden: true });
    await user.click(within(ask).getByRole('button', { name: 'Отмена', hidden: true }));

    expect(recorded.calls.some((call: Call) => call.method === 'PATCH')).toBe(false);
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

  it('все цены из ответа видны под ценой, пока человек решает', async () => {
    await openThread({
      incoming: [
        {
          ...UNSURE,
          offers: [
            { product: 'guest post', niche: null, price: '250', currency: 'EUR', period: null },
            {
              product: 'homepage link',
              niche: 'casino',
              price: '400',
              currency: 'EUR',
              period: 'month',
            },
          ],
        },
      ],
    });

    const rows = within(screen.getByRole('list', { name: 'Все цены из ответа' })).getAllByRole(
      'listitem',
    );
    expect(rows.map((row) => row.textContent?.replace(/[\u00a0\u202f]/g, ' '))).toEqual([
      'guest post — 250,00 €',
      'homepage link · casino — 400,00 € в месяц',
    ]);
    // Форма решения — рядом и та же: список её не заменяет.
    expect(screen.getByLabelText('Белая цена')).toHaveValue('250');
  });

  it('вложение видно, а сам файл не показывается', async () => {
    await openThread({
      incoming: [
        {
          ...UNSURE,
          raw_body: 'Our rates are attached.',
          attachments: [
            {
              id: 11,
              name: 'price.pdf',
              size: 9000,
              content_type: 'application/pdf',
              accepted: true,
              reason: null,
            },
            {
              id: 12,
              name: 'macro.exe',
              size: 100,
              content_type: null,
              accepted: false,
              reason: '«.exe» — исполняемый файл или скрипт, такие не принимаются',
            },
          ],
        },
      ],
    });

    // Прайс приходит файлом чаще, чем текстом: ответ с вложением
    // не должен выглядеть пустым. Скачать можно только сохранённый.
    expect(screen.getByText('price.pdf')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Скачать price.pdf' })).toBeInTheDocument();
    expect(screen.getByText('macro.exe')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скачать macro.exe' })).not.toBeInTheDocument();
    expect(screen.getByText('не сохранён')).toBeInTheDocument();
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
        body: {
          id: 21,
          reviewed_by: 'админ@site.com',
          reviewed_at: '2026-09-24T16:00:00Z',
          handoff: 'queued',
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Взять в работу' }));

    expect(recorded.calls.some((call: Call) => call.path === '/api/replies/21/lead')).toBe(true);
    expect(await screen.findByText('Лид взят в работу и передаётся в CRM')).toBeInTheDocument();
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

  it('взятый лид можно передать в CRM ещё раз', async () => {
    const recorded = await openThread(
      {
        ...LEAD_VIEW,
        card: { ...LEAD_VIEW.card, state: 'lead_taken' },
        incoming: [
          { ...LEAD, reviewed_by: 'anna@parsingprices.com', reviewed_at: '2026-09-24T16:00:00Z' },
        ],
      },
      { 'POST /api/replies/21/lead/send': { body: { id: 21, job_id: 'lead-21-20261004' } } },
    );
    const user = userEvent.setup();

    // Взятый лид не ждёт решения: его разбор под лентой открывает кнопка в пузыре.
    await user.click(screen.getByRole('button', { name: 'Передача лида' }));
    await user.click(screen.getByRole('button', { name: 'Передать в CRM ещё раз' }));

    await waitFor(() =>
      expect(recorded.calls.some((call: Call) => call.path === '/api/replies/21/lead/send')).toBe(
        true,
      ),
    );
    expect(await screen.findByText('Передача в CRM поставлена')).toBeInTheDocument();
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

describe('номер диалога из адреса', () => {
  it('не номер — «такого диалога нет» без запроса к серверу, и ссылка к списку', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({ 'GET /api/auth/me': { body: ADMIN } });
    renderWith(<AppRoutes />, '/threads/abc');

    expect(await screen.findByRole('heading', { name: 'Такого диалога нет' })).toBeVisible();
    expect(screen.getByText(/«abc» в адресе — не номер диалога/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'К списку' })).toHaveAttribute('href', '/threads');
    expect(recorded.calls.some((call: Call) => call.path.startsWith('/api/threads'))).toBe(false);
  });

  it('нет в базе — те же слова, что и у сервера', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads/9999': { status: 404, body: { detail: 'Диалога №9999 нет' } },
    });
    renderWith(<AppRoutes />, '/threads/9999');

    expect(await screen.findByRole('heading', { name: 'Такого диалога нет' })).toBeVisible();
    expect(screen.getByText(/Диалога №9999 нет/)).toBeInTheDocument();
  });
});

describe('ответ собеседнику', () => {
  it('на ответ человека можно ответить — письмо уходит на сервер', async () => {
    const recorded = await openThread(VIEW, {
      'POST /api/threads/3/answer': {
        body: { id: 9, sender_email: 'anna@mail.test', real: true },
      },
    });
    const user = userEvent.setup();

    // Поле — сразу внизу ленты, без кнопки «Ответить» (замечание Anthony 10.10.2026).
    await user.type(screen.getByLabelText('Текст ответа'), 'Thanks! Which topics?');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() =>
      expect(recorded.calls.some((call: Call) => call.path === '/api/threads/3/answer')).toBe(true),
    );
    const call = recorded.calls.find((sent: Call) => sent.path === '/api/threads/3/answer');
    expect(call?.body).toEqual({ reply_id: 7, body: 'Thanks! Which topics?', file_ids: [] });
    expect(await screen.findByText('Ответ отправлен с anna@mail.test')).toBeInTheDocument();
  });

  it('пустой ответ не отправить — ни кнопкой, ни Ctrl+Enter', async () => {
    const recorded = await openThread(VIEW);
    const user = userEvent.setup();

    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
    await user.type(screen.getByLabelText('Текст ответа'), '   {Control>}{Enter}{/Control}');

    expect(recorded.calls.some((call: Call) => call.path === '/api/threads/3/answer')).toBe(false);
  });

  it('Ctrl+Enter отправляет, Enter — новая строка: ответ — письмо, а не реплика', async () => {
    const recorded = await openThread(VIEW, {
      'POST /api/threads/3/answer': {
        body: { id: 9, sender_email: 'anna@mail.test', real: true },
      },
    });
    const user = userEvent.setup();
    const field = screen.getByLabelText('Текст ответа');

    await user.type(field, 'Hello,{Enter}which topics?');
    expect(recorded.calls.some((call: Call) => call.path === '/api/threads/3/answer')).toBe(false);
    await user.type(field, '{Control>}{Enter}{/Control}');

    await waitFor(() =>
      expect(recorded.calls.some((call: Call) => call.path === '/api/threads/3/answer')).toBe(true),
    );
    const call = recorded.calls.find((sent: Call) => sent.path === '/api/threads/3/answer');
    expect(call?.body).toMatchObject({ body: 'Hello,\nwhich topics?' });
  });

  it('набранный ответ переживает уход из переписки и стирается, когда ответ ушёл', async () => {
    await openThread(VIEW);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Текст ответа'), 'Half a thought');
    cleanup();

    // Тот же диалог открыт заново (J/K, «Следующий ждущий», строка списка).
    await openThread(VIEW);
    expect(screen.getByLabelText('Текст ответа')).toHaveValue('Half a thought');
    cleanup();

    await openThread({
      letters: [LETTER, { ...LETTER, id: 9, step: 100, answers_reply_id: 7 }],
    });
    expect(sessionStorage.getItem('outreach.answer.3')).toBeNull();
  });

  it('без права отправлять поля ответа нет', async () => {
    await openThread(VIEW, {}, OPERATOR);

    expect(screen.queryByLabelText('Текст ответа')).not.toBeInTheDocument();
    expect(screen.queryByText(/с того же ящика/)).not.toBeInTheDocument();
  });

  it('с какого ящика уйдёт ответ — описание самого поля, а не карточка внизу', async () => {
    await openThread(VIEW);

    // До 06.10.2026 внизу переписки всегда стояло «ответ из карточки
    // появится вместе с подключением почты» — и после того, как ответ
    // заработал.
    expect(screen.queryByText(/вместе с подключением почты/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Текст ответа')).toHaveAccessibleDescription(
      /с того же ящика, что вёл переписку/,
    );
    expect(screen.getByRole('button', { name: 'Как уйдёт ответ' })).toBeInTheDocument();
  });

  it('отвеченный ответ говорит об этом, а наше письмо подписано «наш ответ»', async () => {
    await openThread({
      letters: [
        LETTER,
        { ...LETTER, id: 9, step: 100, subject: 'Re: Advertising rates', answers_reply_id: 7 },
      ],
    });

    expect(screen.getByText(/Ответили — наше письмо в ленте выше/)).toBeInTheDocument();
    expect(screen.getByText('наш ответ')).toBeInTheDocument();
    expect(screen.queryByLabelText('Текст ответа')).not.toBeInTheDocument();
  });
});

describe('ящик переписки', () => {
  // Полдень по UTC — та же дата в любом поясе, где идёт тест.
  const MAIL = {
    mailbox: 'outreach@mail-one.example.test',
    waiting: null,
    next_step: 1,
    next_at: '2026-10-10T12:00:00+00:00',
  };

  it('карточка называет ящик переписки и срок следующей добивки', async () => {
    await openThread({ mail: MAIL });

    expect(
      screen.getByText(/^Пишет outreach@mail-one\.example\.test · добивка 1 — 10\.10\.2026/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Письма переписки ждут свой ящик')).not.toBeInTheDocument();
  });

  it('ящик не пишет — почему ждут, видно до клика, словами сервера', async () => {
    // Прежде причина жила только в журнале прохода добивок: человек узнавал
    // её, нажав «Ответить» и получив отказ.
    const waiting =
      'Ящик outreach@mail-one.example.test сейчас не пишет (на паузе: доля отказов 7%)';
    await openThread({ mail: { ...MAIL, waiting } });

    expect(screen.getByText('Письма переписки ждут свой ящик')).toBeInTheDocument();
    expect(screen.getByText(waiting)).toBeInTheDocument();
  });

  it('первое письмо ещё не уходило — ящика нет, и строки нет', async () => {
    await openThread({ mail: null });

    expect(screen.queryByText(/^Пишет /)).not.toBeInTheDocument();
    // Ящик выберется при отправке — отвечать можно.
    expect(screen.getByLabelText('Текст ответа')).toBeEnabled();
  });

  it('ящик переписки удалён — вместо поля ответа причина, отправить нечем', async () => {
    // Проверка QA 10.10.2026: шапка говорила «Ящик переписки удалён», а поле и
    // «Отправить» работали — отказ приходил только после Ctrl+Enter.
    const gone = 'Ящика первого письма нет: им начата переписка, а его удалили.';
    await openThread({ mail: { ...MAIL, mailbox: null, waiting: gone, next_step: null } });

    expect(screen.getByText('Ящик переписки удалён')).toBeInTheDocument();
    expect(
      screen.getByText(/^Ответ отсюда не уйдёт: ящик, которым начата переписка, удалён/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Текст ответа')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Отправить' })).not.toBeInTheDocument();
  });
});

describe('лента переписки', () => {
  // Замечание Anthony 08.10.2026 по первому настоящему ответу донора: переписка
  // полными карточками читалась простынёй — цитата нашего письма, хвост подписи
  // с трекинговыми ссылками, форма цены у каждого ответа.
  const QUOTED = {
    ...UNSURE,
    raw_body:
      'Guest post is 525 USD.\n\nBest regards,\nNellie\n\n' +
      'On Thu, Oct 8, 2026 at 3:09 PM Alex <alex@mail.example.test> wrote:\n' +
      '> Before I share anything, could you confirm the fee?\n',
    fresh_body: 'Guest post is 525 USD.\n\nBest regards,\nNellie',
  };

  it('наши письма справа, письма собеседника слева', async () => {
    await openThread();

    const ours = screen.getByRole('article', { name: 'Наше письмо: первое письмо' });
    const theirs = screen.getByRole('article', { name: 'Ответ elena@donor.example.test' });
    expect(ours).toHaveClass('bubbleOurs');
    expect(ours.parentElement).toHaveClass('bubbleRowOurs');
    expect(theirs).toHaveClass('bubbleTheirs');
    expect(theirs.parentElement).toHaveClass('bubbleRowTheirs');
  });

  it('в ответе — написанное человеком, цитата и подпись — по раскрытию', async () => {
    await openThread({ incoming: [QUOTED] });
    const user = userEvent.setup();

    const reply = screen.getByRole('article', { name: 'Ответ elena@donor.example.test' });
    expect(within(reply).getByText(/Guest post is 525 USD/)).toBeInTheDocument();
    expect(within(reply).queryByText(/could you confirm the fee/)).not.toBeInTheDocument();

    await user.click(within(reply).getByRole('button', { name: 'Показать цитату и подпись' }));

    expect(within(reply).getByText(/could you confirm the fee/)).toBeInTheDocument();
    await user.click(within(reply).getByRole('button', { name: 'Скрыть цитату и подпись' }));
    expect(within(reply).queryByText(/could you confirm the fee/)).not.toBeInTheDocument();
  });

  it('отрезать нечего — кнопки цитаты нет', async () => {
    await openThread();

    expect(screen.queryByRole('button', { name: 'Показать цитату и подпись' })).toBeNull();
  });

  it('галочки — как в почте, отказ доставки — словами', async () => {
    await openThread({
      letters: [
        LETTER,
        { ...LETTER, id: 2, step: 1, status: 'sent', sent_at: '2026-09-21T10:00:00+00:00' },
        { ...LETTER, id: 4, step: 2, status: 'bounced', sent_at: '2026-09-24T10:00:00+00:00' },
      ],
    });

    const first = screen.getByRole('article', { name: 'Наше письмо: первое письмо' });
    const second = screen.getByRole('article', { name: 'Наше письмо: добивка 1' });
    const third = screen.getByRole('article', { name: 'Наше письмо: добивка 2' });
    expect(within(first).getByLabelText('доставлено')).toBeInTheDocument();
    expect(within(second).getByLabelText('принято платформой')).toBeInTheDocument();
    expect(within(third).getByText('отказ доставки')).toBeInTheDocument();
  });

  it('письмо, которое ещё не ушло, — в конце ленты, а не над первым', async () => {
    await openThread({
      letters: [
        LETTER,
        { ...LETTER, id: 9, step: 100, status: 'queued', sent_at: null, answers_reply_id: 7 },
      ],
    });

    const bubbles = screen.getAllByRole('article');
    expect(bubbles.at(-1)).toHaveAccessibleName('Наше письмо: наш ответ');
    expect(within(bubbles.at(-1) as HTMLElement).getByText('в очереди')).toBeInTheDocument();
  });

  it('форма цены — одна, под лентой, у ответа, который ждёт человека', async () => {
    await openThread();

    expect(screen.getAllByLabelText('Белая цена')).toHaveLength(1);
    const reply = screen.getByRole('article', { name: 'Ответ elena@donor.example.test' });
    expect(reply).toHaveClass('bubbleActive');
    // Ждущий ответ свернуть нельзя: решение по нему ещё не принято.
    expect(screen.queryByRole('button', { name: 'Свернуть' })).toBeNull();
  });

  it('разобранный ответ формы не держит — её открывает «Поправить цену» и закрывает «Свернуть»', async () => {
    await openThread({
      incoming: [
        {
          ...UNSURE,
          needs_review: false,
          confidence: 0.92,
          reviewed_by: 'anna@parsingprices.com',
          reviewed_at: '2026-09-19T12:00:00+00:00',
        },
      ],
    });
    const user = userEvent.setup();

    expect(screen.queryByLabelText('Белая цена')).toBeNull();
    expect(screen.getByText('подтвердил anna@parsingprices.com')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Поправить цену' }));

    expect(screen.getByLabelText('Белая цена')).toHaveValue('250');
    await user.click(screen.getByRole('button', { name: 'Свернуть' }));
    expect(screen.queryByLabelText('Белая цена')).toBeNull();
  });
});
