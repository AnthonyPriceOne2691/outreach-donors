/**
 * Экран расхода: у каждого провайдера своя валюта счёта.
 *
 * Главное здесь — не показывать «0.00 $» там, где платят не деньгами.
 * До этого среза так и было: расход на выдачу не записывался вовсе,
 * и экран честно показывал по ней ноль долларов — то есть уверял,
 * что выдача досталась даром.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { operationTitle } from '../api/labels';
import { spendOperation } from './usageLabels';
import { openUsage, SPENDING } from '../test/usageFixtures';

describe('расход', () => {
  it.each([
    ['без права «Продажи» — расход без продаж, и это сказано словами', true],
    ['с правом «Продажи» расход весь — строки о продажах нет', false],
  ])('%s', async (_, hidden) => {
    // Решение Anthony 10.10.2026 (П2б): без права сервер считает статьи, плитки и итог без
    // операций продаж — числа сходятся между собой, но это не весь счёт, и экран так и говорит.
    await openUsage({ ...SPENDING, sales_hidden: hidden });

    expect(screen.queryByText(/Расход продаж не показан/) !== null).toBe(hidden);
  });

  it('остаток у источника выдачи виден отдельно от Ahrefs', async () => {
    await openUsage();

    // Деньги и числа — по-русски: запятая в деньгах, разряды в больших числах.
    expect(screen.getByText(/на счету источника выдачи 51,90 \$/)).toBeInTheDocument();
    expect(screen.getByText(/у провайдера осталось 913\s000/)).toBeInTheDocument();
  });

  it('трата на выдачу показана в деньгах', async () => {
    await openUsage();

    expect(screen.getByText(/Выдача с начала месяца/)).toBeInTheDocument();
    expect(screen.getAllByText(/0,36 \$/).length).toBeGreaterThan(0);
  });

  it('статьи названы словами, а не кодами операций', async () => {
    await openUsage({
      ...SPENDING,
      articles: [
        ...SPENDING.articles,
        { provider: 'llm', operation: 'site_judge', calls: 3, units: 1200, amount_usd: '0' },
        { provider: 'llm', operation: 'brand_new_thing', calls: 1, units: 5, amount_usd: '0' },
      ],
    });

    expect(screen.getByText('судья площадок')).toBeInTheDocument();
    expect(screen.queryByText('site_judge')).not.toBeInTheDocument();
    // Незнакомая операция не пропадает и не выдаёт себя за знакомую.
    expect(screen.getByText('прочее (brand_new_thing)')).toBeInTheDocument();
  });

  it('проверка адресов продаж названа словами, а не кодом', () => {
    // Операция сервера `sales_verify` (`OPERATION_PROVIDERS`, срез продаж 1.4).
    expect(operationTitle('sales_verify')).toBe('проверка адресов продаж');
  });

  it('вид ответа лида продаж назван словами, а не кодом', () => {
    // Операция сервера `sales_reply_kind` (`OPERATION_PROVIDERS`, срез продаж 2.2):
    // без подписи экран показывал «прочее (sales_reply_kind)».
    expect(operationTitle('sales_reply_kind')).toBe('разбор ответов продаж');
  });

  it('переписывание писем продаж названо словами и отдельно от писем доноров', () => {
    // Операция сервера `sales_letter_rewrite` (`OPERATION_PROVIDERS`): свой потолок продаж
    // считает её отдельно от `letter_rewrite` доноров.
    expect(operationTitle('sales_letter_rewrite')).toBe('переписывание писем продаж');
    expect(operationTitle('letter_rewrite')).toBe('переписывание писем');
  });

  it('расход агента продаж назван словами, а не кодом', () => {
    // Операции сервера агента продаж (`OPERATION_PROVIDERS`, срез продаж 3.2a).
    expect(operationTitle('sales_situation')).toBe('ситуация писем продаж');
    expect(operationTitle('sales_draft')).toBe('черновики продаж');
    expect(operationTitle('sales_judge')).toBe('судья черновиков продаж');
  });

  it('расход сбора базы продаж назван словами и отдельно от прогона доноров', () => {
    // Операции сервера сбора базы продаж (`OPERATION_PROVIDERS`, срез продаж 6.1).
    expect(operationTitle('sales_segment_judge')).toBe('судья сегмента продаж');
    expect(operationTitle('sales_serp')).toBe('выдача сбора продаж');
    expect(operationTitle('serp_search')).not.toBe(operationTitle('sales_serp'));
  });

  it('там, где платят не деньгами, нулей в долларах нет', async () => {
    await openUsage({
      ...SPENDING,
      articles: [],
      units_by_provider: { ahrefs: 6416 },
      amount_by_provider: {},
      total_amount: '0',
      serp_spent_by_us: '0',
    });

    // У отправки трат не было — так и сказано словами и прочерком,
    // а не «0.00 $», которое читается как «платили деньгами, и вышло
    // бесплатно». Строка про выдачу — отдельный разговор: там ноль
    // действительно означает ноль долларов.
    expect(screen.getAllByText('трат не было').length).toBeGreaterThan(0);
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('недоступный остаток у выдачи не прячется', async () => {
    await openUsage({
      ...SPENDING,
      serp_left_usd: null,
      serp_left_error: 'Не удалось узнать остаток у источника выдачи.',
    });

    expect(screen.getByText('Не удалось узнать остаток у источника выдачи.')).toBeInTheDocument();
  });
});

describe('плитки расхода', () => {
  it('единица — словом под числом, а не хвостом, который рвётся на телефоне', async () => {
    await openUsage({
      ...SPENDING,
      units_by_provider: { ahrefs: 6416, llm: 2180924, email: 4 },
    });

    const tile = screen.getByText('модель').closest('.mantine-Card-root') as HTMLElement;
    // Число и единица — разными строками: «2 180 924 ток.» не влезало
    // в плитку и ломалось на «2 180 924» и «ток.».
    expect(within(tile).getByText(/^2\s180\s924$/)).toBeInTheDocument();
    // «2 180 924 токена», а не «токенов»: слово согласуется с числом.
    expect(within(tile).getByText('токена')).toBeInTheDocument();
    const mail = screen.getByText('отправка').closest('.mantine-Card-root') as HTMLElement;
    expect(within(mail).getByText('4')).toBeInTheDocument();
    expect(within(mail).getByText('письма')).toBeInTheDocument();
  });

  it('юниты и деньги у одного провайдера — одной подписью', async () => {
    await openUsage({
      ...SPENDING,
      units_by_provider: { ahrefs: 6416 },
      amount_by_provider: { ahrefs: '1.5' },
    });

    const tile = screen
      .getByText('метрики Ahrefs', { selector: 'p' })
      .closest('.mantine-Card-root') as HTMLElement;
    expect(within(tile).getByText(/^юнитов и 1,50 \$$/)).toBeInTheDocument();
  });
});

describe('провайдеры', () => {
  it('поиск адресов у Hunter — своей строкой и плиткой, а незнакомый провайдер не роняет экран', async () => {
    // До 05.10.2026 сервер уже писал расход под `hunter`, а экран о нём не знал:
    // значок провайдера брался из таблицы подписей, и страница падала целиком.
    await openUsage({
      ...SPENDING,
      articles: [
        ...SPENDING.articles,
        { provider: 'hunter', operation: 'contacts_search', calls: 7, units: 7, amount_usd: '0' },
        { provider: 'courier', operation: 'parcel', calls: 1, units: 1, amount_usd: '0' },
      ],
      units_by_provider: { ...SPENDING.units_by_provider, hunter: 7 },
    });

    expect(screen.getByText('7 запр.')).toBeInTheDocument();
    expect(screen.getByText('прочее (courier)')).toBeInTheDocument();
    const tile = screen
      .getByText('адреса Hunter', { selector: 'p' })
      .closest('.mantine-Card-root') as HTMLElement;
    expect(within(tile).getByText('запросов')).toBeInTheDocument();
  });
});

describe('расход по этапам', () => {
  const STAGED = {
    ...SPENDING,
    articles: [
      ...SPENDING.articles,
      {
        provider: 'ahrefs',
        operation: 'batch_metrics',
        units: 50,
        amount_usd: '0',
        calls: 1,
        stage: 'advertisers',
      },
      {
        provider: 'proxy',
        operation: 'crawl_proxy',
        units: 2_000_000,
        amount_usd: '0.0100',
        calls: 9,
        stage: 'advertisers',
      },
      {
        provider: 'email',
        operation: 'letter_send',
        units: 4,
        amount_usd: '0',
        calls: 4,
        stage: null,
      },
    ],
    units_by_provider: { ahrefs: 6466, proxy: 2_000_000, email: 4 },
    amount_by_provider: { serp: '0.3600', proxy: '0.0100' },
    stages: [
      ...SPENDING.stages,
      {
        stage: 'advertisers',
        units_by_provider: { ahrefs: 50, proxy: 2_000_000 },
        amount_usd: '0.0100',
        calls: 10,
      },
      { stage: null, units_by_provider: { email: 4 }, amount_usd: '0', calls: 4 },
    ],
    crawl: { cap_usd: '120.00', spent_usd: '0.0100' },
  };

  it('одна операция у двух этапов — две статьи, и у каждой свой этап', async () => {
    // DR кандидатов Этапа 2 и метрики прогона — обе «метрики доменов»: до этапа у строки
    // расхода они были одной строкой, и Этап 2 не считался отдельно.
    await openUsage(STAGED);

    const rows = screen.getAllByText('метрики доменов').map((cell) => cell.closest('tr'));
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText('Этап 1')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('Этап 2')).toBeInTheDocument();
    expect(screen.getByText('без этапа')).toBeInTheDocument();
  });

  it('разбивка по этапам — каждый провайдер в своей валюте, деньги отдельно', async () => {
    await openUsage(STAGED);

    const panel = screen.getByText('По этапам').closest('.mantine-Card-root') as HTMLElement;
    const second = within(panel).getByText('Этап 2 · рекламодатели').parentElement as HTMLElement;
    expect(
      within(second).getByText(/метрики Ahrefs: 50 юн\. · прокси обхода: 1,9 МБ · 0,01 \$/),
    ).toBeInTheDocument();
    expect(within(panel).getByText('Без этапа')).toBeInTheDocument();
  });

  it('трафик прокси — размером, и плитка прокси есть, только когда по нему тратили', async () => {
    await openUsage(STAGED);

    const tile = screen
      .getByText('прокси обхода', { selector: 'p' })
      .closest('.mantine-Card-root') as HTMLElement;
    expect(within(tile).getByText('1,9 МБ')).toBeInTheDocument();
    expect(screen.queryByText('рендеринг страниц', { selector: 'p' })).not.toBeInTheDocument();
  });

  it('прокси и рендеринг — против своего потолка Этапа 2, отдельно от юнитов', async () => {
    await openUsage(STAGED);

    expect(screen.getByText(/Этап 2 — прокси и рендеринг с начала месяца/).textContent).toMatch(
      /0,01 \$ из 120,00 \$ по своему потолку/,
    );
    expect(
      screen.getByRole('progressbar', { name: 'Прокси и рендеринг Этапа 2 с начала месяца' }),
    ).toBeInTheDocument();
  });

  it('потолка нет — так и сказано, а не «0 из 0»: без него платные уровни не зовутся', async () => {
    await openUsage();

    expect(
      screen.getByText(
        'своего потолка у Этапа 2 нет — прокси и рендеринг не зовутся, пока его не зададут',
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('progressbar', { name: 'Прокси и рендеринг Этапа 2 с начала месяца' }),
    ).not.toBeInTheDocument();
  });

  it('потолок выбран — сказано, что платные уровни стоят до следующего месяца', async () => {
    await openUsage({ ...STAGED, crawl: { cap_usd: '10.00', spent_usd: '10.0000' } });

    expect(screen.getByText(/потолок выбран/)).toBeInTheDocument();
  });

  it('статьи обхода названы словами', () => {
    expect(spendOperation('crawl_proxy')).toBe('трафик прокси обхода');
    expect(spendOperation('crawl_render')).toBe('рендеринг пустых страниц');
    expect(spendOperation('site_judge')).toBe('судья площадок');
  });
});
