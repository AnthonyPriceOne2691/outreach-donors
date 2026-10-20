/**
 * Тематика на экранах: строка под доменом, фильтр над таблицей, токены в смете, метка
 * «нет ответа» у прогона.
 *
 * Проверяется то, что видно и что уходит на сервер: у «Доноров» фильтр — параметром
 * запроса и в адресе, пустой результат называет тему словами; у очереди прогона — по
 * пришедшим строкам тем же правилом, что сервер (тема основная или вторая). Подписи тем —
 * присланные сервером, своей копии у экрана нет.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { TopicCount, TopicFields } from '../api/topics';
import type { Forecast } from '../api/types';
import {
  conditionsOf,
  NO_FILTERS,
  queryOf,
  readFilters,
  writeFilters,
} from '../donors/donorFilters';
import { spendOperation } from '../settings/usageLabels';
import { CONTACTS_STATE, page, ROWS } from '../test/donorFixtures';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Call, Recorded } from '../test/server';
import { theme } from '../theme';
import { TopicFilter } from './TopicFilter';
import { TopicLine } from './TopicLine';
import { TopicSilence, TopicTokens } from './TopicNotes';
import { anyTopic, hasTopic, topicsOf, topicWords } from './topicRows';

const NEWS = { code: 'news', label: 'Новости' };
const FINANCE = { code: 'finance', label: 'Финансы' };
const FOOD = { code: 'food', label: 'Еда и напитки' };

function plain(ui: ReactElement) {
  return render(<MantineProvider theme={theme}>{ui}</MantineProvider>);
}

/** Пробелы подписи — неразрывные (`TopicLine`): сравниваем по обычному. */
function spaced(text: string | null | undefined): string {
  return (text ?? '').replace(/\u00a0/g, ' ');
}

describe('тематика: строка под доменом', () => {
  it('основная и вторая тема, почему — в «i», и щелчок по «i» не уходит в строку', () => {
    const opened = vi.fn();
    plain(
      <div onClick={opened}>
        <TopicLine row={{ topic: NEWS, topic_secondary: FOOD, topic_reason: 'городская газета' }} />
      </div>,
    );

    const line = document.querySelector('.topicLine');
    expect(spaced(line?.textContent)).toBe('Тематика: Новости · Еда и напитки');
    fireEvent.click(screen.getByRole('button', { name: 'Почему тематика «Новости»' }));
    expect(opened).not.toHaveBeenCalled();
  });

  it('без темы — ничего, а не прочерк', () => {
    plain(<TopicLine row={{ topic: null }} />);
    expect(document.querySelector('.topicLine')).toBeNull();
  });
});

describe('тематика: пункты по строкам очереди', () => {
  const rows: TopicFields[] = [
    { topic: NEWS, topic_secondary: FINANCE },
    { topic: FINANCE },
    { topic: FOOD },
    { topic: null },
  ];

  it('тема — основная или вторая; «не определена» — последней', () => {
    expect(topicsOf(rows)).toEqual([
      { ...FINANCE, count: 2 },
      { ...FOOD, count: 1 },
      { ...NEWS, count: 1 },
      { code: 'none', label: 'не определена', count: 1 },
    ]);
    expect(rows.filter((row) => hasTopic(row, 'finance'))).toHaveLength(2);
    expect(rows.filter((row) => hasTopic(row, 'none'))).toHaveLength(1);
  });

  it('фильтр нужен, только когда тема есть хоть у одной строки', () => {
    expect(anyTopic(topicsOf([{ topic: null }]))).toBe(false);
    expect(anyTopic(topicsOf(rows))).toBe(true);
  });

  it('тема словами — подписью пункта, «не определена» — так и сказано', () => {
    const items = topicsOf(rows);
    expect(topicWords('finance', items)).toBe('тематика «Финансы»');
    expect(topicWords('none', items)).toBe('тематика не определена');
    expect(topicWords('retired', items)).toBe('тематика «retired»');
  });
});

describe('тематика: фильтр', () => {
  const items: TopicCount[] = [
    { ...FINANCE, count: 12 },
    { code: 'none', label: 'не определена', count: 3 },
  ];

  it('в поле — название, в пунктах — счётчики; «все» — не сужать', async () => {
    const chosen = vi.fn();
    plain(<TopicFilter value="finance" items={items} onChange={chosen} />);
    const field = screen.getByRole('textbox', { name: 'Тематика' });
    expect(field).toHaveValue('Финансы');

    await userEvent.click(field);
    const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
    expect(list.getByRole('option', { name: 'Финансы · 12', hidden: true })).toBeInTheDocument();
    await userEvent.click(list.getByRole('option', { name: 'все', hidden: true }));
    expect(chosen).toHaveBeenLastCalledWith(null);
  });

  it('тема из адреса, которой у строк нет, — всё равно пункт', () => {
    plain(<TopicFilter value="pets" items={items} onChange={vi.fn()} />);
    expect(screen.getByRole('textbox', { name: 'Тематика' })).toHaveValue('pets');
  });
});

