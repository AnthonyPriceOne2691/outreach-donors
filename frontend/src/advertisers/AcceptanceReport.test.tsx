/**
 * «Приёмка по донорам» (сверка ТЗ 11.10.2026: «по донору 100 рекламодателей с контактами,
 * письма ушли 70% из них»).
 *
 * Проверяется то, ради чего вид: у каждого донора видно, пройден ли порог, а если нет —
 * чего не хватает, словами и с десятой у доли; обход — тем же видом, что на панели
 * обхода; что нашёл обход — колонками (тест-группа 23.10.2026: «сколько продажных ссылок,
 * статей, сколько спорных»); все доноры вместе — первой строкой таблицы.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AcceptanceView, Findings, Tally } from '../api/acceptance';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { shortOf } from './AcceptanceReport';

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

  it('пройденный — мятой; обход — тем же видом, что на панели, с долей закрытых', async () => {
    await openReport();

    const row = screen.getByText('big.example.test').closest('tr')!;
    expect(within(row).getByText('пройдена')).toBeInTheDocument();
    expect(within(row).getByText(/обойдён: 1\s000 страниц/)).toBeInTheDocument();
    expect(within(row).getByText(/закрыто 5% из 1\s100 запросов/)).toBeInTheDocument();
    expect(within(row).getByText('90 · 75%')).toBeInTheDocument();
  });

  it('не пройденный — чего не хватает, с десятой у доли', async () => {
    await openReport();

    const row = screen.getByText('small.example.test').closest('tr')!;
    expect(within(row).getByText('не пройдена')).toBeInTheDocument();
    expect(within(row).getByText('с адресом 34 из 100; ушло 58,8% из 70%')).toBeInTheDocument();
    // Не переведённые адреса не получат — это видно у числа найденных.
    expect(within(row).getByText('переведено 38')).toBeInTheDocument();
    expect(within(row).getByText('не обходили')).toBeInTheDocument();
  });

  it('все доноры вместе — первой строкой, теми же колонками, справочно и без вердикта', async () => {
    await openReport();

    const row = screen.getByText('Все доноры вместе').closest('tr')!;
    expect(row.parentElement!.firstElementChild).toBe(row);
    expect(within(row).getByText('без поставщиков')).toBeInTheDocument();
    expect(cellsOf(row)).toEqual({
      Куплено: '150',
      Спорных: '1 250решено 0',
      Мимо: '230',
      'Кому не пишем': '41',
      Страниц: '1 057',
      // Рекламодатели «вместе» — по разным доменам, и непереведённые видны так же, как у донора.
      Рекламодателей: '160переведено 130',
      'С адресом': '120',
      'Письмо ушло': '90 · 75%',
      Приёмка: 'справочно, не критерий',
    });
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
    expect(
      within(row).getByText('его рекламодателей не трогаем — не считается'),
    ).toBeInTheDocument();
    expect(within(row).queryByText('не пройдена')).toBeNull();
  });

  it('доноров больше, чем строк, — «показано N из M»', async () => {
    await openReport(report({ total: 315 }));

    expect(
      screen.getByText('Показано 2 из 315: сверху — у кого рекламодателей больше.'),
    ).toBeInTheDocument();
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

    expect(screen.getByText(/Обойдённых доноров нет/)).toBeInTheDocument();
    expect(screen.queryByText(/Все доноры вместе/)).toBeNull();
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

describe('что нашёл обход', () => {
  it('колонки — между обходом и рекламодателями, по порядку; донор — липкой колонкой', async () => {
    await openReport();

    expect(screen.getByRole('table')).toHaveClass('stickyName');
    expect(screen.getAllByRole('columnheader').map((head) => head.textContent)).toEqual([
      'Донор',
      'Обход',
      'Куплено',
      'Спорных',
      'Мимо',
      'Кому не пишем',
      'Страниц',
      'Рекламодателей',
      'С адресом',
      'Письмо ушло',
      'Приёмка',
    ]);
  });

  it('у донора — куплено, спорные и сколько из них решено, мимо, кому не пишем, страницы', async () => {
    await openReport();

    const row = screen.getByText('big.example.test').closest('tr')!;
    expect(cellsOf(row)).toMatchObject({
      Куплено: '118',
      Спорных: '40решено 12',
      Мимо: '230',
      'Кому не пишем': '41',
      Страниц: '57',
      Рекламодателей: '130',
    });
    expect(within(row).getByText('решено 12')).toBeInTheDocument();
  });

  it('пусто — нули, а «решено» без спорных не пишется', async () => {
    await openReport();

    const row = screen.getByText('small.example.test').closest('tr')!;
    expect(cellsOf(row)).toMatchObject({
      Куплено: '0',
      Спорных: '0',
      Мимо: '0',
      'Кому не пишем': '0',
      Страниц: '0',
    });
    expect(within(row).queryByText(/решено/)).toBeNull();
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
