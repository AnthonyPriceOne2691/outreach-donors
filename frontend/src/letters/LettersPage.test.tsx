/**
 * Экран писем: что видно до нажатия и что уходит на сервер.
 *
 * Проверяется не «отрисовалось», а три обещания экрана: текст письма
 * виден целиком, отправка не нажимается, пока её что-то блокирует,
 * и ненастоящий транспорт называет себя вслух. Все три — про то, чтобы
 * человек не узнал о препятствии после нажатия.
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
  id: 7,
  host: 'digest-weekly.example.test',
  email: 'editor@digest-weekly.example.test',
  campaign: 'Демонстрация',
  status: 'queued',
  subject: 'Advertising rates for digest-weekly.example.test',
  body: 'Good afternoon to you,\n\nI have been reading digest-weekly.example.test.\n\nBest regards,\nAnna Ro',
  uniqueness: 0.19,
  verdict: null,
  followups: [
    {
      step: 1,
      subject: 'Advertising rates for digest-weekly.example.test',
      body: 'Hi again,\n\nI wrote to you last week about digest-weekly.example.test.',
      in_days: 7,
    },
    {
      step: 2,
      subject: 'Advertising rates for digest-weekly.example.test',
      body: 'Hello,\n\nThis is my last note about digest-weekly.example.test.',
      in_days: 14,
    },
  ],
};

const OFF_CORRIDOR = {
  ...LETTER,
  id: 8,
  host: 'city-news.example.test',
  email: 'info@city-news.example.test',
  uniqueness: 0.04,
  verdict: 'отличие 4% ниже коридора 15–25%: письмо слишком похоже на шаблон',
};

const LETTER_DEFAULT = {
  subject: 'Guest article on {{host}}',
  zones: [
    { name: 'greeting', kind: 'rewrite', title: 'Приветствие', text: 'Hi,' },
    { name: 'ask', kind: 'rewrite', title: 'Вопросы', text: 'Could you let me know:\n1. Price?' },
    { name: 'terms', kind: 'fixed', title: 'Условия', text: 'We pay promptly.' },
    { name: 'signature', kind: 'fixed', title: 'Подпись', text: 'Best regards,\n{{sender_name}}' },
  ],
};

/** Прогон, в котором кого-то приняли: из него и собирается рассылка. */
const RUN_WITH_ACCEPTED = {
  id: 18,
  status: 'done',
  country: 'us',
  keywords: 100,
  estimated_units: 14983,
  actual_units: 16998,
  estimate_error: 0.134,
  stats: null,
  started_at: '2026-09-23T16:21:00Z',
  alive_at: '2026-09-23T16:40:00Z',
  hosts: 535,
  reviewed: 0,
  disagreements: 0,
  queue: { pending: 380, accepted: 14 },
  reason: null,
};

/** Задача сборки, как её отдаёт сервер сразу после постановки. */
const BUILD_JOB = {
  job_id: 'j',
  kind: 'сборка писем',
  state: 'queued',
  title: 'в очереди',
  error: null,
  report: null,
  retries_left: 3,
  next_try_at: null,
  ended_at: null,
};

const VIEW = {
  letters: [LETTER, OFF_CORRIDOR],
  followup_default: [7, 14],
  letter_default: LETTER_DEFAULT,
  blocked_by: [],
  transport: { name: 'null', real: false, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 12, 'с адресом': 9, 'вне стоп-листа': 9, 'ещё не писали': 2 },
};

async function openLetters(
  view: Record<string, unknown> = {},
  routes: Record<string, unknown> = {},
  who = ADMIN,
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/letters': { body: { ...VIEW, ...view } },
    'GET /api/runs/with-accepted': { body: [RUN_WITH_ACCEPTED] },
    // После «Собрать очередь» строка задачи сама спрашивает её исход.
    'GET /api/jobs/j': { body: BUILD_JOB },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/letters');
  await screen.findByRole('heading', { name: 'Письма' });
  return recorded;
}