describe('тематика: адрес и запрос «Доноров»', () => {
  it('код — в адрес и в запрос, назад читается то же; чужой формы — не сужать', () => {
    const filters = { ...NO_FILTERS, topic: 'real_estate' };
    expect(writeFilters(filters).toString()).toBe('topic=real_estate');
    expect(readFilters(writeFilters(filters))).toEqual(filters);
    expect(queryOf(filters)).toEqual({ topic: 'real_estate' });
    expect(readFilters(new URLSearchParams('topic=Финансы')).topic).toBeNull();
    expect(
      conditionsOf(filters, [{ code: 'real_estate', label: 'Недвижимость', count: 4 }]),
    ).toEqual(['тематика «Недвижимость»']);
  });
});

const FORECAST = { expected_domains: 26, topic_tokens: 52_000, topic_tokens_per_site: 2_000 };

describe('тематика: смета и история прогонов', () => {
  it('смета называет токены тематики и говорит, оценка это или замер', () => {
    const { rerender } = plain(<TopicTokens forecast={FORECAST as unknown as Forecast} />);
    const line = document.querySelector('.topicTokens');
    expect(spaced(line?.textContent)).toContain('по оценке — замера ещё не было');
    expect(spaced(line?.textContent)).toContain('2 000 на сайт');

    rerender(
      <MantineProvider theme={theme}>
        <TopicTokens forecast={{ ...FORECAST, topic_measured: true } as unknown as Forecast} />
      </MantineProvider>,
    );
    expect(document.querySelector('.topicTokens')?.textContent).toContain(
      'по замеру прошлых прогонов',
    );
  });

  it('тематика не определяется — строки нет, а не «0 токенов»', () => {
    plain(<TopicTokens forecast={{ topic_tokens: 0 } as unknown as Forecast} />);
    expect(document.querySelector('.topicTokens')).toBeNull();
  });

  it('модель не ответила — меткой с причиной в подсказке; без ветки — тишина', () => {
    const topics = { unanswered: 3, unanswered_reason: 'модель, отказ (чинить): HTTP 401' };
    plain(<TopicSilence run={{ stats: { topics } }} />);
    const badge = screen.getByText('тематика: нет ответа 3');
    expect(badge.closest('[title]')).toHaveAttribute('title', topics.unanswered_reason);

    plain(<TopicSilence run={{ stats: { topics: { unanswered: 0 } } }} />);
    plain(<TopicSilence run={{ stats: null }} />);
    expect(screen.getAllByText(/тематика: нет ответа/)).toHaveLength(1);
  });

  it('ответ не по форме — та же метка: темы нет и у этих, причина — первая названная', () => {
    const topics = {
      unanswered: 1,
      unanswered_reason: null,
      unreadable: 2,
      unreadable_reason: 'ответ модели не разобран: не JSON',
    };
    plain(<TopicSilence run={{ stats: { topics } }} />);
    const badge = screen.getByText('тематика: нет ответа 3');
    expect(badge.closest('[title]')).toHaveAttribute('title', topics.unreadable_reason);
  });

  it('строка журнала расхода — словами, тематика — среди операций модели', () => {
    expect(spendOperation('site_topic')).toBe('тематика сайтов');
    expect(spendOperation('crawl_proxy')).toBe('трафик прокси обхода');
    expect(spendOperation('site_judge')).toBe('судья площадок');
  });
});

// --- на экранах ---------------------------------------------------------------------

const TOPICS: TopicCount[] = [
  { ...FINANCE, count: 1 },
  { code: 'none', label: 'не определена', count: 2 },
];

function donorsAt(query: string): string {
  return `GET /api/donors?${query}`;
}

function donorCalls(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/donors?'))
    .map((call: Call) => call.path);
}

