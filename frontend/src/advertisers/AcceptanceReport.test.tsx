/**
 * «Приёмка по донорам» (сверка ТЗ 11.10.2026: «по донору 100 рекламодателей с контактами,
 * письма ушли 70% из них»).
 *
 * Проверяется то, ради чего вид: у каждого донора видно, пройден ли порог, а если нет —
 * чего не хватает, словами и с десятой у доли; обход — тем же значком с «i», что на вкладке
 * обхода, а на виду под ним — только доля больше нуля; что нашёл обход — одной колонкой
 * (тест-группа 23.10.2026: «сколько продажных ссылок, статей, сколько спорных»); все доноры
 * вместе — первой строкой таблицы; таблица — в карточку на 1440 с открытым меню (10.11.2026).
 */

import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AcceptanceView, Findings, Tally } from '../api/acceptance';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { COLUMNS, TABLE_MIN_WIDTH, shortOf } from './AcceptanceReport';

const SHORT: Tally = {
  found: 40,
  promoted: 38,
  with_address: 34,
  sent: 20,
  sent_share: 20 / 34,
  accepted: false,
};

const DONE: Tally = {
  found: 130,
  promoted: 130,
  with_address: 120,
  sent: 90,
  sent_share: 0.75,
  accepted: true,
};

const FOUND: Findings = {
  bought: 118,
  pending: 40,
  decided: 12,
  skipped: 230,
  blocked: 41,
  pages: 57,
};

const NOTHING: Findings = { bought: 0, pending: 0, decided: 0, skipped: 0, blocked: 0, pages: 0 };

function report(overrides: Partial<AcceptanceView> = {}): AcceptanceView {
  return {
    donors: [
      {
        host: 'big.example.test',
        crawl: {
          id: 3,
          host: 'big.example.test',
          status: 'done',
          outcome: 'ok',
          stop_reason: 'max_pages',
          pages: 1000,
          max_pages: 1000,
          articles: 900,
          links: 5000,
          resumes: 0,
          requested_by: null,
          created_at: '2026-10-10T10:00:00Z',
          finished_at: '2026-10-10T10:30:00Z',
          reason: null,
          blocked_share: 0.05,
          requests: 1100,
        },
        tally: DONE,
        findings: FOUND,
        supplier: false,
      },
      {
        host: 'small.example.test',
        crawl: null,
        tally: SHORT,
        findings: NOTHING,
        supplier: false,
      },
    ],
    total: 2,
    page: 1,
    limit: 15,
    overall: { ...DONE, found: 160, accepted: true },
    // Спорных много, решённых нет — «решено 0»: «никто ещё не смотрел» — тоже ответ.
    overall_findings: { ...FOUND, bought: 150, pending: 1250, decided: 0, pages: 1057 },
    target_with_address: 100,
    target_sent_share: 0.7,
    ...overrides,
  };
}

async function openReport(view: AcceptanceView = report()) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/crawls/units-stop': { body: null },
    'GET /api/advertisers/acceptance': { body: view },
  });
  renderWith(<AppRoutes />, '/advertisers?view=acceptance');
  await screen.findByRole('heading', { name: 'Приёмка по донорам' });
}

