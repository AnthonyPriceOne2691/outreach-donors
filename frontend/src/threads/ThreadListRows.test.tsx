/**
 * Строки списка диалогов: окно в сто строк и второй ярус строки (11.11.2026).
 *
 * Разведка экранов 10.11.2026: список рисовал все диалоги разом — сотни и тысячи строк, лента в
 * полтора десятка экранов на телефоне; а метка «ответ продаж — ждёт человека» оставляла от
 * адреса семь знаков. Проверяется путь человека: первые сто и «Показать ещё», счёт с начала при
 * другом списке, открытый диалог виден и за сотней, возврат к списку — к тем же строкам; числа
 * и переходы считают весь список, а не нарисованное. Данные — выдуманные, на `*.example.test`.
 *
 * Раскладку jsdom не считает: у второго яруса сверяется разметка, на которую опираются правила
 * `glass.css`, и сами правила.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { SPLIT_QUERY } from '../layout/split';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { LIST_STEP, listScope, windowSize } from './listWindow';
import { NO_THREAD_FILTERS } from './threadFilters';

// Сотни строк рисуются по-настоящему, по нескольку раз за тест: на раннере проверок это в
// пять-шесть раз дольше, чем на машине разработчика, и обычных пяти секунд тесту впритык.
vi.setConfig({ testTimeout: 15_000 });

/** Короткая дата строки зависит от «сегодня» (`shortWhen`): часы закреплены. */
const NOW = new Date('2026-11-11T12:00:00+03:00');

function thread(id: number, stage: string, state: string) {
  const host = `${stage}-site-${id}.example.test`;
  return {
    id,
    host,
    contact_email: `editor@${host}`,
    campaign: 'Демонстрация',
    stage,
    state,
    messages_sent: 1,
    // Чем больше номер, тем раньше событие: среди равных по ожиданию список идёт от свежего
    // события к старому — то есть по возрастанию номера диалога, и место строки равно номеру.
    last_event_at: new Date(Date.UTC(2026, 10, 10, 12, 0, 0) - id * 60_000).toISOString(),
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  };
}

/** Двести пятьдесят переписок продаж: первые сто пятьдесят ждут человека, остальные — ответа. */
const SALES = Array.from({ length: 250 }, (_, at) =>
  thread(at + 1, 'sales', at < 150 ? 'sales_pending' : 'waiting'),
);
/** Сто двадцать диалогов рекламодателей — другая часть, тоже длиннее сотни. */
const ADVERTISERS = Array.from({ length: 120 }, (_, at) =>
  thread(at + 1001, 'advertisers', 'lead'),
);
const DONOR = thread(2001, 'donors', 'recheck');
const THREADS = [...SALES, ...ADVERTISERS, DONOR];

function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

function open(path: string, threads = THREADS) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: threads },
    'GET /api/replies/calibration': { body: { versions: [] } },
    'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    ...Object.fromEntries(
      THREADS.map((card) => [
        `GET /api/threads/${card.id}`,
        { body: { card, letters: [], incoming: [], corridor: { min: 0.15, max: 0.25 } } },
      ]),
    ),
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
}