describe('цепочка писем', () => {
  it('добивки видны здесь же, а не приходят сюрпризом от донора', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Добивка 1' }));

    expect(screen.getByText(/Hi again/)).toBeInTheDocument();
    expect(screen.getByText(/через 7 дн/)).toBeInTheDocument();
  });

  it('добивку не правят и не отправляют руками', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Добивка 2' }));

    // Текст добивки один на всю рассылку и живёт шаблоном в коде;
    // уходит она по сроку, а не по кнопке.
    expect(screen.queryByRole('button', { name: 'Отправить' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Поправить' })).not.toBeInTheDocument();
  });

  it('смена письма возвращает к первому', async () => {
    await openLetters();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Добивка 1' }));

    await user.click(screen.getByText('city-news.example.test'));

    expect(screen.getByRole('tab', { name: 'Первое письмо' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('сроки добивок уходят вместе с рассылкой', async () => {
    const recorded = await openLetters(
      {},
      { 'POST /api/letters/build': { body: { job_id: 'j' } } },
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Кампания'), 'Май');
    await user.click(await screen.findByLabelText(/№18/));
    await user.clear(screen.getByLabelText('Добивка 1, дней'));
    await user.type(screen.getByLabelText('Добивка 1, дней'), '3');
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    const call = recorded.calls.find((one: Call) => one.path === '/api/letters/build');
    expect(call?.body).toMatchObject({ campaign: 'Май', followup_days: [3, 14], run_ids: [18] });
  });
});

describe('прогоны рассылки', () => {
  it('в выборе все прогоны с принятыми, а не первая страница истории', async () => {
    // История прогонов с 25.09.2026 отдаётся страницами по десять. Выбор
    // рассылки брал тот же список и молча потерял бы старые прогоны
    // с принятыми донорами; теперь у него свой запрос.
    const ready = Array.from({ length: 12 }, (_, index) => ({
      ...RUN_WITH_ACCEPTED,
      id: 30 - index,
      queue: { accepted: index + 1 },
    }));
    const recorded = await openLetters({}, { 'GET /api/runs/with-accepted': { body: ready } });

    expect(await screen.findByLabelText(/№19 /)).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox', { name: /^№\d+ · / })).toHaveLength(12);
    expect(recorded.calls.some((call: Call) => call.path.startsWith('/api/runs?'))).toBe(false);
  });
});

describe('очередь писем', () => {
  it('текст письма виден целиком, а не в виде сводки', async () => {
    await openLetters();

    // Смысл экрана в том, что спорное решение видит человек: пересказ
    // письма вместо письма этот смысл отменяет.
    expect(screen.getByText(/Best regards/)).toBeInTheDocument();
    expect(screen.getByText(/Good afternoon to you/)).toBeInTheDocument();
  });

  it('ненастоящий транспорт называет себя до нажатия', async () => {
    await openLetters();

    // Письмо, помеченное отправленным и никуда не ушедшее, выглядит
    // как работа — и это самая дорогая ложь на экране.
    expect(screen.getByText('Наружу письмо не уйдёт')).toBeInTheDocument();
  });

  it('незаполненная обязательная настройка блокирует кнопку', async () => {
    await openLetters({ blocked_by: ['OUTREACH_SENDER_NAME'] });

    // Наверху — почему так, у кнопки — почему она не нажимается. Разными
    // словами: два одинаковых предупреждения на экране читаются как сбой.
    // Имя настройки в окружении человеку на экране ничего не говорит.
    expect(screen.getByText(/Не задано: имя отправителя/)).toBeInTheDocument();
    expect(screen.queryByText(/OUTREACH_SENDER_NAME/)).not.toBeInTheDocument();
    expect(screen.getByText(/Кнопка не нажимается/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
  });

  it('незаданное имя отправителя в письме — тихая пометка, а не крик', async () => {
    await openLetters({
      blocked_by: ['OUTREACH_SENDER_NAME'],
      letters: [{ ...LETTER, body: 'Best regards,\n«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО»' }],
    });

    expect(screen.getByText(/\[имя отправителя\]/)).toBeInTheDocument();
    expect(screen.queryByText(/НЕ ЗАДАНО/)).not.toBeInTheDocument();
    expect(screen.getByText(/подставится при подключении почты/)).toBeInTheDocument();
  });

  it('отличие вне коридора объясняется словами, а не только цветом', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.click(screen.getByText('city-news.example.test'));

    expect(screen.getByText(/ниже коридора 15–25%/)).toBeInTheDocument();
  });

  it('отправка уходит по одному письму', async () => {
    const recorded = await openLetters(
      { blocked_by: [] },
      {
        'POST /api/letters/7/send': {
          body: { id: 7, sender_email: 'outreach1@mail-alpha.example.test', real: false },
        },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await screen.findByText(/наружу НЕ ушло/);
    const posts = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0]?.path).toBe('/api/letters/7/send');
  });

  it('правка отправляет новый текст и показывает пересчитанный процент', async () => {
    const recorded = await openLetters(
      {},
      {
        'PATCH /api/letters/7': { body: { ...LETTER, uniqueness: 0.22 } },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Поправить' }));
    const field = screen.getByLabelText('Текст письма');
    await user.clear(field);
    await user.type(field, 'Другое письмо целиком');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await screen.findByText(/отличие от шаблона — 22%/);
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.path).toBe('/api/letters/7');
  });

  it('«не писать» предупреждает, что донор больше не появится', async () => {
    await openLetters(
      {},
      { 'POST /api/letters/7/skip': { body: { ...LETTER, status: 'stopped' } } },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не писать' }));

    expect(await screen.findByText(/в следующей сборке не появится/)).toBeInTheDocument();
  });

  it('пустая очередь объясняет, где кончились доноры', async () => {
    await openLetters({
      letters: [],
      funnel: { подходящих: 12, 'с адресом': 0, 'ещё не писали': 0 },
    });

    // Пустая очередь при «всем написали» и при «ни у кого нет адреса»
    // выглядит одинаково, а действия из них следуют разные.
    expect(screen.getByText('Очередь пуста')).toBeInTheDocument();
    expect(screen.getByText(/пора добрать контакты/)).toBeInTheDocument();
  });

  it('без права на отправку очередь видна, а кнопки нет', async () => {
    await openLetters({}, {}, OPERATOR);

    // Донор виден дважды и это норма: строка очереди и заголовок письма.
    expect(screen.getAllByText('digest-weekly.example.test')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
    expect(screen.getByText(/отдельное право/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Собрать очередь' })).not.toBeInTheDocument();
  });

  it('коридор берётся с сервера, а не из числа на фронте', async () => {
    await openLetters({ corridor: { min: 0.1, max: 0.4 } });

    const tile = screen.getByText('Вне коридора').closest('div');
    expect(within(tile as HTMLElement).getByText('коридор 10–40%')).toBeInTheDocument();
  });
});

describe('текст первого письма', () => {
  it('нетронутый текст не уходит на сервер', async () => {
    const recorded = await openLetters(
      {},
      { 'POST /api/letters/build': { body: { job_id: 'j' } } },
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Кампания'), 'Май');
    await user.click(await screen.findByLabelText(/№18/));
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    // Иначе одноимённая рассылка упиралась бы в «у неё уже свой текст».
    const call = recorded.calls.find((one: Call) => one.path === '/api/letters/build');
    expect(call?.body).not.toHaveProperty('letter');
  });

  it('поправленная зона уходит вместе с рассылкой', async () => {
    const recorded = await openLetters(
      {},
      { 'POST /api/letters/build': { body: { job_id: 'j' } } },
    );
    const user = userEvent.setup();

    const toggle = screen.getByRole('button', { name: 'Текст первого письма' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    const terms = screen.getByLabelText('Условия');
    await user.clear(terms);
    await user.type(terms, 'We pay within 48 hours.');
    expect(screen.getByText('поправлен')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Кампания'), 'Май');
    await user.click(await screen.findByLabelText(/№18/));
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    const call = recorded.calls.find((one: Call) => one.path === '/api/letters/build');
    expect(call?.body).toMatchObject({
      campaign: 'Май',
      letter: {
        subject: 'Guest article on {{host}}',
        zones: { terms: 'We pay within 48 hours.', greeting: 'Hi,' },
      },
    });
  });

  it('каждая зона говорит, что с ней сделает модель', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Текст первого письма' }));

    expect(screen.getAllByText('Переписывает модель под каждого донора')).toHaveLength(2);
    expect(screen.getAllByText('Уходит как есть, модель не видит')).toHaveLength(2);
  });

  it('исходный текст возвращается одной кнопкой', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Текст первого письма' }));
    const reset = screen.getByRole('button', { name: 'Вернуть исходный текст' });
    expect(reset).toBeDisabled();

    await user.type(screen.getByLabelText('Условия'), ' Always.');
    expect(reset).toBeEnabled();
    await user.click(reset);

    expect(screen.getByLabelText('Условия')).toHaveValue('We pay promptly.');
    expect(screen.getByText('по умолчанию')).toBeInTheDocument();
  });
});

describe('прогоны рассылки', () => {
  it('без выбранного прогона собрать нельзя', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Кампания'), 'Май');

    // Рассылка — из принятых доноров выбранных прогонов, а не из всей базы.
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(await screen.findByLabelText(/№18.*принято 14/)).toBeInTheDocument();
  });
});

const OFFER = {
  ...LETTER,
  id: 21,
  host: 'brand.example.test',
  email: 'marketing@brand.example.test',
  campaign: 'Сентябрь',
  subject: 'Your placement on donor.example.test',
  body: 'Hello there,\n\nIt was your link on donor.example.test, anchored "best CRM".',
};

const OFFER_VIEW = {
  ...VIEW,
  stage: 'advertisers',
  letters: [OFFER],
  letter_default: {
    subject: 'Your placement on {{donor_host}}',
    zones: [
      { name: 'greeting', kind: 'rewrite', title: 'Приветствие', text: 'Hello there,' },
      {
        name: 'offer',
        kind: 'fixed',
        title: 'Кто мы',
        text: 'It was your link on {{donor_host}}, anchored "{{anchor}}", on {{page_url}}.',
      },
      {
        name: 'signature',
        kind: 'fixed',
        title: 'Подпись',
        text: 'Best regards,\n{{sender_name}}',
      },
    ],
  },
  funnel: {
    рекламодателей: 5,
    'со ссылкой': 5,
    'цена донора свежая': 0,
    'с адресом': 0,
    'вне стоп-листа': 0,
    'ещё не писали': 0,
  },
};

describe('этапы рассылки', () => {
  it('рекламодатели — своя очередь, и оффер не спутать с вопросом донору', async () => {
    const recorded = await openLetters(
      {},
      { 'GET /api/letters?stage=advertisers': { body: OFFER_VIEW } },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));

    expect(await screen.findAllByText('brand.example.test')).not.toHaveLength(0);
    expect(screen.queryByText('digest-weekly.example.test')).not.toBeInTheDocument();
    expect(
      recorded.calls.some((call: Call) => call.path === '/api/letters?stage=advertisers'),
    ).toBe(true);
  });

  it('у рекламодателей нет прогонов: собрать можно без них, и этап уходит на сервер', async () => {
    const recorded = await openLetters(
      {},
      {
        'GET /api/letters?stage=advertisers': { body: OFFER_VIEW },
        'POST /api/letters/build': { body: { job_id: 'j' } },
      },
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));
    await screen.findAllByText('brand.example.test');

    await user.type(screen.getByLabelText('Кампания'), 'Сентябрь');

    expect(screen.queryByText('Прогоны рассылки')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));
    const call = recorded.calls.find((one: Call) => one.path === '/api/letters/build');
    expect(call?.body).toMatchObject({ campaign: 'Сентябрь', stage: 'advertisers' });
    expect(call?.body).not.toHaveProperty('run_ids');
  });

  it('пустая очередь рекламодателей называет ступень, на которой они кончились', async () => {
    await openLetters(
      {},
      { 'GET /api/letters?stage=advertisers': { body: { ...OFFER_VIEW, letters: [] } } },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));

    expect(await screen.findByText(/со свежей ценой донора 0/)).toBeInTheDocument();
    expect(
      screen.getByText(/Кончились на ступени «цена донора свежая»: сначала нужны ответы доноров/),
    ).toBeInTheDocument();
    // Воронка накопительная: «с адресом 0» стоит после нуля на цене и про адреса
    // не говорит — совет искать контакты отправил бы платить за поиск впустую.
    expect(screen.queryByText(/искать контакты/)).not.toBeInTheDocument();
  });

  it('переключение этапа запоминается: вернувшись, человек продолжает там же', async () => {
    await openLetters({}, { 'GET /api/letters?stage=advertisers': { body: OFFER_VIEW } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));

    expect(localStorage.getItem('letters:stage')).toBe('advertisers');
  });

  it('выбранный этап помнится до следующего захода', async () => {
    localStorage.setItem('letters:stage', 'advertisers');

    const recorded = await openLetters(
      {},
      { 'GET /api/letters?stage=advertisers': { body: OFFER_VIEW } },
    );

    expect(await screen.findAllByText('brand.example.test')).not.toHaveLength(0);
    expect(recorded.calls.some((call: Call) => call.path === '/api/letters')).toBe(false);
  });
});