describe('тематика на «Донорах»', () => {
  it('тема — под доменом, фильтр — над таблицей, выбор — параметром запроса', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const rows = [{ ...ROWS[0]!, topic: FINANCE, topic_reason: 'кредиты' }, ROWS[1]!, ROWS[2]!];
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/contacts': { body: CONTACTS_STATE },
      [donorsAt('limit=20&offset=0')]: { body: page({ rows, topics: TOPICS }) },
      [donorsAt('topic=finance&limit=20&offset=0')]: {
        body: page({ rows: [rows[0]!], total: 1, topics: TOPICS }),
      },
    });
    renderWith(<AppRoutes />, '/donors');
    await screen.findByText('good.example.test');

    expect(document.querySelector('[data-topic="finance"]')).not.toBeNull();
    const field = screen.getByRole('textbox', { name: 'Тематика' });
    await userEvent.click(field);
    const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
    await userEvent.click(await list.findByRole('option', { name: 'Финансы · 1', hidden: true }));

    await screen.findByText('найдено 1 из 3');
    expect(donorCalls(recorded)).toContain('/api/donors?topic=finance&limit=20&offset=0');
  });

  it('тем нет ни у кого — фильтра нет', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/contacts': { body: CONTACTS_STATE },
      [donorsAt('limit=20&offset=0')]: {
        body: page({ topics: [{ code: 'none', label: 'не определена', count: 3 }] }),
      },
    });
    renderWith(<AppRoutes />, '/donors');
    await screen.findByText('good.example.test');

    expect(screen.queryByRole('textbox', { name: 'Тематика' })).toBeNull();
  });
});

const MACHINE = {
  intent: 'editorial_ads',
  recommendation: 'accept',
  decided_by: 'model',
  quote: 'Latest business news',
  reason: null,
  source_url: null,
  home_shop: [],
  home_reached: true,
  judged_at: '2026-10-20T09:00:00Z',
};

function candidate(id: number, host: string, topic: TopicFields) {
  return {
    candidate_id: id,
    domain_id: id * 10,
    host,
    status: 'pending',
    tier: 'likely',
    carried: false,
    decided_by: null,
    decided_at: null,
    note: null,
    dr: 40,
    org_traffic: 9000,
    geo: 'ca',
    geo_top_share: 0.4,
    contact_status: null,
    found_by: ['toronto business news'],
    sells: null,
    sells_by: null,
    machine: MACHINE,
    seller: { answer: null, answered_at: null, price: null, currency: null },
    ...topic,
  };
}

const ACCURACY = {
  decided: 0,
  by_advice: {},
  by_layer: {},
  by_intent: {},
  unjudged: 0,
  asked_to_review: 0,
  auto_accept_ready: false,
  auto_accept_precision: 0.95,
  auto_accept_min_decisions: 200,
};

describe('тематика на «Рассмотрении»', () => {
  it('фильтр сужает пришедшие строки тем же правилом: тема основная или вторая', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const rows = [
      candidate(1, 'ledger.example.test', { topic: NEWS, topic_secondary: FINANCE }),
      candidate(2, 'money.example.test', { topic: FINANCE }),
      candidate(3, 'kitchen.example.test', { topic: FOOD }),
    ];
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/review/runs/31?status=pending': {
        body: {
          run: { id: 31, country: 'ca', keywords: 6, created_at: '2026-10-20T09:00:00Z' },
          rows,
          counts: { pending: 3, accepted: 0, rejected: 0 },
          hidden: 0,
          keywords: null,
        },
      },
      'GET /api/review/runs/31?status=accepted': {
        body: {
          run: { id: 31, country: 'ca', keywords: 6, created_at: '2026-10-20T09:00:00Z' },
          rows: [],
          counts: { pending: 3, accepted: 0, rejected: 0 },
          hidden: 0,
          keywords: null,
        },
      },
      'GET /api/review/accuracy?run_id=31': { body: ACCURACY },
      'GET /api/review/accuracy': { body: ACCURACY },
    });
    renderWith(<AppRoutes />, '/runs/31/review');
    await screen.findByText('kitchen.example.test');

    const field = screen.getByRole('textbox', { name: 'Тематика' });
    await userEvent.click(field);
    const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
    await userEvent.click(await list.findByRole('option', { name: 'Финансы · 2', hidden: true }));

    expect(screen.getByText('ledger.example.test')).toBeInTheDocument();
    expect(screen.getByText('money.example.test')).toBeInTheDocument();
    expect(screen.queryByText('kitchen.example.test')).toBeNull();

    // Другая вкладка — фильтр заново: выбранной темы там может не быть вовсе.
    await userEvent.click(screen.getByText('Приняты — 0'));
    await screen.findByText('Принятых пока нет.');
    expect(screen.queryByRole('textbox', { name: 'Тематика' })).toBeNull();
  });
});
