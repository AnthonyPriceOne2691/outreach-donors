/**
 * Вкладка «Рассылка»: режим, срок очереди, адреса по частям, план и факт по дням — числами и
 * словами двух ручек сервера; отказ одной ручки — словами в её блоке, другая живёт; обе — раз в
 * минуту, и видно, когда обновлено. Ответы сервера — записанные (`serve()`), незаписанный запрос
 * роняет тест; гипотезы, ящики и домены выдуманы.
 *
 * Часы закреплены (`vi.useFakeTimers({ toFake: ['Date'] })`): «обновлено в …» берёт время
 * браузера, и тест без закреплённых часов назавтра проверял бы другое. Таймеры не подменяются —
 * иначе встали бы `findBy`; раз в минуту проверяется вызовом того самого интервала, который
 * поставил запрос (`setInterval` с 60 000 мс), — без минуты ожидания.
 */

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { formatTime } from '../format';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import {
  AUTOBUILD_OFF,
  AUTOBUILD_OFF_SHORT,
  AUTOSEND_OFF_SHORT,
  AUTOSEND_ON,
  AUTOSEND_ON_SHORT,
  AUTO_WORDS,
  BLOCKED,
  CUT_WORDS,
  FORECAST,
  FORECAST_BLOCKED,
  FORECAST_MANUAL,
  FORECAST_PAUSED,
  FORECAST_ROUTE,
  MAILING,
  MAILING_IN_FLIGHT,
  MAILING_MANUAL,
  MAILING_MANY_REASONS,
  MAILING_NOW,
  MAILING_ROUTE,
  MANUAL_WORDS,
  NOTHING_LEFT,
} from './mailingFixtures';

const SCREEN_WAIT = { timeout: 5000 };
/** Раз в минуту — числом задания, а не константой экрана: правка константы не проходит молча. */
const MINUTE = 60_000;
const REFUSED = { status: 503, body: { detail: 'выдуманный отказ теста' } };

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 5,
      name: 'Кофейни Лиссабона',
      description: null,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 5, ready: 35, rejected: 10 },
      total: 180,
    },
  ],
  total: 1,
  module_enabled: true,
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openMailing(routes: Routes = {}, ready = 'Когда уйдёт очередь'): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [MAILING_ROUTE]: { body: MAILING },
    [FORECAST_ROUTE]: { body: FORECAST },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=mailing');
  await screen.findByRole('region', { name: ready }, SCREEN_WAIT);
  // Обе ручки ответили — числами или отказом: крутилок вкладки больше нет.
  await waitFor(() => {
    expect(screen.queryByLabelText('Загружаем прогноз рассылки')).toBeNull();
    expect(screen.queryByLabelText('Загружаем режим и адреса')).toBeNull();
  }, SCREEN_WAIT);
  return recorded;
}

/** Текст так, как его сравнивает поиск по тексту: неразрывные пробелы сервера («60 с») — обычные. */
function plain(text: string): string {
  return text.replace(/\s+/g, ' ');
}

function region(name: string): HTMLElement {
  return screen.getByRole('region', { name });
}

/** Строка таблицы по первой ячейке — гипотезе, дню или ящику. */
function rowOf(where: HTMLElement, first: string): HTMLElement {
  const row = within(where)
    .getAllByRole('row')
    .find((found) => found.querySelector('td')?.textContent?.startsWith(first));
  if (row === undefined) throw new Error(`строки «${first}» нет`);
  return row;
}

function cells(row: HTMLElement): string[] {
  return [...row.querySelectorAll('td')].map((cell) => cell.textContent ?? '');
}

function calls(recorded: Recorded, path: string): Call[] {
  return recorded.calls.filter((call) => call.path === path.replace(/^GET /, ''));
}

