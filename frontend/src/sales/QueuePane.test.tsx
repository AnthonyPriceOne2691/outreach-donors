/**
 * Вкладка «Очередь писем»: подключены ли продажи — словами сервера, сборка задачей,
 * пачка — общей отправкой очереди этапа продаж. Ответы сервера — записанные (`serve()`),
 * незаписанный запрос роняет тест; адреса и имена выдуманы.
 *
 * Проверяется то, ради чего вкладка: не подключены — кнопки закрыты, и названо всё,
 * чего не хватает; отказ сервера на нажатие виден словами; на сервер уходит гипотеза и
 * число писем, а пачка — общей кнопкой почты с этапом продаж; число на кнопке пачки —
 * письма всех гипотез, и вкладка говорит это до нажатия; итог задачи — словами; без права
 * отправки кнопки пачки нет, а сборка закрыта и сказано почему.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type {
  HypothesesView,
  SalesLeadFollowupsView,
  SalesQueueLetter,
  SalesQueueReport,
  SalesQueueView,
} from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { letterLine, queueLine } from './queueData';

const SCREEN_WAIT = { timeout: 5000 };
const QUEUE = '/api/sales/queue';
const ALL_MISSING = ['первого письма', 'первой добивки', 'второй добивки'];
const WRITE_RIGHT = 'Собирает очередь тот, у кого есть право отправки писем. Смотреть можно всем.';

function hypothesis(id: number, name: string) {
  return {
    id,
    name,
    description: null,
    created_at: '2026-10-01T10:00:00+00:00',
    leads: { new: 0, ready: 3, rejected: 0 },
    total: 3,
  };
}

const HYPOTHESES: HypothesesView = {
  rows: [hypothesis(5, 'тестовая гипотеза'), hypothesis(8, 'вторая тестовая')],
  total: 2,
  module_enabled: true,
};

/** Строка сервера вместо кнопки пачки, пока включена автоотправка (`auto_lines.batch_line`). */
const BATCH_OFF =
  'Пачкой письма продаж не уходят, пока включена автоотправка: письма уходят сами, по одному с ' +
  'ящика за проход и с паузой между письмами ящика; отправить очередь вручную — выключить ' +
  'автоотправку (SALES_AUTOSEND).';

const READY: SalesQueueView = {
  hypothesis_id: 5,
  connected: true,
  missing: [],
  chains: [
    { language: 'ru', source: 'common', missing: ALL_MISSING, version: 'chain-ru0000000000' },
    { language: 'en', source: 'own', missing: [], version: 'chain-en0000000000' },
  ],
  unwritten: 4,
  queued: 2,
  stage_queued: 7,
  limit_max: 200,
  batch_max: 200,
  autosend: {
    enabled: false,
    line: 'Автоотправка выключена — первые письма уходят кнопкой «Отправить очередь» или по одному; включается настройкой SALES_AUTOSEND.',
  },
  autobuild: {
    enabled: false,
    line: 'Автосборка выключена — очередь собирают кнопкой «Собрать очередь»; включается настройкой SALES_AUTOBUILD.',
  },
  batch_off: null,
};