/** Широкое окно: список — колонкой рядом с перепиской. */
function wide() {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: query === SPLIT_QUERY,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

/**
 * Список, его строки и кнопка под ними ищутся по разметке, а не по ролям: запрос по роли обходит
 * дерево и считает имя каждой ссылки, а строк здесь сотни — на раннере проверок тест с двумя
 * сотнями строк не укладывался в пять секунд. Роли и имена сверяются там, где строка одна.
 */
const list = () =>
  waitFor(() => {
    const found = document.querySelector<HTMLElement>(
      '[role="navigation"][aria-label="Список диалогов"]',
    );
    expect(found).not.toBeNull();
    return found!;
  });
/** Нарисованные строки — ссылки на переписки. */
const drawn = async () => Array.from((await list()).querySelectorAll<HTMLElement>('a[href]'));
/** Строка диалога по его номеру; нет в нарисованном — `null`. */
const row = (id: number) =>
  document.querySelector<HTMLElement>(
    `[role="navigation"][aria-label="Список диалогов"] a[href^="/threads/${id}?"]`,
  );
/** Кнопка под строками. Число держится при слове неразрывным пробелом, и имя кнопки — с ним. */
const more = (count: number) => {
  const button = document.querySelector<HTMLElement>('.threadListMore button');
  return button?.textContent === `Показать ещё\u00a0${count}` ? button : null;
};
/** Выбранная строка — открытый диалог. */
const current = () =>
  waitFor(async () => {
    const found = (await list()).querySelector<HTMLElement>('a[aria-current="page"]');
    expect(found).not.toBeNull();
    return found!;
  });
const where = () => screen.getByTestId('where');

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('окно списка: сколько строк рисовать', () => {
  it('первая сотня, показанное по кнопке и — до открытого диалога, до целой сотни', () => {
    expect(windowSize(LIST_STEP, -1, 1250)).toBe(100);
    expect(windowSize(300, -1, 1250)).toBe(300);
    // Открытый диалог — сотый: он последний из показанных; сто первый — уже вторая сотня.
    expect(windowSize(LIST_STEP, 99, 1250)).toBe(100);
    expect(windowSize(LIST_STEP, 100, 1250)).toBe(200);
    expect(windowSize(LIST_STEP, 1249, 1250)).toBe(1250);
    // Показанное по кнопке открытый диалог не урезает.
    expect(windowSize(400, 250, 1250)).toBe(400);
    // Больше, чем есть, не рисуется; меньше сотни не просят.
    expect(windowSize(LIST_STEP, -1, 37)).toBe(37);
    expect(windowSize(LIST_STEP, 240, 250)).toBe(250);
    expect(windowSize(0, -1, 500)).toBe(100);
  });

  it('другой список — другая часть, сторона, состояние или поиск', () => {
    const same = listScope({ ...NO_THREAD_FILTERS, part: 'sales' });
    expect(listScope({ ...NO_THREAD_FILTERS, part: 'sales' })).toBe(same);
    expect(listScope({ ...NO_THREAD_FILTERS, part: 'donors' })).not.toBe(same);
    expect(listScope({ ...NO_THREAD_FILTERS, part: 'sales', state: 'waiting' })).not.toBe(same);
    expect(listScope({ ...NO_THREAD_FILTERS, part: 'sales', search: 'site' })).not.toBe(same);
    expect(listScope({ ...NO_THREAD_FILTERS, role: 'donors' })).not.toBe(
      listScope(NO_THREAD_FILTERS),
    );
  });
});

describe('длинный список диалогов: первые сто и «Показать ещё»', () => {
  it('нарисованы сто строк, кнопка называет следующую сотню и сколько осталось', async () => {
    const user = userEvent.setup();
    open('/threads?stage=sales');

    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    expect(more(100)).toBeInTheDocument();
    expect(screen.getByText('осталось 150')).toBeInTheDocument();
    // Числа — по данным, а не по нарисованному: заголовок, вкладка и фильтр считают все 250.
    expect(screen.getByText('всего 250')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Диалоги — 250' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('все · 250');

    await user.click(more(100)!);
    expect(await drawn()).toHaveLength(200);
    // Осталось меньше сотни — кнопка называет остаток, второй цифры рядом нет.
    expect(more(50)).toBeInTheDocument();
    expect(screen.queryByText(/^осталось/)).toBeNull();

    await user.click(more(50)!);
    expect(await drawn()).toHaveLength(250);
    expect(screen.queryByRole('button', { name: /^Показать ещё/ })).toBeNull();
  });

  it('список за тысячу диалогов: нарисована сотня, остаток — с разрядами', async () => {
    const many = Array.from({ length: 1250 }, (_, at) => thread(at + 1, 'sales', 'waiting'));
    open('/threads?stage=sales', many);

    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    expect(more(100)).toBeInTheDocument();
    // Числа — через `formatNumber`: «1 150», а не «1150».
    expect(screen.getByText('осталось 1 150')).toBeInTheDocument();
    expect(screen.getByText('всего 1 250')).toBeInTheDocument();
  });

  it('список короче сотни — кнопки нет', async () => {
    open('/threads?stage=donors');

    await waitFor(async () => expect(await drawn()).toHaveLength(1));
    expect(screen.queryByRole('button', { name: /^Показать ещё/ })).toBeNull();
  });

  it('поиск и фильтр состояния — счёт с начала, и считают они весь список', async () => {
    const user = userEvent.setup();
    open('/threads?stage=sales');
    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    await user.click(more(100)!);
    expect(await drawn()).toHaveLength(200);

    // «site-1» — 1, 10–19 и 100–199: сто одиннадцать диалогов, из них нарисована сотня.
    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'site-1');
    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    expect(screen.getByText('найдено 111 из 250')).toBeInTheDocument();
    expect(more(11)).toBeInTheDocument();

    // Поиск стёрт — список прежний, но показанное не вернулось: снова первая сотня.
    await user.clear(screen.getByLabelText('Поиск по донору или адресу'));
    await waitFor(() => expect(screen.getByText('всего 250')).toBeInTheDocument());
    expect(await drawn()).toHaveLength(100);
    await user.click(more(100)!);
    expect(await drawn()).toHaveLength(200);

    // Фильтр состояния: ждущих человека — сто пятьдесят, нарисована сотня.
    await user.click(screen.getByRole('textbox', { name: 'Состояние' }));
    await user.click(
      await screen.findByRole('option', { name: 'ждёт человека · 150', hidden: true }),
    );
    await waitFor(() => expect(where()).toHaveTextContent('state=sales_pending'));
    expect(await drawn()).toHaveLength(100);
    expect(screen.getByText('найдено 150 из 250')).toBeInTheDocument();
    expect(more(50)).toBeInTheDocument();
  });

  it('другая часть списка — счёт с начала', async () => {
    const user = userEvent.setup();
    open('/threads?stage=sales');
    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    await user.click(more(100)!);
    expect(await drawn()).toHaveLength(200);

    const menu = within(screen.getByRole('navigation', { name: 'Разделы' }));
    await user.click(menu.getByRole('link', { name: 'Диалоги с рекламодателями' }));

    await screen.findByRole('heading', { name: 'Диалоги с рекламодателями' });
    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    expect(more(20)).toBeInTheDocument();
  });

  it('вернулись из переписки к списку — показано столько же, сколько было', async () => {
    // На узком окне переписка — отдельная страница: список рисуется заново, и с первой сотней
    // прокрутка не дотянулась бы до строки, из которой уходили (`ScrollMemory`).
    const user = userEvent.setup();
    open('/threads?stage=sales');
    await waitFor(async () => expect(await drawn()).toHaveLength(100));
    await user.click(more(100)!);

    await user.click(row(180)!);
    await screen.findByRole('heading', { name: 'sales-site-180.example.test' });
    await user.click(screen.getByRole('link', { name: 'К списку' }));

    await waitFor(async () => expect(await drawn()).toHaveLength(200));
    expect(row(180)).toHaveTextContent('sales-site-180.example.test');
  });
});

describe('открытый диалог виден в списке и за первой сотней', () => {
  it('ссылка на диалог из середины списка: его строка нарисована и выбрана', async () => {
    wide();
    open('/threads/180?stage=sales');

    const selected = await current();
    expect(selected).toHaveAttribute('href', '/threads/180?stage=sales');
    // Сто восьмидесятый — во второй сотне: окно выросло до неё целиком, дальше — по кнопке.
    expect(await drawn()).toHaveLength(200);
    expect(more(50)).toBeInTheDocument();
  });

  it('J за край показанного — следующий диалог открыт, и его строка нарисована', async () => {
    wide();
    const user = userEvent.setup();
    open('/threads/100?stage=sales');
    await current();
    expect(await drawn()).toHaveLength(100);

    await user.keyboard('j');

    await waitFor(() => expect(where()).toHaveTextContent('/threads/101?stage=sales'));
    expect(await current()).toHaveAttribute('href', '/threads/101?stage=sales');
    expect(await drawn()).toHaveLength(200);
  });

  it('«Следующий ждущий» ведёт по данным: к ждущему, чьей строки в окне ещё нет', async () => {
    wide();
    open('/threads/100?stage=sales');

    expect(await screen.findByRole('link', { name: /Следующий ждущий/ })).toHaveAttribute(
      'href',
      '/threads/101?stage=sales',
    );
    expect(await drawn()).toHaveLength(100);
    expect(row(101)).toBeNull();
  });
});

describe('второй ярус строки: адрес и состояние', () => {
  it('в «Диалогах продаж» метка не повторяет слово части — «ждёт человека»', async () => {
    open('/threads?stage=sales');

    const row = (await screen.findByText('sales-site-1.example.test')).closest('a')!;
    expect(within(row).getByText('ждёт человека')).toHaveClass('mantine-Badge-label');
    expect(screen.queryByText('ответ продаж — ждёт человека')).toBeNull();
    // Адрес стоит в строке целиком — его не заменили и не убрали ради метки.
    expect(within(row).getByText('editor@sales-site-1.example.test')).toBeInTheDocument();
  });

  it('у остальных частей метки прежние, и длинной адрес не срезать: ярус переносится', async () => {
    open('/threads?stage=donors');

    const row = (await screen.findByText('donors-site-2001.example.test')).closest('a')!;
    const state = within(row)
      .getByText('цену перезапросили — ждём ответа')
      .closest('.threadRowState')!;
    const address = within(row).getByText('editor@donors-site-2001.example.test');
    const second = state.parentElement!;
    // Адрес и метка — соседи в ярусе, которому разрешён перенос; у адреса — своё место.
    expect(second).toHaveClass('threadRowSecond');
    expect(second.style.getPropertyValue('--group-wrap')).toBe('wrap');
    expect(address.closest('.threadRowWho')!.parentElement).toBe(second);
    expect(address.closest('.threadRowWho')!.nextElementSibling).toBe(state);

    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');
    // Адресу положено место, метка не сжимается и, не влезая рядом, встаёт ниже у правого края.
    expect(glass).toMatch(/\.threadRowWho \{\s*flex: 1 1 5\.5rem;\s*min-width: 0;/);
    expect(glass).toMatch(/\.threadRowState \{\s*margin-inline-start: auto;/);
    expect(glass).toMatch(
      /\.threadRowWhen,\s*\.threadRowState \{\s*flex-shrink: 0;\s*white-space: nowrap;/,
    );
  });

  it('у списка без части и в фильтре вне части состояние продаж названо полностью', async () => {
    // Короткое имя — только там, где слово «продаж» уже сказал заголовок списка.
    open('/threads?state=sales_pending');

    expect(
      await screen.findByText('В состоянии «ответ продаж — ждёт человека» диалогов нет.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue(
      'ответ продаж — ждёт человека · 0',
    );
  });
});