describe('приёмка по донорам', () => {
  it('порог ТЗ — словами сервера', async () => {
    await openReport();

    expect(
      screen.getByText(
        'Порог ТЗ: у донора 100 рекламодателей с адресом, и письмо ушло 70% из них.',
      ),
    ).toBeInTheDocument();
  });

  it('пройденный — мятой; обход — значком с «i», как на вкладке обхода', async () => {
    const user = userEvent.setup();
    await openReport();

    const row = screen.getByText('big.example.test').closest('tr')!;
    expect(within(row).getByText('пройдена')).toBeInTheDocument();
    // Значок — короткий, как на вкладке: «страниц» и почему кончился — в «i», а не под значком.
    expect(within(row).getByText(/^обойдён: 1\s000 из 1\s000$/)).toBeInTheDocument();
    expect(within(row).queryByText(/потолок/)).toBeNull();
    expect(within(row).getByText('90 · 75%')).toBeInTheDocument();

    await user.hover(within(row).getByRole('button', { name: /^Подробнее: обойдён/ }));

    expect(await screen.findByText(/^потолок 1\s000 страниц на донора$/)).toBeInTheDocument();
    expect(screen.getByText(/^закрыто 5% из 1\s100 запросов$/)).toBeInTheDocument();
  });

  it('доля закрытых и отказов на виду — только больше нуля, коротко', async () => {
    const view = report();
    const big = view.donors[0]!;
    const crawl = { ...big.crawl!, blocked_share: 0, failure_share: 0, window: 500 };
    await openReport({
      ...view,
      donors: [
        { ...big, crawl },
        {
          ...big,
          host: 'closing.example.test',
          crawl: { ...crawl, blocked_share: 0.125, failure_share: 0.083 },
        },
      ],
    });

    // Нули — только в «i»: четыре строки нулей под каждым значком были простынёй.
    const calm = screen.getByText('big.example.test').closest('tr')!;
    expect(within(calm).queryByText(/закрыто/)).toBeNull();
    expect(within(calm).queryByText(/403\/429/)).toBeNull();
    const closing = screen.getByText('closing.example.test').closest('tr')!;
    expect(within(closing).getByText('закрыто 12,5%')).toBeInTheDocument();
    expect(within(closing).getByText('403/429/5xx/таймаут — 8,3%')).toBeInTheDocument();
    // Число запросов за долей — в «i», на виду его нет.
    expect(within(closing).queryByText(/запросов/)).toBeNull();
  });

  it('не пройденный — чего не хватает, по строке на условие, с десятой у доли', async () => {
    await openReport();

    const row = screen.getByText('small.example.test').closest('tr')!;
    expect(within(row).getByText('не пройдена')).toBeInTheDocument();
    expect(within(row).getByText('с адресом 34 из 100')).toBeInTheDocument();
    expect(within(row).getByText('ушло 58,8% из 70%')).toBeInTheDocument();
    // Не переведённые адреса не получат — это видно у числа найденных.
    expect(within(row).getByText('переведено 38')).toBeInTheDocument();
    expect(within(row).getByText('не обходили')).toBeInTheDocument();
  });

  it('все доноры вместе — первой строкой, теми же колонками, справочно и без вердикта', async () => {
    await openReport();

    const row = screen.getByText('Все доноры вместе').closest('tr')!;
    expect(row.parentElement!.firstElementChild).toBe(row);
    expect(within(row).getByText('без поставщиков')).toBeInTheDocument();
    expect(cellsOf(row)).toMatchObject({
      Приёмка: 'справочно, не критерий',
      // Рекламодатели «вместе» — по разным доменам, и непереведённые видны так же, как у донора.
      Рекламодателей: '160переведено 130',
      'С адресом': '120',
      'Письмо ушло': '90 · 75%',
    });
    // Обхода у суммы нет — и ячейки с подписью «Обход» тоже.
    expect(cellsOf(row)).not.toHaveProperty('Обход');
    expect(foundOf(row)).toEqual([
      'куплено 150',
      'мимо 230',
      'спорных 1 250',
      'не пишем 41',
      'решено 0',
      'страниц 1 057',
    ]);
    expect(within(row).queryByText(/пройдена/)).toBeNull();
  });

  it('поставщик — помечен, а не «не пройдена»', async () => {
    const view = report();
    await openReport({
      ...view,
      donors: [
        ...view.donors,
        { ...view.donors[1]!, host: 'partner.example.test', supplier: true },
      ],
      total: 3,
    });

    const row = screen.getByText('partner.example.test').closest('tr')!;
    expect(within(row).getByText('поставщик')).toBeInTheDocument();
    // Почему не считается — в «i» вида: строка вердикта — в две строки, а не в четыре.
    expect(within(row).getByText('не считается')).toBeInTheDocument();
    expect(within(row).queryByText('не пройдена')).toBeNull();
  });

  it('обойдённых нет — где обход начинается', async () => {
    await openReport(
      report({
        donors: [],
        total: 0,
        overall: { ...SHORT, found: 0, accepted: false },
        overall_findings: NOTHING,
      }),
    );

    // Обход — на своей вкладке с 29.10.2026, а не панелью в «Проверке».
    expect(screen.getByText(/Обойдённых доноров нет/)).toHaveTextContent(
      'приёмка считается по обходу, а он — на вкладке «Обход доноров».',
    );
    expect(screen.queryByText(/Все доноры вместе/)).toBeNull();
  });
});