/** Интервалы в минуту, которые поставили запросы: их вызов — это и есть «прошла минута». */
function minuteTicks(intervals: MockInstance<typeof setInterval>): (() => void)[] {
  return intervals.mock.calls
    .filter(([, delay]) => delay === MINUTE)
    .map(([callback]) => callback as () => void);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(MAILING_NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('рассылка: режим и срок очереди', () => {
  it('режим — короткими строками сервера у значков, целиком — в «i» (замечание 10.11.2026)', async () => {
    await openMailing();

    expect(screen.getByText(AUTOSEND_ON_SHORT)).toBeInTheDocument();
    expect(screen.getByText(AUTOBUILD_OFF_SHORT)).toBeInTheDocument();
    expect(screen.getByTitle('автоотправка включена')).toBeInTheDocument();
    expect(screen.getByTitle('автосборка выключена')).toBeInTheDocument();
    // Темп прохода, кому уходят письма и имя настройки — не на экране, а в «i».
    expect(screen.queryByText(plain(AUTOSEND_ON))).toBeNull();
    expect(screen.queryByText(AUTOBUILD_OFF)).toBeNull();

    const user = userEvent.setup();
    await user.hover(screen.getByRole('button', { name: 'Подробно: автоотправка' }));
    expect(await screen.findByText(plain(AUTOSEND_ON))).toBeInTheDocument();
    await user.hover(screen.getByRole('button', { name: 'Подробно: автосборка' }));
    expect(await screen.findByText(AUTOBUILD_OFF)).toBeInTheDocument();
  });

  it('когда уйдёт очередь: день, дней отправки, часы автоотправки и слова срока сервера', async () => {
    await openMailing();
    const eta = within(region('Когда уйдёт очередь'));

    expect(eta.getByText('Уйдёт к').nextElementSibling).toHaveTextContent('ср 04.11');
    expect(eta.getByText('Дней отправки').nextElementSibling).toHaveTextContent('3');
    expect(eta.getByText('Часов').nextElementSibling).toHaveTextContent('≈ 54,3 ч');
    expect(eta.getByText('В очереди').nextElementSibling).toHaveTextContent('96');
    expect(eta.queryByText(/автоотправка стоит/)).toBeNull();
    // Плитки дня и часов сказали срок числами — слова сервера в «i» заголовка (разведка 10.11).
    expect(eta.queryByText(plain(AUTO_WORDS))).toBeNull();
    await userEvent
      .setup()
      .hover(eta.getByRole('button', { name: 'Как считается «Когда уйдёт очередь»' }));
    expect(await screen.findByText(plain(AUTO_WORDS))).toBeInTheDocument();
  });

  it('автоотправка стоит — плашкой у срока словами сервера; срок тот же, при условии, что остановку снимут', async () => {
    await openMailing({ [FORECAST_ROUTE]: { body: FORECAST_PAUSED } });
    const eta = within(region('Когда уйдёт очередь'));

    expect(
      eta.getByText(
        /^Исход письма №38 неизвестен: .* в блоке «Исход неизвестен»\. Пока это так, автоотправка стоит: срок и план — при условии, что остановку снимут\.$/,
      ),
    ).toBeInTheDocument();
    expect(eta.getByText('Уйдёт к').nextElementSibling).toHaveTextContent('ср 04.11');
    expect(eta.getByText('Часов').nextElementSibling).toHaveTextContent('≈ 54,3 ч');
    expect(eta.queryByText(plain(AUTO_WORDS))).toBeNull();
  });

  it('письма, которые не уйдут, пока их не поправят, — плашкой с причинами и числами', async () => {
    await openMailing();
    const eta = within(region('Когда уйдёт очередь'));

    expect(
      eta.getByText('В очереди 96, из них 3 не уйдут, пока их не поправят:'),
    ).toBeInTheDocument();
    expect(eta.getByText('адрес лида ждёт проверки — 2')).toBeInTheDocument();
    expect(eta.getByText('пояс получателя неизвестен — 1')).toBeInTheDocument();
  });

  it('вручную часов нет — срок словами сервера: верен, если жать пачку каждый день отправки', async () => {
    await openMailing({
      [MAILING_ROUTE]: { body: MAILING_MANUAL },
      [FORECAST_ROUTE]: { body: FORECAST_MANUAL },
    });
    const eta = within(region('Когда уйдёт очередь'));

    expect(screen.getByText(AUTOSEND_OFF_SHORT)).toBeInTheDocument();
    expect(screen.getByTitle('автоотправка выключена')).toBeInTheDocument();
    expect(eta.queryByText('Часов')).toBeNull();
    expect(eta.queryByText(/≈ .* ч$/)).toBeNull();
    expect(eta.getByText('Уйдёт к').nextElementSibling).toHaveTextContent('ср 04.11');
    expect(eta.getByText(MANUAL_WORDS)).toBeInTheDocument();
  });

  it('письма не уходят вовсе — плашкой первой, над режимом; срока нет — словами сервера', async () => {
    await openMailing({ [FORECAST_ROUTE]: { body: FORECAST_BLOCKED } });
    const eta = within(region('Когда уйдёт очередь'));

    const plaque = screen.getByText(BLOCKED);
    const mode = screen.getByText(AUTOSEND_ON_SHORT);
    expect(plaque.compareDocumentPosition(mode) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(eta.queryByText('Уйдёт к')).toBeNull();
    expect(eta.queryByText('Дней отправки')).toBeNull();
    expect(eta.getByText(`очередь не уходит: ${BLOCKED}`)).toBeInTheDocument();
  });
});

describe('рассылка: адреса по частям', () => {
  it('части по гипотезам и итог — числами сервера; из чего складывается «всего» — в «i»', async () => {
    await openMailing();
    const parts = region('Адреса по гипотезам');
    const formula =
      'Всего = ушло + в очереди + без письма (напишет и не напишет) + не готовы + сняты: каждый адрес — ровно в одной части.';

    // Формулу читают раз — она в «i» заголовка, а не строкой над таблицей.
    expect(within(parts).queryByText(formula)).toBeNull();
    await userEvent
      .setup()
      .hover(within(parts).getByRole('button', { name: 'Как считается «Адреса по гипотезам»' }));
    expect(await screen.findByText(formula)).toBeInTheDocument();
    const headers = within(parts)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent);
    expect(headers).toEqual([
      'Гипотеза',
      'Всего',
      'Ушло',
      'В очереди',
      'Напишет',
      'Не напишет',
      'Не готовы',
      'Сняты',
    ]);
    expect(cells(rowOf(parts, 'Кофейни Лиссабона'))).toEqual([
      'Кофейни Лиссабона',
      '180',
      '70',
      '60',
      '25',
      '10',
      '5',
      '10',
    ]);
    expect(cells(rowOf(parts, 'Dental clinics — Toronto')).slice(1)).toEqual([
      '120',
      '40',
      '36',
      '15',
      '8',
      '21',
      '0',
    ]);
    expect(cells(rowOf(parts, 'Все гипотезы')).slice(1)).toEqual([
      '300',
      '110',
      '96',
      '40',
      '18',
      '26',
      '10',
    ]);
  });

  it('письма в полёте — под «ушло» строки и итога приглушённо; нет их — под числом пусто', async () => {
    await openMailing({ [MAILING_ROUTE]: { body: MAILING_IN_FLIGHT } });
    const parts = region('Адреса по гипотезам');
    const flying = 'из них 2 в полёте — исход ещё неизвестен';
    // «Ушло» — третья ячейка строки: число сервера, под ним — сколько из него в полёте.
    const sentOf = (first: string) => rowOf(parts, first).querySelectorAll('td')[2];

    for (const first of ['Кофейни Лиссабона', 'Все гипотезы']) {
      const sent = sentOf(first);
      expect(sent).toBeDefined();
      const note = within(sent as HTMLElement).getByText(flying);
      expect(note).toHaveClass('mailingInFlight');
      // Ячейка таблицы — в одну строку: без переноса слова ложатся на числа соседних колонок.
      expect(note).toHaveStyle({ whiteSpace: 'normal' });
    }
    expect(cells(rowOf(parts, 'Кофейни Лиссабона'))).toEqual([
      'Кофейни Лиссабона',
      '180',
      `70${flying}`,
      '60',
      '25',
      '10',
      '5',
      '10',
    ]);
    expect(cells(rowOf(parts, 'Dental clinics — Toronto'))[2]).toBe('40');
    expect(within(parts).getAllByText(flying)).toHaveLength(2);
    expect(within(parts).getAllByRole('columnheader')).toHaveLength(8);
  });

  it('писем в полёте нет — под «ушло» ничего', async () => {
    await openMailing();

    expect(within(region('Адреса по гипотезам')).queryByText(/в полёте/)).toBeNull();
  });

  it('почему сборка не напишет и почему сняты — раскрытием под строкой своей гипотезы', async () => {
    await openMailing();
    const user = userEvent.setup();
    const parts = within(region('Адреса по гипотезам'));

    const wont = parts.getByRole('button', {
      name: 'Почему сборка не напишет: «Кофейни Лиссабона» — 10',
    });
    expect(wont).toHaveAttribute('aria-expanded', 'false');
    expect(parts.queryByText('цепочка на португальском задана не целиком — 10')).toBeNull();

    await user.click(wont);

    expect(wont).toHaveAttribute('aria-expanded', 'true');
    const list = document.getElementById(wont.getAttribute('aria-controls') ?? '');
    expect(list).not.toBeNull();
    expect(list).toHaveTextContent('цепочка на португальском задана не целиком — 10');
    // Причины — той гипотезы, чья строка: у второй их не видно.
    expect(parts.queryByText('у лида нет значения для имени — 8')).toBeNull();

    await user.click(parts.getByRole('button', { name: 'Почему сняты: все гипотезы — 10' }));
    expect(parts.getByText('дубль — 6')).toBeInTheDocument();
    expect(parts.getByText('домен не принимает почту — 4')).toBeInTheDocument();

    await user.click(wont);
    expect(wont).toHaveAttribute('aria-expanded', 'false');
    expect(parts.queryByText('цепочка на португальском задана не целиком — 10')).toBeNull();
  });

  it('причин больше пяти — на виду пять частых, остальные — «ещё N причин» раскрытием', async () => {
    await openMailing({ [MAILING_ROUTE]: { body: MAILING_MANY_REASONS } });
    const user = userEvent.setup();
    const parts = within(region('Адреса по гипотезам'));

    await user.click(parts.getByRole('button', { name: 'Почему сняты: все гипотезы — 11' }));

    expect(parts.getByText('дубль — 4')).toBeInTheDocument();
    expect(parts.getByText('письмо снято: 552 выдуманный ящик переполнен — 1')).toBeInTheDocument();
    expect(parts.queryByText('письмо снято: 554 выдуманный отказ получателя — 1')).toBeNull();
    const more = parts.getByRole('button', { name: 'ещё 2 причины' });
    expect(more).toHaveAttribute('aria-expanded', 'false');

    await user.click(more);

    expect(more).toHaveAttribute('aria-expanded', 'true');
    expect(more).toHaveTextContent('Свернуть');
    expect(
      await parts.findByText('письмо снято: 554 выдуманный отказ получателя — 1', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(
      parts.getByText('письмо снято: 421 выдуманная временная ошибка — 1'),
    ).toBeInTheDocument();
  });

  it('нет причин — число без кнопки: раскрывать нечего', async () => {
    await openMailing();
    const parts = within(region('Адреса по гипотезам'));

    expect(
      parts.queryByRole('button', { name: /^Почему сняты: «Dental clinics — Toronto»/ }),
    ).toBeNull();
    expect(
      parts.getByRole('button', {
        name: 'Почему сборка не напишет: «Dental clinics — Toronto» — 8',
      }),
    ).toBeInTheDocument();
  });
});

describe('рассылка: одна гипотеза — без итога', () => {
  it('итог при одной гипотезе не повторяет её строку теми же числами', async () => {
    await openMailing({
      [MAILING_ROUTE]: { body: { ...MAILING, rows: MAILING.rows.slice(0, 1) } },
    });
    const parts = region('Адреса по гипотезам');

    const names = within(parts)
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.querySelector('td')?.textContent);
    expect(names).toEqual(['Кофейни Лиссабона']);
    expect(within(parts).queryByText('Все гипотезы')).toBeNull();
  });
});

describe('рассылка: план и факт', () => {
  it('план на 7 дней: мощность, план и факт первых и добивок, почему меньше мощности — по датам', async () => {
    await openMailing();
    const plan = region('План на 7 дней');

    expect(within(plan).getAllByRole('row')).toHaveLength(8);
    // Сегодня план — день целиком: ушло 6 и ещё уйдёт 24 — 30; добивок ушло 2 и ждёт 5 — 7; факт
    // — ушедшее. День выбирает мощность, и причины у него нет: ушедшее — не причина.
    expect(cells(rowOf(plan, 'пн 02.11'))).toEqual([
      'пн 02.11сегодня',
      '30',
      '30',
      '6',
      '7',
      '2',
      'во всю мощность',
    ]);
    // У будущих дней факта нет — прочерк, а не ноль.
    expect(cells(rowOf(plan, 'вт 03.11'))).toEqual([
      'вт 03.11',
      '35',
      '35',
      '—',
      '3',
      '—',
      'во всю мощность',
    ]);
    expect(cells(rowOf(plan, 'пт 06.11'))).toEqual([
      'пт 06.11',
      '40',
      '0',
      '—',
      '2',
      '—',
      'отправлять больше нечего',
    ]);
    expect(cells(rowOf(plan, 'сб 07.11'))).toEqual([
      'сб 07.11',
      '0',
      '0',
      '—',
      '0',
      '—',
      'не день отправки',
    ]);

    await userEvent.setup().hover(
      within(rowOf(plan, 'пт 06.11')).getByRole('button', {
        name: 'Почему меньше мощности — пт 06.11',
      }),
    );
    expect(await screen.findByText(NOTHING_LEFT)).toBeInTheDocument();
  });

  it('над планом — сколько ещё пустят лимиты сегодня и сниженные лимиты словами; ушедшее — в колонках факта', async () => {
    await openMailing();
    const plan = within(region('План на 7 дней'));

    expect(plan.getByText('Сегодня лимиты ящиков пустят ещё 24 первых.')).toBeInTheDocument();
    expect(plan.queryByText(/ушло первых/)).toBeNull();
    expect(plan.getByText(CUT_WORDS)).toBeInTheDocument();
    // Окно получателя и пояс — «как считается»: в «i» плана, а не строкой над таблицей. Пояс —
    // смещением от UTC на закреплённый день теста и именем из базы поясов в скобках.
    const window =
      'Окно получателя — пн–пт 09:00–17:00 по его часам; часы дня — по поясу UTC−5 (America/Toronto), самому частому в очереди.';
    expect(plan.queryByText(window)).toBeNull();
    await userEvent
      .setup()
      .hover(screen.getByRole('button', { name: 'Как считается «План на 7 дней»' }));
    expect(await screen.findByText(window)).toBeInTheDocument();
  });

  it('факт за 7 дней — по нажатию вкладки, по датам: шесть прошлых, старые первыми, и сегодня последней', async () => {
    await openMailing();
    await userEvent.setup().click(screen.getByRole('radio', { name: 'Факт за 7 дней' }));
    const past = region('Факт за 7 дней');

    const days = within(past)
      .getAllByRole('row')
      .slice(1)
      .map((row) => cells(row));
    expect(days).toEqual([
      ['вт 27.10', '22', '6'],
      ['ср 28.10', '25', '3'],
      ['чт 29.10', '28', '8'],
      ['пт 30.10', '30', '5'],
      ['сб 31.10', '0', '0'],
      ['вс 01.11', '0', '0'],
      ['пн 02.11сегодня', '6', '2'],
    ]);
  });

  it('ящиков с разгоном на вкладке нет: они на экране «Домены рассылки»', async () => {
    await openMailing();

    expect(screen.queryByRole('region', { name: 'Ящики и разгон' })).toBeNull();
    expect(screen.queryByText(/hello@mail-a/)).toBeNull();
  });
});

describe('рассылка: отказ ручки и обновление', () => {
  it('ручка частей адресов отказала — словами в её блоке, прогноз живёт', async () => {
    await openMailing({ [MAILING_ROUTE]: REFUSED });

    expect(
      await screen.findByText('Режим и адреса по гипотезам не загрузились', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText('выдуманный отказ теста')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Адреса по гипотезам' })).toBeNull();
    expect(
      within(region('Когда уйдёт очередь')).getByText('Уйдёт к').nextElementSibling,
    ).toHaveTextContent('ср 04.11');
    expect(region('План на 7 дней')).toBeInTheDocument();
  });

  it('прогноз отказал — словами в его блоке, режим и адреса живут', async () => {
    await openMailing({ [FORECAST_ROUTE]: REFUSED });

    const eta = within(region('Когда уйдёт очередь'));
    expect(
      await eta.findByText('Прогноз рассылки не загрузился', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(eta.getByText('выдуманный отказ теста')).toBeInTheDocument();
    expect(screen.getByText(AUTOSEND_ON_SHORT)).toBeInTheDocument();
    expect(region('Адреса по гипотезам')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'План на 7 дней' })).toBeNull();
    expect(screen.queryByRole('radio', { name: 'Факт за 7 дней' })).toBeNull();
  });

  it('обе ручки — раз в минуту; видно, когда обновлено', async () => {
    const intervals = vi.spyOn(globalThis, 'setInterval');
    let asked = 0;
    const recorded = await openMailing({
      [FORECAST_ROUTE]: () => {
        asked += 1;
        return { body: asked === 1 ? FORECAST : { ...FORECAST, queued_first: 90 } };
      },
    });
    expect(
      screen.getByText(`обновлено в ${formatTime(MAILING_NOW.toISOString())} · раз в минуту`),
    ).toBeInTheDocument();

    // Интервал в минуту ставит каждый запрос с `refetchInterval` — и обе ручки вкладки, и числа
    // у пунктов меню: зовутся все, а проверяется, что перечитались обе ручки.
    const minute = intervals.mock.calls.filter(([, delay]) => delay === MINUTE);
    expect(minute.length).toBeGreaterThanOrEqual(2);
    const later = new Date(MAILING_NOW.getTime() + MINUTE);
    vi.setSystemTime(later);
    act(() => {
      for (const [tick] of minute) (tick as () => void)();
    });

    await waitFor(() => expect(calls(recorded, FORECAST_ROUTE)).toHaveLength(2), SCREEN_WAIT);
    expect(calls(recorded, MAILING_ROUTE)).toHaveLength(2);
    const eta = within(region('Когда уйдёт очередь'));
    await waitFor(
      () => expect(eta.getByText('В очереди').nextElementSibling).toHaveTextContent('90'),
      SCREEN_WAIT,
    );
    expect(
      await screen.findByText(
        `обновлено в ${formatTime(later.toISOString())} · раз в минуту`,
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
  });

  it('обновление раз в минуту не сбрасывает вкладку: факт остаётся, числа новые', async () => {
    const intervals = vi.spyOn(globalThis, 'setInterval');
    let asked = 0;
    // Второй ответ: у прошлого дня другое число, и сегодня ушло ещё три первых письма.
    const past = FORECAST.past.map((day) =>
      day.day === '2026-10-27' ? { ...day, sent_first: 77 } : day,
    );
    const today = { ...FORECAST.today, sent_first: 9 };
    await openMailing({
      [FORECAST_ROUTE]: () => {
        asked += 1;
        return { body: asked === 1 ? FORECAST : { ...FORECAST, past, today } };
      },
    });
    await userEvent.setup().click(screen.getByRole('radio', { name: 'Факт за 7 дней' }));
    const minute = minuteTicks(intervals);
    vi.setSystemTime(new Date(MAILING_NOW.getTime() + MINUTE));
    act(() => {
      for (const tick of minute) tick();
    });

    await waitFor(() => expect(asked).toBe(2), SCREEN_WAIT);
    await waitFor(
      () => expect(cells(rowOf(region('Факт за 7 дней'), 'вт 27.10'))[1]).toBe('77'),
      SCREEN_WAIT,
    );
    // Сегодняшняя строка факта растёт вместе с прогнозом — без перезагрузки страницы.
    expect(cells(rowOf(region('Факт за 7 дней'), 'пн 02.11'))).toEqual([
      'пн 02.11сегодня',
      '9',
      '2',
    ]);
    expect(screen.getByRole('radio', { name: 'Факт за 7 дней' })).toBeChecked();
  });

  it('обновление отказало — прежние числа стоят, и сказано, на какое время они', async () => {
    const intervals = vi.spyOn(globalThis, 'setInterval');
    let asked = 0;
    await openMailing({
      [FORECAST_ROUTE]: () => {
        asked += 1;
        return asked === 1 ? { body: FORECAST } : REFUSED;
      },
    });
    const minute = minuteTicks(intervals);
    const later = new Date(MAILING_NOW.getTime() + MINUTE);
    vi.setSystemTime(later);
    act(() => {
      for (const tick of minute) tick();
    });

    const eta = within(region('Когда уйдёт очередь'));
    expect(
      await eta.findByText(
        `Не обновилось: выдуманный отказ теста. Числа — на ${formatTime(MAILING_NOW.toISOString())}.`,
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    expect(eta.getByText('Уйдёт к').nextElementSibling).toHaveTextContent('ср 04.11');
    // Части адресов обновились, прогноз — нет: «обновлено» — по старшему из двух, а не по свежему.
    await waitFor(() => expect(asked).toBe(2), SCREEN_WAIT);
    expect(
      screen.getByText(`обновлено в ${formatTime(MAILING_NOW.toISOString())} · раз в минуту`),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(`обновлено в ${formatTime(later.toISOString())} · раз в минуту`),
    ).toBeNull();
  });
});
