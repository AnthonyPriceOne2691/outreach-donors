/**
 * Отчёт прогона на экране: проверено, прошло, отсеяно по каждому порогу, юниты и деньги
 * (ТЗ: «после прогона — отчёт…»; сверка ТЗ 11.10.2026).
 *
 * Проверяется то, ради чего экран заведён: разбивка «подходит / не подходит / не
 * проверен», каждый порог — со значением этого прогона, расход — единицей своего
 * провайдера, и у идущего прогона — не нули, а слова «итога ещё нет».
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { RunReportView } from '../api/runReport';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { history } from '../test/runFixtures';
import { serve } from '../test/server';

const RUN = {
  id: 27,
  status: 'done' as const,
  country: 'us',
  keywords: 12,
  estimated_units: 1500,
  actual_units: 1454,
  estimate_error: -0.03,
  stats: null,
  started_at: '2026-10-11T11:05:00Z',
  alive_at: '2026-10-11T11:40:00Z',
  hosts: 120,
  reviewed: 0,
  disagreements: 0,
  queue: { pending: 9, accepted: 2 },
  reason: null,
  started_by: 'ivan@team.example.test',
};

const REPORT: RunReportView = {
  run: RUN,
  thresholds: {
    min_dr: 25,
    min_org_traffic: 700,
    min_refdomains: 150,
    min_keywords: 400,
    geo_top_n: 3,
    geo_min_share: 0.25,
  },
  domains: {
    found: 120,
    excluded: 3,
    fresh: 40,
    to_check: 77,
    checked: 77,
    suitable: 30,
    unsuitable: 41,
    unchecked: 6,
    not_reached: 0,
    judge_enforced: false,
    resumes: 0,
  },
  rejected: [
    { threshold: 'dr', count: 12, said: null },
    { threshold: 'org_traffic', count: 20, said: null },
    { threshold: 'keywords', count: 6, said: null },
    { threshold: 'refdomains', count: 0, said: null },
    { threshold: 'geo', count: 2, said: null },
    { threshold: 'other', count: 1, said: 'без причины' },
  ],
  spending: [
    { provider: 'llm', operation: 'site_judge', units: 12345, amount_usd: '0', calls: 3 },
    { provider: 'ahrefs', operation: 'batch_metrics', units: 1234, amount_usd: '0', calls: 4 },
    { provider: 'ahrefs', operation: 'by_country', units: 220, amount_usd: '0', calls: 4 },
    { provider: 'serp', operation: 'serp_search', units: 0, amount_usd: '0.42', calls: 2 },
  ],
  free_requests: 5,
};

function openReport(report: unknown, path = '/runs/27') {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/27/report': { body: report },
  });
  renderWith(<AppRoutes />, path);
  return recorded;
}

describe('отчёт прогона', () => {
  it('домены — разбивкой «подходит / не подходит / не проверен» и путь до неё', async () => {
    openReport(REPORT);

    expect(await screen.findByRole('heading', { name: 'Отчёт прогона №27' })).toBeInTheDocument();
    expect(
      screen.getByText(/Из выдачи — 120 доменов: исключено 3, проверены раньше 40/),
    ).toBeInTheDocument();
    // Кто, когда, сколько — ТЗ: кто запустил, по записи журнала о запуске.
    expect(screen.getByText(/запустил ivan@team\.example\.test/)).toBeInTheDocument();
    for (const [title, value] of [
      ['Проверено в Ahrefs', '77'],
      ['Подходит', '30'],
      ['Не подходит', '41'],
      ['Не проверен', '6'],
    ] as const) {
      const tile = screen.getByText(title).closest('.metricTile');
      expect(tile, title).not.toBeNull();
      expect(within(tile as HTMLElement).getByText(value)).toBeInTheDocument();
    }
  });

  it('каждый порог — со значением этого прогона и числом отсеянных, незнакомая причина — как записана', async () => {
    openReport(REPORT);

    const card = (await screen.findByRole('heading', { name: 'Отсеяно по порогам' })).closest(
      '.mantine-Card-root',
    ) as HTMLElement;
    const cells = within(within(card).getByRole('table'))
      .getAllByRole('row')
      .slice(1)
      .map((row) =>
        within(row)
          .getAllByRole('cell')
          .map((cell) => cell.textContent),
      );
    expect(cells).toEqual([
      ['DR не ниже 25', '12'],
      ['Органический трафик не ниже 700 в месяц', '20'],
      ['Ключей в органике не меньше 400', '6'],
      ['Реф. доменов не меньше 150', '0'],
      ['Регион: США — в топ-3 стран по трафику или от 25% трафика', '2'],
      ['Другая причина: «без причины»', '1'],
    ]);
  });

  it('расход — единицей своего провайдера: Ahrefs юнитами со сметой, выдача деньгами, модель токенами', async () => {
    openReport(REPORT);

    const ahrefs = (await screen.findByText('метрики Ahrefs', { selector: '.metricTitle' }))
      .parentElement as HTMLElement;
    expect(within(ahrefs).getByText('1 454')).toBeInTheDocument();
    expect(within(ahrefs).getByText('юнита · смета 1 500')).toBeInTheDocument();
    const serp = screen.getByText('выдача', { selector: '.metricTitle' })
      .parentElement as HTMLElement;
    expect(within(serp).getByText('0,42 $')).toBeInTheDocument();
    const model = screen.getByText('модель', { selector: '.metricTitle' })
      .parentElement as HTMLElement;
    expect(within(model).getByText('12 345')).toBeInTheDocument();
    expect(within(model).getByText('токенов')).toBeInTheDocument();
    // Статьи — словами, а не кодами операций, и по порядку провайдеров прогона.
    const articles = screen.getAllByRole('table').at(-1) as HTMLElement;
    const names = within(articles)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[0]?.textContent);
    expect(names).toEqual([
      'метрики доменов',
      'трафик по странам',
      'поиск по выдаче',
      'судья площадок',
    ]);
    expect(screen.getByText(/Бесплатно из кэша Ahrefs: 5 запросов/)).toBeInTheDocument();
  });

  it('у идущего прогона — не нули, а «итога ещё нет»; расход уже потраченного виден', async () => {
    openReport({
      ...REPORT,
      run: { ...RUN, status: 'running', actual_units: null, estimate_error: null },
      domains: null,
      rejected: [],
      spending: [REPORT.spending[1]],
      free_requests: 0,
    });

    expect(
      await screen.findByText(/Прогон ещё идёт — числа доменов появятся, когда он закончится/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Подходит')).not.toBeInTheDocument();
    expect(screen.queryByText('Отсеяно по порогам')).not.toBeInTheDocument();
    expect(screen.getByText('метрики доменов')).toBeInTheDocument();
  });

  it('новые, до Ahrefs не дошедшие, названы: у законченного с режущим судьёй — его отрезанные', async () => {
    const domains = { ...REPORT.domains!, checked: 70, suitable: 23, not_reached: 7 };
    openReport({ ...REPORT, domains: { ...domains, judge_enforced: true } });

    expect(await screen.findByText(/Отрезал судья до Ahrefs: 7/)).toBeInTheDocument();
  });

  it('у остановленного — «не дошли до Ahrefs», а продолженный после сбоя говорит, чьи числа', async () => {
    const domains = { ...REPORT.domains!, checked: 70, not_reached: 7, resumes: 2 };
    openReport({ ...REPORT, run: { ...RUN, status: 'stopped' }, domains });

    expect(await screen.findByText(/Не дошли до Ahrefs: 7 из 77/)).toBeInTheDocument();
    expect(screen.getByText(/Прогон продолжали после сбоя \(2 раза\)/)).toBeInTheDocument();
  });

  it('идёт продолжение, а отчёт — прошлой попытки: так и сказано, а не «остановился»', async () => {
    // Попытка, прерванная сбоем, записала свой отчёт; продолжение напишет свой в конце.
    const domains = { ...REPORT.domains!, checked: 70, not_reached: 7, resumes: 1 };
    openReport({ ...REPORT, run: { ...RUN, status: 'running' }, domains });

    expect(await screen.findByText(/Идёт продолжение — числа прошлой попытки/)).toBeInTheDocument();
    expect(screen.getByText(/их проверяет продолжение/)).toBeInTheDocument();
    expect(screen.queryByText(/остановился раньше/)).not.toBeInTheDocument();
  });

  it('негодный номер в адресе — словами, без запроса', async () => {
    const recorded = openReport(REPORT, '/runs/abc');

    expect(await screen.findByRole('heading', { name: 'Такого прогона нет' })).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path.includes('/report'))).toBe(false);
  });
});

describe('история прогонов → отчёт', () => {
  it('номер прогона в строке — ссылка на его отчёт, и «К прогонам» ведёт на ту же страницу', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/runs/countries': { body: ['us'] },
      'GET /api/runs?page=2': history([RUN], { page: 2, total: 12 }),
      'GET /api/keywords/yield?country=us': { body: [] },
      'GET /api/runs/27/report': { body: REPORT },
    });
    renderWith(<AppRoutes />, '/run?page=2');
    const user = userEvent.setup();

    // В строке истории — кто запустил: имя до «@», почта целиком — в подсказке.
    const starter = await screen.findByText('запустил ivan');
    expect(starter).toHaveAttribute('title', 'ivan@team.example.test');
    await user.click(await screen.findByRole('link', { name: 'Отчёт прогона №27' }));

    expect(await screen.findByRole('heading', { name: 'Отчёт прогона №27' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'К прогонам' })).toHaveAttribute('href', '/run?page=2');
  });
});