/** Где сейчас экран: адрес целиком. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

const PAGER = 'Страницы приёмки';

/** Доноры страницы по именам — с тем же обходом, что у первого в `report`. */
function donorsOf(...hosts: string[]): AcceptanceView['donors'] {
  return hosts.map((host) => ({ ...report().donors[1]!, host }));
}

function openPages(routes: Record<string, { body: AcceptanceView }>, path: string, first: string) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/crawls/units-stop': { body: null },
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  return screen.findByText(first).then(() => recorded);
}

describe('страницы приёмки', () => {
  const FIRST = report({ donors: donorsOf('first.example.test'), total: 16 });
  const SECOND = report({ donors: donorsOf('second.example.test'), total: 16, page: 2 });

  it('одна страница — переключателя нет', async () => {
    await openReport();

    expect(screen.queryByRole('navigation', { name: PAGER })).not.toBeInTheDocument();
  });

  it('доноров больше страницы — переключатель вместо «показано N из M»', async () => {
    await openReport(report({ total: 40 }));

    const pager = screen.getByRole('navigation', { name: PAGER });
    // Страниц три — по размеру, который назвал сервер.
    expect(within(pager).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
    expect(screen.queryByText(/Показано/)).toBeNull();
  });

  it('вторая страница — у сервера, в адресе, и «все доноры вместе» снова первой строкой', async () => {
    const recorded = await openPages(
      {
        'GET /api/advertisers/acceptance': { body: FIRST },
        'GET /api/advertisers/acceptance?page=2': { body: SECOND },
      },
      '/advertisers?view=acceptance',
      'first.example.test',
    );
    const user = userEvent.setup();

    await user.click(
      within(screen.getByRole('navigation', { name: PAGER })).getByRole('button', {
        name: 'Страница 2',
      }),
    );

    await screen.findByText('second.example.test');
    expect(screen.queryByText('first.example.test')).toBeNull();
    expect(recorded.calls.at(-1)?.path).toBe('/api/advertisers/acceptance?page=2');
    expect(screen.getByTestId('where')).toHaveTextContent('/advertisers?view=acceptance&page=2');
    const together = screen.getByText('Все доноры вместе').closest('tr')!;
    expect(together.parentElement!.firstElementChild).toBe(together);
    // Итог — тот же: по всем донорам, а не по строкам страницы.
    expect(cellsOf(together)).toMatchObject({ Рекламодателей: '160переведено 130' });
  });

  it('страница за концом — на последнюю настоящую, а не «обойдённых нет»', async () => {
    const recorded = await openPages(
      {
        'GET /api/advertisers/acceptance?page=9': {
          body: report({ donors: [], total: 16, page: 9 }),
        },
        'GET /api/advertisers/acceptance?page=2': { body: SECOND },
      },
      '/advertisers?view=acceptance&page=9',
      'second.example.test',
    );

    expect(recorded.calls.map((call) => call.path)).toContain('/api/advertisers/acceptance?page=2');
    expect(screen.getByTestId('where')).toHaveTextContent('/advertisers?view=acceptance&page=2');
    expect(screen.queryByText(/Обойдённых доноров нет/)).toBeNull();
  });
});

/** Ячейки строки по подписям колонок — тем же `data-label`, что подписывает их на телефоне. */
function cellsOf(row: HTMLElement): Record<string, string> {
  const cells: Record<string, string> = {};
  for (const cell of row.querySelectorAll<HTMLElement>('td[data-label]')) {
    cells[cell.dataset.label ?? ''] = (cell.textContent ?? '').replace(/\s/g, ' ');
  }
  return cells;
}

/** Что нашёл обход — подписи с числами по порядку сетки: слева направо, сверху вниз. */
function foundOf(row: HTMLElement): string[] {
  const cell = row.querySelector<HTMLElement>("td[data-label='Что нашёл обход']")!;
  return [...cell.querySelectorAll('.foundItem')].map((item) =>
    (item.textContent ?? '').replace(/\s/g, ' '),
  );
}

describe('что нашёл обход', () => {
  it('вердикт — второй колонкой, сразу за донором; что нашёл обход — одной колонкой', async () => {
    await openReport();

    expect(screen.getByRole('table')).toHaveClass('stickyName');
    expect(
      screen.getAllByRole('columnheader').map((head) => head.textContent?.replace('\u00ad', '')),
    ).toEqual([
      'Донор',
      'Приёмка',
      'Обход',
      'Что нашёл обход',
      'Рекламодателей',
      'С адресом',
      'Письмо ушло',
    ]);
  });

  it('таблица — в карточку на 1440 с открытым меню (1 093 px), без прокрутки вбок', () => {
    const widths = COLUMNS.map((column) => parseFloat(column.width ?? '0') * 16);
    expect(widths.reduce((sum, width) => sum + width, 0)).toBe(TABLE_MIN_WIDTH);
    expect(TABLE_MIN_WIDTH).toBeLessThanOrEqual(1093);
  });

  it('донор залит, только пока таблица уехала вбок (замечание 10.11: «выделяется чёрным»)', async () => {
    await openReport();
    const area = screen.getByRole('table').parentElement as HTMLElement;
    const filled = () => screen.getByRole('table').closest('.stickyScrolled');
    expect(filled()).toBeNull();

    Object.defineProperty(area, 'scrollLeft', { configurable: true, value: 120 });
    fireEvent.scroll(area);
    expect(filled()).not.toBeNull();

    Object.defineProperty(area, 'scrollLeft', { configurable: true, value: 0 });
    fireEvent.scroll(area);
    expect(filled()).toBeNull();
  });

  it('у донора — куплено, мимо, спорные, кому не пишем, сколько спорных решено, страницы', async () => {
    await openReport();

    const row = screen.getByText('big.example.test').closest('tr')!;
    expect(foundOf(row)).toEqual([
      'куплено 118',
      'мимо 230',
      'спорных 40',
      'не пишем 41',
      'решено 12',
      'страниц 57',
    ]);
    expect(cellsOf(row)).toMatchObject({ Рекламодателей: '130' });
  });

  it('пусто — нули, а «решено» без спорных не пишется, и страницы стоят на своём месте', async () => {
    await openReport();

    const row = screen.getByText('small.example.test').closest('tr')!;
    expect(foundOf(row)).toEqual(['куплено 0', 'мимо 0', 'спорных 0', 'не пишем 0', 'страниц 0']);
    expect(within(row).queryByText(/решено/)).toBeNull();
    // Под спорными — пустое место, а не страницы: сетка у всех строк одна.
    const grid = row.querySelector('.foundGrid')!;
    expect(grid.children).toHaveLength(6);
    expect(grid.children[4]).toBeEmptyDOMElement();
  });

  it('обход — в блоке одной ширины у каждой строки: «i» соседних строк — на одной линии', async () => {
    await openReport();

    for (const host of ['big.example.test', 'small.example.test']) {
      const cell = screen
        .getByText(host)
        .closest('tr')!
        .querySelector<HTMLElement>("td[data-label='Обход']")!;
      expect(cell.querySelector('.crawlSlot')).not.toBeNull();
    }
  });

  it('что значит каждая колонка — в «i» у заголовка экрана', async () => {
    const user = userEvent.setup();
    await openReport();

    await user.hover(
      screen.getByRole('button', { name: 'Как устроен экран «Приёмка по донорам»' }),
    );

    expect(await screen.findByText(/Куплено — скоринг уверен, что это купленное/)).toBeDefined();
    expect(screen.getByText(/Спорных — решает человек/)).toBeInTheDocument();
    expect(screen.getByText(/Мимо — не реклама/)).toBeInTheDocument();
  });
});

describe('чего не хватает до порога', () => {
  const view = report();

  it.each([
    [{ ...SHORT, with_address: 100, sent: 69, sent_share: 0.69 }, ['ушло 69% из 70%']],
    [{ ...SHORT, with_address: 99, sent: 99, sent_share: 1 }, ['с адресом 99 из 100']],
    // Адресов нет — и доли нет: про неё молчим, а не пишем «ушло —».
    [{ ...SHORT, with_address: 0, sent: 0, sent_share: null }, ['с адресом 0 из 100']],
    [{ ...SHORT, with_address: 100, sent: 70, sent_share: 0.7 }, []],
  ])('%o → %o', (tally, said) => {
    expect(shortOf(tally, view)).toEqual(said);
  });
});