const OFF: SalesQueueView = {
  ...READY,
  connected: false,
  missing: [
    'модуль продаж выключен — включает администратор',
    'не задан физический адрес — вкладка «Отправитель»',
  ],
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

function job(id: string, report: Record<string, unknown>) {
  return {
    job_id: id,
    kind: 'сборка очереди продаж',
    state: 'done',
    title: 'готово',
    error: null,
    report,
    retries_left: null,
    next_try_at: null,
    ended_at: null,
  };
}

/** Ключ сводки причин — словами сервера, без чисел: сто таких лидов — одна строка. */
const OFF_CORRIDOR = 'вне коридора отличия — ждёт следующей сборки';

/** Почему ждёт письмо лида — по нему, с отличием каждой попытки (`corridor.waiting_reason`). */
const OFF_LETTER = 'вне коридора отличия после 3 попыток: 38%, 31%, 27% — ждёт следующей сборки';

/** Итог сборки без писем по одному — таким его отдавал сервер до отличия на виду. */
const TOTALS = {
  campaign_id: 3,
  prepared: 2,
  refreshed: 1,
  tokens_spent: 259,
  off_corridor: 1,
  followups_template: 0,
  waiting: { 'стоп-лист': 1, [OFF_CORRIDOR]: 1 },
  stopped: null,
};

/** Почему зоны остались шаблонными — заметки сборки, как у доноров: экран их не повторяет. */
const NOTES = { 'зона «close» оставлена шаблонной подбором под коридор отличия': 2 };

const LETTER: SalesQueueLetter = {
  lead_id: 7,
  name: 'Jane Example',
  attempts: 2,
  difference: 0.213,
  difference_text: '21,3%',
  zones: ['intro'],
  queued: true,
  reason: null,
  followups: [],
};

/** Итог сборки с письмами по одному — по порядку сборки; ждущий лид без имени. */
const BUILT = {
  ...TOTALS,
  notes: NOTES,
  letters: [
    LETTER,
    {
      ...LETTER,
      lead_id: 8,
      name: 'Olga Example',
      attempts: 1,
      difference_text: '19%',
      zones: ['greeting', 'intro'],
    },
    {
      ...LETTER,
      lead_id: 45,
      name: null,
      attempts: 3,
      difference_text: '27%',
      zones: ['intro', 'close'],
      queued: false,
      reason: OFF_LETTER,
    },
    { ...LETTER, lead_id: 9, name: 'Ivan Example', attempts: 1, difference_text: '25%', zones: [] },
  ],
} satisfies SalesQueueReport;

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openQueue(
  routes: Routes = {},
  view: SalesQueueView = READY,
  who: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [`GET ${QUEUE}?hypothesis=5`]: { body: view },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=queue');
  await screen.findByText('Лидов без письма', {}, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

describe('очередь писем продаж', () => {
  it('«Писем за раз» — поле по числу, а не 180 px (аудит 09.10.2026)', async () => {
    await openQueue();

    // Поле — по числу, колонка — по подписи: подпись с «i» в одну строку.
    const field = screen.getByRole('textbox', { name: 'Писем за раз' });
    expect(field.closest('.mantine-Input-wrapper')?.getAttribute('style')).toMatch(
      /(^|;)\s*width: 6\.5rem/,
    );
    expect(field.closest('.mantine-InputWrapper-root')?.getAttribute('style') ?? '').not.toMatch(
      /(^|;)\s*width:/,
    );
    // Пояснение — в «i» у подписи, как у «За раз» на «Письмах»: строкой под полем оно
    // раздувало поле до своей ширины.
    expect(screen.queryByText(/Каждое стоит вызова модели/)).toBeNull();
    await userEvent
      .setup()
      .hover(screen.getByRole('button', { name: 'Что значит «Писем за раз»' }));
    expect(
      await screen.findByText('Каждое стоит вызова модели.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('вступление — одной строкой, как уходят письма — в «i» (аудит 09.10.2026)', async () => {
    await openQueue();
    const user = userEvent.setup();

    expect(screen.getByText(/^Сборка пишет первые письма/)).toBeInTheDocument();
    expect(screen.queryByText(/Уходят письма общей отправкой/)).toBeNull();

    await user.hover(screen.getByRole('button', { name: 'Как уходят письма продаж' }));

    expect(
      await screen.findByText(/Уходят письма общей отправкой/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('подключены: цепочки и числа словами сервера, кнопки открыты', async () => {
    await openQueue();

    expect(screen.getByText('продажи подключены')).toBeInTheDocument();
    expect(
      screen.getByText(
        'общая цепочка: нет первого письма, первой добивки, второй добивки — лиды на этом языке ждут.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('своя цепочка гипотезы полна — лиды на этом языке получат письма.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
    // Пачка берёт очередь этапа целиком: число — письма всех гипотез, а не этой.
    expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeEnabled();
  });

  it('автоотправка и автосборка выключены — так и сказано словами сервера, с настройкой', async () => {
    await openQueue();

    expect(screen.getByText(READY.autosend.line)).toBeInTheDocument();
    expect(screen.getByText(READY.autobuild.line)).toBeInTheDocument();
    expect(screen.getByTitle('автоотправка выключена')).toHaveTextContent('автоотправка');
    expect(screen.getByTitle('автосборка выключена')).toHaveTextContent('автосборка');
  });

  it('автоотправка включена: как часто, сколько в окне и почему стоит — строкой сервера', async () => {
    const sending =
      'Автоотправка включена: проход раз в 60 с, с ящика — не больше письма за проход и пауза ' +
      '8–22 мин между его первыми письмами. В окне получателя сейчас 3 письма. Стоит: исход ' +
      'письма №41 неизвестен: оно «отправляется» с 17.03 09:13 UTC, и слать дальше вслепую нельзя.';
    const building =
      'Автосборка включена: очередь — на сутки вперёд, дневные капы ящиков продаж с разгоном — ' +
      '37 писем, в очереди 2 письма; не хватает 35 писем — ближайший проход соберёт гипотезу ' +
      '«тестовая гипотеза», до 35 писем';
    await openQueue(
      {},
      {
        ...READY,
        autosend: { enabled: true, line: sending },
        autobuild: { enabled: true, line: building },
        batch_off: BATCH_OFF,
      },
    );

    expect(screen.getByText(sending)).toBeInTheDocument();
    expect(screen.getByText(building)).toBeInTheDocument();
    expect(screen.getByTitle('автоотправка включена')).toBeInTheDocument();
    expect(screen.getByTitle('автосборка включена')).toBeInTheDocument();
    // Сборка остаётся: очередь пополняют и руками.
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
  });

  it('автоотправка включена — кнопки пачки нет, вместо неё строка сервера (решение владельца 13.10)', async () => {
    await openQueue(
      {},
      { ...READY, autosend: { enabled: true, line: 'идёт' }, batch_off: BATCH_OFF },
    );

    expect(screen.queryByRole('button', { name: /Отправить очередь/ })).not.toBeInTheDocument();
    expect(screen.getByText(BATCH_OFF)).toBeInTheDocument();
    // Слова о пачке «берёт очередь целиком» — о кнопке, которой нет: их тоже нет.
    expect(screen.queryByText(/Пачка берёт очередь продаж целиком/)).not.toBeInTheDocument();
  });

  it('автоотправка выключена — кнопка пачки как была, строки вместо неё нет', async () => {
    await openQueue();

    expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeEnabled();
    expect(screen.getByText(/Пачка берёт очередь продаж целиком/)).toBeInTheDocument();
    expect(screen.queryByText(BATCH_OFF)).not.toBeInTheDocument();
  });

  it('не подключены: всё, чего не хватает, названо, кнопки закрыты', async () => {
    await openQueue({}, OFF);

    const warning = screen.getByText('Продажи к почте не подключены').closest('[role="alert"]');
    if (!(warning instanceof HTMLElement)) throw new Error('плашки нет');
    expect(
      within(warning)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(OFF.missing);
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeDisabled();
  });

  it('не подключены: имя настройки длиннее телефона переносится — плашка не срезает строки (снимок 390 px, 09.10.2026)', async () => {
    await openQueue({}, OFF);

    const message = screen
      .getByText('Продажи к почте не подключены')
      .closest('.mantine-Alert-root')
      ?.querySelector('.mantine-Alert-message');
    expect(message?.getAttribute('style') ?? '').toMatch(/overflow-wrap: anywhere/);
  });

  it('сборка уходит гипотезой и числом писем, итог задачи — словами, очередь перечитана', async () => {
    const recorded = await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', BUILT) },
    });
    const user = userEvent.setup();

    const limit = screen.getByRole('textbox', { name: 'Писем за раз' });
    await user.clear(limit);
    await user.type(limit, '17');
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    await waitFor(() => expect(calls(recorded, 'POST', QUEUE)).toHaveLength(1), SCREEN_WAIT);
    expect(calls(recorded, 'POST', QUEUE)[0]?.body).toEqual({ hypothesis_id: 5, limit: 17 });
    expect(
      await screen.findByText(
        'Новых писем 2, собрано заново 1. Ждут — стоп-лист: 1; ' + `${OFF_CORRIDOR}: 1.`,
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    await waitFor(
      () => expect(calls(recorded, 'GET', `${QUEUE}?hypothesis=5`).length).toBeGreaterThan(1),
      SCREEN_WAIT,
    );
  });

  it('после сборки — письма списком под строкой задачи: ждущие первыми, отличие словами сервера', async () => {
    await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', BUILT) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    const list = await screen.findByRole('list', { name: 'Письма сборки' }, SCREEN_WAIT);
    expect(
      within(list)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      `лид №45 — ${OFF_LETTER}`,
      'Jane Example — отличие 21,3% (переписаны intro), 2 попытки — в очереди',
      'Olga Example — отличие 19% (переписаны greeting, intro), 1 попытка — в очереди',
      'Ivan Example — отличие 25% (переписанных зон нет), 1 попытка — в очереди',
    ]);
    // Строка задачи — первой, список — под ней.
    const outcome = screen.getByText(/^Новых писем 2/);
    expect(outcome.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('итог сборки прежней версии сервера без писем по одному — списка нет', async () => {
    await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', TOTALS) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    expect(await screen.findByText(/^Новых писем 2/, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Письма сборки' })).not.toBeInTheDocument();
  });

  it('писем больше десяти — первые десять, остальные по «Показать все»', async () => {
    const many = Array.from({ length: 13 }, (_, n) => ({
      ...LETTER,
      lead_id: 100 + n,
      name: `Lead ${n + 1}`,
    }));
    await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', { ...BUILT, letters: many }) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    const list = await screen.findByRole('list', { name: 'Письма сборки' }, SCREEN_WAIT);
    expect(within(list).getAllByRole('listitem')).toHaveLength(10);
    expect(screen.getByText('Показаны 10 из 13 писем сборки.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Показать все' }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(13);
    expect(screen.queryByRole('button', { name: 'Показать все' })).not.toBeInTheDocument();
  });

  it('отказ сервера на сборку — словами у кнопки', async () => {
    const refusal =
      'Очередь продаж не собрана: продажи к почте ещё не подключены — не задана подпись';
    await openQueue({ [`POST ${QUEUE}`]: { status: 409, body: { detail: refusal } } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    const alert = (await screen.findByText('Очередь не собрана', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent(refusal);
  });

  it('у гипотезы нечего собирать — кнопка закрыта и сказано почему', async () => {
    await openQueue({}, { ...READY, unwritten: 0, queued: 0 });

    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(
      screen.getByText('Лидов без письма и писем в очереди у гипотезы нет — собирать нечего.'),
    ).toBeInTheDocument();
  });

  it('другая гипотеза — своя очередь с сервера', async () => {
    const recorded = await openQueue({
      [`GET ${QUEUE}?hypothesis=8`]: { body: { ...READY, hypothesis_id: 8, unwritten: 11 } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Гипотеза' }));
    await user.click(
      await screen.findByRole('option', { name: 'вторая тестовая', hidden: true }, SCREEN_WAIT),
    );

    await waitFor(
      () => expect(calls(recorded, 'GET', `${QUEUE}?hypothesis=8`)).toHaveLength(1),
      SCREEN_WAIT,
    );
    expect(await screen.findByText('11', {}, SCREEN_WAIT)).toBeInTheDocument();
  });

  it('гипотез нет — сказано, откуда берётся очередь', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } },
      'GET /api/sales/kb': { body: KB },
    });
    renderWith(<AppRoutes />, '/sales?tab=queue');

    expect(
      await screen.findByText(
        'Гипотез пока нет. Очередь собирается из лидов гипотезы — загрузите базу.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
  });
});

/** Добивки письма, переписанные при сборке: первая — в коридоре, вторая — шаблоном. */
const LATER: SalesQueueLetter['followups'] = [
  {
    step: 2,
    attempts: 1,
    difference: 0.19,
    difference_text: '19%',
    zones: ['nudge'],
    reason: null,
  },
  {
    step: 3,
    attempts: 3,
    difference: null,
    difference_text: null,
    zones: [],
    reason: 'не легла в коридор отличия после 3 попыток: 10%, 10%, 10% — уйдёт текстом шаблона',
  },
];

/** Добивки лида, какими уйдут, — ответ сервера на раскрытие строки. */
const AHEAD: SalesLeadFollowupsView = {
  lead_id: 7,
  followups: [
    {
      step: 2,
      body: 'Hi Jane, a made-up nudge rewritten for the test.',
      words: 'переписана при сборке — отличие 19%',
      difference: 0.19,
      difference_text: '19%',
      zones: ['nudge'],
    },
    {
      step: 3,
      body: 'A made-up last reminder.',
      words: LATER[1]?.reason ?? '',
      difference: null,
      difference_text: null,
      zones: [],
    },
  ],
};

describe('добивки письма сборки', () => {
  const WITH_LATER = {
    ...BUILT,
    followups_template: 1,
    letters: [{ ...LETTER, followups: LATER }],
  };

  it('строка письма называет добивки, итог — сколько уйдёт шаблоном', () => {
    expect(letterLine({ ...LETTER, followups: LATER })).toBe(
      'Jane Example — отличие 21,3% (переписаны intro), 2 попытки — в очереди; ' +
        'добивки: первая — отличие 19%, вторая — шаблоном',
    );
    expect(queueLine(WITH_LATER)).toMatch(/ Добивок шаблоном: 1\.$/);
    expect(queueLine(TOTALS)).not.toMatch(/Добивок/);
    // Итог прежней версии сервера — без добивок: строка о них молчит.
    const old: Partial<SalesQueueLetter> = { ...LETTER };
    delete old.followups;
    expect(letterLine(old as SalesQueueLetter)).toBe(
      'Jane Example — отличие 21,3% (переписаны intro), 2 попытки — в очереди',
    );
  });

  it('тексты добивок — по раскрытию, словами сервера; закрытое не спрашивается', async () => {
    const recorded = await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', WITH_LATER) },
      'GET /api/sales/leads/7/followups': { body: AHEAD },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));
    const toggle = await screen.findByRole('button', { name: 'Тексты добивок' }, SCREEN_WAIT);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(calls(recorded, 'GET', '/api/sales/leads/7/followups')).toHaveLength(0);

    await user.click(toggle);

    const texts = await screen.findByLabelText('Добивки письма', {}, SCREEN_WAIT);
    expect(
      await within(texts).findByText('Hi Jane, a made-up nudge rewritten for the test.'),
    ).toBeInTheDocument();
    expect(
      within(texts).getByText(
        'Первая добивка — переписана при сборке — отличие 19% (переписаны nudge)',
      ),
    ).toBeInTheDocument();
    expect(
      within(texts).getByText(`Вторая добивка — ${AHEAD.followups[1]?.words}`),
    ).toBeInTheDocument();
    expect(within(texts).getByText('A made-up last reminder.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Скрыть тексты добивок' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('письмо без переписанных добивок и ждущее — без раскрытия', async () => {
    await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': {
        body: job('job-b', {
          ...BUILT,
          letters: [
            LETTER,
            { ...LETTER, lead_id: 45, queued: false, reason: OFF_LETTER, followups: LATER },
          ],
        }),
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    await screen.findByRole('list', { name: 'Письма сборки' }, SCREEN_WAIT);
    expect(screen.queryByRole('button', { name: 'Тексты добивок' })).not.toBeInTheDocument();
  });

  it('добивок впереди нет — сказано словами', async () => {
    await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', WITH_LATER) },
      'GET /api/sales/leads/7/followups': { body: { lead_id: 7, followups: [] } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));
    await user.click(await screen.findByRole('button', { name: 'Тексты добивок' }, SCREEN_WAIT));

    expect(
      await screen.findByText(
        'Добивок впереди нет: цепочка кончилась или ушла целиком.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
  });
});

describe('«Писем за раз» — как набрано', () => {
  const NONE = { prepared: 0, refreshed: 0, waiting: {}, stopped: null };

  it('стёртое поле и «05» не прыгают к умолчанию; «05» уходит пятёркой', async () => {
    const recorded = await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': { body: job('job-b', NONE) },
    });
    const user = userEvent.setup();
    const limit = screen.getByRole('textbox', { name: 'Писем за раз' });
    const build = screen.getByRole('button', { name: 'Собрать очередь' });

    await user.clear(limit);
    // Стёртое — пустое, а не умолчание: «впишите» словами, кнопка закрыта.
    expect(limit).toHaveValue('');
    expect(screen.getByText('Впишите, сколько писем собрать')).toBeInTheDocument();
    expect(build).toBeDisabled();

    await user.type(limit, '05');
    expect(limit).toHaveValue('05');
    expect(screen.queryByText('Впишите, сколько писем собрать')).not.toBeInTheDocument();
    await user.click(build);

    await waitFor(() => expect(calls(recorded, 'POST', QUEUE)).toHaveLength(1), SCREEN_WAIT);
    expect(calls(recorded, 'POST', QUEUE)[0]?.body).toEqual({ hypothesis_id: 5, limit: 5 });
  });

  it.each([
    ['201', 'Допустимо от 1 до 200'],
    ['0', 'Допустимо от 1 до 200'],
    ['1.5', 'Только целое число от 1 до 200'],
    ['пять', 'Только целое число от 1 до 200'],
  ])('«%s» — отказ словами у поля, кнопка закрыта, запроса нет', async (text, words) => {
    const recorded = await openQueue();
    const user = userEvent.setup();
    const limit = screen.getByRole('textbox', { name: 'Писем за раз' });

    await user.clear(limit);
    await user.type(limit, text);

    // Набранное — на месте, границы — сервера (`limit_max`), а не копия числа на экране.
    expect(limit).toHaveValue(text);
    expect(screen.getByText(words)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(calls(recorded, 'POST', QUEUE)).toEqual([]);
  });

  it('граница — у сервера: потолок меньше — и отказ называет его', async () => {
    await openQueue({}, { ...READY, limit_max: 30 });
    const user = userEvent.setup();
    const limit = screen.getByRole('textbox', { name: 'Писем за раз' });

    // Умолчание — не выше потолка сервера.
    expect(limit).toHaveValue('30');
    await user.clear(limit);
    await user.type(limit, '31');

    expect(screen.getByText('Допустимо от 1 до 30')).toBeInTheDocument();
  });

  it('вторая сборка гипотезы — строка об исходе новой: номер сборки постоянный', async () => {
    let built = 0;
    const reports = [NONE, { ...NONE, prepared: 4, refreshed: 1 }];
    const recorded = await openQueue({
      [`POST ${QUEUE}`]: () => {
        built += 1;
        return { body: { job_id: 'sales-queue-5' } };
      },
      'GET /api/jobs/sales-queue-5': () => ({
        body: job('sales-queue-5', reports[Math.max(0, built - 1)] ?? NONE),
      }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));
    expect(
      await screen.findByText('Новых писем 0, собрано заново 0.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    // Под тем же номером — новая задача: строка спросила о ней заново, а не держит прежний итог.
    expect(
      await screen.findByText('Новых писем 4, собрано заново 1.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(calls(recorded, 'POST', QUEUE)).toHaveLength(2);
  });
});

describe('пачка продаж', () => {
  it('уходит этапом продаж после подтверждения, итог сказан словами', async () => {
    const recorded = await openQueue({
      'POST /api/letters/send-queue': { body: { job_id: 'job-s', queued: 7 } },
      'GET /api/jobs/job-s': {
        body: job('job-s', { sent: 5, refused: { 'стоп-лист': 2 }, stopped: null, left: 0 }),
      },
    });
    const user = userEvent.setup();

    // Пачка — общая кнопка почты: что она берёт очередь всех гипотез, говорит вкладка.
    expect(
      screen.getByText(
        'Пачка берёт очередь продаж целиком — письма всех гипотез, не только выбранной.',
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/В очереди 7 писем/)).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 7' }));

    await waitFor(
      () =>
        expect(calls(recorded, 'POST', '/api/letters/send-queue')[0]?.body).toEqual({
          stage: 'sales',
        }),
      SCREEN_WAIT,
    );
    expect(
      await screen.findByText('Ушло 5, стоп-лист — 2, осталось в очереди 0.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('очередь этапа длиннее пачки — окно называет потолок, который назвал сервер', async () => {
    await openQueue({}, { ...READY, stage_queued: 7, batch_max: 5 });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    const dialog = await screen.findByRole('dialog');

    // Пачка берёт не больше потолка сервера: окно не обещает всю очередь этапа одним нажатием.
    expect(
      within(dialog).getByText(/^В очереди 7 писем; одна пачка берёт до 5, остальное — следующей/),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Отправить 5' })).toBeInTheDocument();
  });

  it('отказ сервера — словами', async () => {
    const refusal = 'Очередь писем не отправлена: продажи к почте ещё не подключены';
    await openQueue({
      'POST /api/letters/send-queue': { status: 409, body: { detail: refusal } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 7' }));

    expect(await screen.findByText(refusal, {}, SCREEN_WAIT)).toBeInTheDocument();
  });

  it('отмена ничего не отправляет', async () => {
    const recorded = await openQueue();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }),
    );

    expect(calls(recorded, 'POST', '/api/letters/send-queue')).toEqual([]);
  });

  it('без права на отправку кнопки пачки нет, сборка закрыта — строка говорит почему', async () => {
    const recorded = await openQueue({}, READY, OPERATOR);

    // Сборка пишет письма адресатам и тратит модель — как сборка ядра, только с правом отправки.
    expect(screen.getByRole('textbox', { name: 'Писем за раз' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(screen.getByText(WRITE_RIGHT)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Отправить очередь/ })).not.toBeInTheDocument();
    // Смотреть — как было: подключение, цепочки и числа.
    expect(screen.getByText('продажи подключены')).toBeInTheDocument();
    expect(calls(recorded, 'POST', QUEUE)).toEqual([]);
  });

  it('с правом отправки строки о праве нет, сборка открыта', async () => {
    await openQueue();

    expect(screen.queryByText(WRITE_RIGHT)).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Писем за раз' })).toBeEnabled();
  });
});

describe('итог сборки словами', () => {
  it('называет, почему сборка остановилась', () => {
    expect(
      queueLine({ prepared: 0, refreshed: 0, waiting: {}, stopped: 'потолок расхода на модель' }),
    ).toBe('Новых писем 0, собрано заново 0. Остановлено: потолок расхода на модель');
  });

  it('письмо — строкой: имя или номер лида, отличие сервера, попыток, в очереди или ждёт', () => {
    expect(letterLine(LETTER)).toBe(
      'Jane Example — отличие 21,3% (переписаны intro), 2 попытки — в очереди',
    );
    expect(
      letterLine({ ...LETTER, name: null, attempts: 3, queued: false, reason: OFF_LETTER }),
    ).toBe(`лид №7 — ${OFF_LETTER}`);
    expect(letterLine({ ...LETTER, attempts: 1, zones: [] })).toBe(
      'Jane Example — отличие 21,3% (переписанных зон нет), 1 попытка — в очереди',
    );
    // Итог сборки прежней версии сервера: ни зон, ни причины по письму — строка о них молчит.
    const old = { ...LETTER, queued: false } as Partial<SalesQueueLetter>;
    delete old.zones;
    delete old.reason;
    expect(letterLine(old as SalesQueueLetter)).toBe(
      'Jane Example — отличие 21,3%, 2 попытки — ждёт',
    );
  });
});
