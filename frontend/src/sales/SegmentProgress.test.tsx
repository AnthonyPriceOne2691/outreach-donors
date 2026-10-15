/**
 * Ход прогона сбора на экране (тест прогона 14.10): стадия с ходом «X из Y», сколько идёт и
 * когда отмечался; «остановлен» без задачи — словами сервера и «Продолжить»; «Задачу некому
 * взять»; опрос отчёта и списка, пока прогон идёт, и после продолжения; `?run=N`; баги экрана
 * 8a, 8c, 8e, 8f, 8g, 8h, 8i; длинная причина «на проверку» — в своей колонке (находка стенда).
 * Ответы сервера — записанные (`serve()`); домены выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { NO_LEAD_FILTERS, readLeadFilters, writeLeadFilters } from './leadFilters';
import type { SegmentCandidate, SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import { listPoll, runFacts, runPoll } from './segmentData';
import { ago, lasting, pulse, runState, stageBadge, stageLine, stoppedAt } from './segmentStage';

const SCREEN_WAIT = { timeout: 5000 };
/** «Сейчас» тестов — часы подменены только у `Date`, таймеры опроса настоящие. */
const NOW = new Date('2026-10-14T12:10:00+00:00');

function before(seconds: number): string {
  return new Date(NOW.getTime() - seconds * 1000).toISOString();
}

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 5,
      name: 'сайты EN',
      description: 'Интернет-магазины велосипедов.',
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 0, ready: 3, rejected: 0 },
      total: 3,
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

const DONE: SegmentRunRow = {
  id: 12,
  hypothesis_id: 5,
  created_at: '2026-10-14T09:30:00+00:00',
  finished_at: '2026-10-14T09:41:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'us',
  language: 'en',
  depth_pages: 1,
  keywords: 3,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: 'sales-segment-12',
  alive_at: '2026-10-14T09:41:00+00:00',
  active: false,
  progress: null,
  watch: null,
};

/** Прогон на судье: 12 из 60 сайтов, проход идёт 4 минуты, отмечался 5 с назад. */
const JUDGING: SegmentRunRow = {
  ...DONE,
  stage: 'judge',
  finished_at: null,
  alive_at: before(5),
  active: true,
  progress: {
    stage: 'judge',
    done: 12,
    total: 60,
    started_at: before(60),
    updated_at: before(60),
    pass_started_at: before(240),
  },
  watch: { going: true, stalled: null, workers: 1, silent: false },
};

const SITE: SegmentCandidate = {
  id: 41,
  host: 'velo-news-sample.example.test',
  page_url: 'https://velo-news-sample.example.test/catalog',
  page_title: 'Новости велоспорта',
  keyword: 'купить горный велосипед',
  lane: 'review',
  verdict: 'reject',
  reason: 'судья в наблюдении отрезал бы: пишет о чужих, сам не продаёт',
  quote: 'Новости велоспорта',
  unanswered: false,
  decided_by_user: null,
};

function detailOf(run: SegmentRunRow, changes: Partial<SegmentRunDetail> = {}): SegmentRunDetail {
  return {
    run,
    hypothesis: 'сайты EN',
    keywords: ['купить горный велосипед'],
    estimate_lines: [],
    summary: { hosts: 7, accepted: 3, review: 1, rejected: 3, pending: 0, no_address: 1, leads: 2 },
    lanes: { pending: 0, accept: 3, review: 1, reject: 3 },
    rejected: [],
    no_address: [],
    waiting_address: 0,
    leads: { new: 0, ready: 1, rejected: 1 },
    lead_reasons: {},
    unanswered: 0,
    report: {},
    review: [],
    ...changes,
  };
}

function homeOf(...runs: SegmentRunRow[]): SegmentHome {
  return {
    hypothesis_id: 5,
    segment: 'Интернет-магазины велосипедов.',
    mode: 'shadow',
    countries: ['us'],
    languages: ['ru', 'en'],
    max_keywords: 100,
    max_depth: 10,
    runs,
  };
}

function job(id: string, state: string) {
  return {
    body: {
      job_id: id,
      kind: 'сбор базы продаж',
      state,
      title: state,
      error: null,
      report: state === 'done' ? { progress: { stage: 'leads', done: 2 } } : null,
      retries_left: 3,
      next_try_at: null,
      ended_at: null,
    },
  };
}

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openTab(routes: Routes, address = '/sales?tab=collect'): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/jobs/sales-segment-12': job('sales-segment-12', 'running'),
    ...routes,
  });
  renderWith(<AppRoutes />, address);
  await screen.findByText('Сегмент гипотезы', {}, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('сбор базы: ход прогона — слова', () => {
  it('этап с ходом «X из Y», сколько идёт и когда отмечался', () => {
    expect(stageLine(JUDGING)).toBe('судья: 12 из 60 сайтов');
    expect(pulse(JUDGING)).toEqual({ words: 'идёт 4 мин · обновлено 5 с назад', quiet: false });
    const serp: SegmentRunRow = {
      ...JUDGING,
      stage: 'serp',
      progress: { ...JUDGING.progress!, stage: 'serp', done: 2, total: 11, mode: 'standard' },
    };
    expect(stageLine(serp)).toBe('покупаем выдачу: готово 2 из 11 ключей');
    const contacts: SegmentRunRow = {
      ...JUDGING,
      stage: 'contacts',
      progress: { ...JUDGING.progress!, stage: 'contacts', done: 1, total: 21 },
    };
    expect(stageLine(contacts)).toBe('адреса: 1 из 21 домена');
  });

  it('давно не отмечался — подсвечено; в очереди — когда поставлен', () => {
    expect(pulse({ ...JUDGING, alive_at: before(300) })).toEqual({
      words: 'идёт 4 мин · обновлено 5 мин назад — давно не отмечался',
      quiet: true,
    });
    expect(pulse({ ...JUDGING, stage: 'queued', progress: null })?.words).toBe(
      'поставлен 5 с назад',
    );
    expect(ago(30)).toBe('30 с назад');
    expect(ago(600)).toBe('10 мин назад');
    expect(lasting(3_900)).toBe('1 ч 05 мин');
  });

  it('задача ещё не у воркера — «в очереди задач» или «ждёт повтора», а не этап прошлого прохода', () => {
    const queued: SegmentRunRow = { ...JUDGING, watch: { ...JUDGING.watch!, job: 'queued' } };
    expect(stageLine(queued)).toBe('в очереди задач');
    expect(stageBadge(queued)).toEqual({ title: 'в очереди задач', color: 'blue' });
    expect(pulse(queued)?.words).toBe('поставлен 5 с назад');
    const retry: SegmentRunRow = { ...JUDGING, watch: { ...JUDGING.watch!, job: 'scheduled' } };
    expect(stageLine(retry)).toBe('ждёт повтора задачи');
    expect(stageBadge(retry)).toEqual({ title: 'ждёт повтора', color: 'blue' });
    // Когда следующая попытка — говорит строка задачи, пульса нет.
    expect(pulse(retry)).toBeNull();
  });

  it('продолжение в очереди — «в очереди задач», запись хода прошлого прохода не видна', () => {
    const resumed: SegmentRunRow = { ...DONE, active: true, progress: JUDGING.progress ?? null };
    expect(stageLine(resumed)).toBe('в очереди задач — продолжение');
    expect(stageBadge(resumed)).toEqual({ title: 'в очереди задач', color: 'blue' });
    expect(stageBadge(DONE)).toEqual({ title: 'готово', color: 'green' });
  });

  it('задачи нет — «остановлен», где встал — словами', () => {
    const stalled: SegmentRunRow = {
      ...JUDGING,
      active: false,
      watch: { going: false, stalled: 'задачи прогона в очереди нет', workers: 1, silent: false },
    };
    expect(stageBadge(stalled)).toEqual({ title: 'остановлен', color: 'yellow' });
    expect(runState(stalled)).toBe('остановлен: задачи прогона нет');
    expect(stoppedAt(stalled)).toBe('Остановлен на этапе «судья»: 12 из 60 сайтов.');
  });

  it('шапка — язык писем, глубина выдачи, кто и когда запустил, когда кончился', () => {
    expect(runFacts(DONE)).toMatch(
      /^Язык писем — английский · выдача — 10 результатов на ключ · запустил seller 14\.10\.2026, \d\d:30 · кончился 14\.10\.2026, \d\d:41\.$/,
    );
  });

  it('опрос: список и отчёт — пока прогон идёт, и стоят, когда кончился', () => {
    expect(listPoll(homeOf(DONE, JUDGING))).toBe(5_000);
    expect(listPoll(homeOf(DONE))).toBe(false);
    expect(runPoll(detailOf(JUDGING))).toBe(3_000);
    expect(runPoll(detailOf(DONE))).toBe(false);
    // Сервер старше хода: «идёт» — по стадии.
    const old = { ...JUDGING } as Partial<SegmentRunRow>;
    delete old.active;
    expect(runPoll(detailOf(old as SegmentRunRow))).toBe(3_000);
  });

  it('?run=N — в адресе вкладки сбора; другая вкладка его не держит', () => {
    const filters = readLeadFilters(new URLSearchParams('tab=collect&hypothesis=5&run=11'));
    expect(filters.run).toBe(11);
    expect(writeLeadFilters(filters).toString()).toBe('tab=collect&hypothesis=5&run=11');
    expect(writeLeadFilters({ ...filters, tab: 'funnel' }).toString()).toBe(
      'tab=funnel&hypothesis=5',
    );
    expect(readLeadFilters(new URLSearchParams('tab=collect&run=12abc')).run).toBeNull();
    expect(NO_LEAD_FILTERS.run).toBeNull();
  });
});

describe('сбор базы: ход прогона — экран', () => {
  it('идущий прогон: стадия, ход и пульс; «Найти адреса принятым» посреди прогона нет', async () => {
    await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(JUDGING) },
      'GET /api/sales/segment/runs/12': {
        body: detailOf(JUDGING, {
          waiting_address: 4,
          summary: { ...detailOf(JUDGING).summary, pending: 48 },
        }),
      },
    });

    const going = await screen.findByRole('status', { name: 'Ход прогона' }, SCREEN_WAIT);
    expect(going).toHaveTextContent('судья: 12 из 60 сайтов · идёт 4 мин · обновлено 5 с назад');
    expect(screen.getByText('судит судья')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Найти адреса принятым/ })).toBeNull();
    // Плитки сходятся и во время судьи: «ещё не судили» — своим числом.
    expect(screen.getByText('Ещё не судили')).toBeInTheDocument();
    expect(screen.getByText('48')).toBeInTheDocument();
  });

  it('задачи нет: «остановлен» словами сервера и «Продолжить прогон»', async () => {
    const words = 'задачи прогона в очереди нет: её воркер умер — продолжите прогон';
    const stalled = {
      ...JUDGING,
      active: false,
      watch: { ...JUDGING.watch!, going: false, stalled: words },
    };
    const recorded = await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(stalled) },
      'GET /api/sales/segment/runs/12': { body: detailOf(stalled) },
      'POST /api/sales/segment/runs/12/resume': {
        body: { run_id: 12, job_id: 'sales-segment-12' },
      },
    });
    const user = userEvent.setup();

    expect(await screen.findByText(words, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByText('Остановлен на этапе «судья»: 12 из 60 сайтов.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Продолжить прогон' }));

    await waitFor(
      () => expect(calls(recorded, 'POST', '/api/sales/segment/runs/12/resume')).toHaveLength(1),
      SCREEN_WAIT,
    );
  });

  it('воркеров нет — «Задачу некому взять» словами, без команд консоли', async () => {
    const queued: SegmentRunRow = {
      ...JUDGING,
      stage: 'queued',
      progress: null,
      watch: { going: true, stalled: null, workers: 0, silent: false },
    };
    await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(queued) },
      'GET /api/sales/segment/runs/12': { body: detailOf(queued) },
    });

    expect(await screen.findByText('Задачу некому взять', {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByText(/служба долгих задач продаж не запущена/)).toBeInTheDocument();
    expect(screen.queryByText(/docker|python -m/)).toBeNull();
  });

  it('после «Найти адреса принятым» экран сам переходит в «идёт» — без перезагрузки', async () => {
    let reads = 0;
    const resumed: SegmentRunRow = { ...DONE, finished_at: null, active: true };
    await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(DONE) },
      'GET /api/sales/segment/runs/12': () => {
        reads += 1;
        return { body: detailOf(reads === 1 ? DONE : resumed, { waiting_address: 4 }) };
      },
      'POST /api/sales/segment/runs/12/resume': {
        body: { run_id: 12, job_id: 'sales-segment-12' },
      },
    });
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: 'Найти адреса принятым · 4' }, SCREEN_WAIT),
    );

    const going = await screen.findByRole('status', { name: 'Ход прогона' }, SCREEN_WAIT);
    expect(going).toHaveTextContent('в очереди задач — продолжение');
    expect(screen.queryByRole('button', { name: /Найти адреса принятым/ })).toBeNull();
  });

  it('прогон кончился — перечитаны гипотезы и лиды («Лиды — N»)', { timeout: 15000 }, async () => {
    let reads = 0;
    const recorded = await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(JUDGING) },
      'GET /api/sales/segment/runs/12': () => {
        reads += 1;
        return { body: detailOf(reads === 1 ? JUDGING : DONE) };
      },
    });

    // Опрос идущего прогона (3 с) приносит «готово» — и гипотезы перечитаны.
    await waitFor(
      () => expect(calls(recorded, 'GET', '/api/sales/hypotheses').length).toBeGreaterThan(1),
      { timeout: 9000 },
    );
    expect(screen.getByText('готово')).toBeInTheDocument();
  });

  it(
    'двойной клик по решению — одно решение; строка стоит на месте с пометкой',
    { timeout: 10000 },
    async () => {
      const next: SegmentCandidate = { ...SITE, id: 42, host: 'velo-maybe-sample.example.test' };
      let decided = false;
      const recorded = await openTab({
        'GET /api/sales/segment?hypothesis=5': { body: homeOf(DONE) },
        'GET /api/sales/segment/runs/12': () => ({
          body: detailOf(DONE, {
            review: decided ? [next] : [SITE, next],
            summary: { ...detailOf(DONE).summary, review: decided ? 1 : 2 },
          }),
        }),
        'POST /api/sales/segment/candidates/41/decision': () => {
          decided = true;
          return { body: { ...SITE, lane: 'accept', decided_by_user: ADMIN.email } };
        },
      });
      const user = userEvent.setup();
      const review = await screen.findByRole('table', { name: 'Сайты на проверку' }, SCREEN_WAIT);
      const [first] = within(review).getAllByRole('button', { name: 'Принять' });

      await user.dblClick(first!);

      await waitFor(
        () => expect(within(review).getByText('принят')).toBeInTheDocument(),
        SCREEN_WAIT,
      );
      expect(calls(recorded, 'POST', '/api/sales/segment/candidates/41/decision')).toHaveLength(1);
      const rows = within(review).getAllByRole('row');
      // Решённая строка — на своём месте, соседняя не поднялась под курсор.
      expect(rows[1]).toHaveTextContent(SITE.host);
      expect(rows[2]).toHaveTextContent(next.host);
      expect(within(rows[2]!).getByRole('button', { name: 'Принять' })).toBeEnabled();
    },
  );

  it('«На проверку» обрезано сервером — сказано, сколько показано из скольких', async () => {
    await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(DONE) },
      'GET /api/sales/segment/runs/12': {
        body: detailOf(DONE, {
          review: [SITE],
          summary: { ...detailOf(DONE).summary, review: 234 },
          report: { serp: { known: 9 } },
        }),
      },
    });

    expect(
      await screen.findByText(/Показаны первые 1 из 234/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText(/Уже в сборе гипотезы: 9/)).toBeInTheDocument();
  });

  it('длинная причина «на проверку» переносится в своей колонке, кнопки решения — в своей', async () => {
    // Находка стенда 14.10: причина в ячейке без переноса уезжала под «Принять» и «Отказ».
    const long: SegmentCandidate = {
      ...SITE,
      host: 'regionalbicyclecatalogue.directory-sample.example.test',
      keyword: 'купить горный велосипед с доставкой по всей стране недорого',
      reason:
        'судья в наблюдении отрезал бы: справочник — списки чужих магазинов велосипедов с ' +
        'отзывами, рейтингами и картой проезда, сам ничего не продаёт и не доставляет',
      quote:
        'Alle Fahrradhändler Ihrer Region auf einen Blick: Bewertungen, Öffnungszeiten, ' +
        'Anfahrt und Angebote der Händler aus Ihrer Stadt und Umgebung im Vergleich',
    };
    await openTab({
      'GET /api/sales/segment?hypothesis=5': { body: homeOf(DONE) },
      'GET /api/sales/segment/runs/12': { body: detailOf(DONE, { review: [long] }) },
    });

    const review = await screen.findByRole('table', { name: 'Сайты на проверку' }, SCREEN_WAIT);
    const row = within(review).getAllByRole('row')[1]!;
    const why = within(row).getByText(long.reason!).closest('td');
    expect(why).toHaveClass('wrapCell');
    for (const name of ['Принять', 'Отказ']) {
      const cell = within(row).getByRole('button', { name }).closest('td');
      expect(cell).not.toBe(why);
      expect(cell).not.toHaveClass('wrapCell');
    }
    // Цитата — двумя строками, целиком — в подсказке.
    const quote = within(row).getByText(`«${long.quote}»`);
    expect(quote).toHaveAttribute('data-line-clamp');
    expect(quote).toHaveAttribute('title', `«${long.quote}»`);
    // Домен — по швам в своей колонке, ключ — одной строкой и целиком в подсказке.
    const site = within(row).getByRole('link', { name: long.host });
    expect(site.closest('td')).toHaveClass('cellName');
    expect(site.querySelectorAll('wbr').length).toBeGreaterThan(0);
    const keyword = `по ключу «${long.keyword}»`;
    expect(within(row).getByText(keyword)).toHaveAttribute('title', keyword);
  });

  it('?run=N открывает тот прогон, а выбор в списке пишет его в адрес', async () => {
    const older: SegmentRunRow = { ...DONE, id: 11, job_id: 'sales-segment-11' };
    const recorded = await openTab(
      {
        'GET /api/sales/segment?hypothesis=5': { body: homeOf(DONE, older) },
        'GET /api/sales/segment/runs/11': { body: detailOf(older) },
        'GET /api/sales/segment/runs/12': { body: detailOf(DONE) },
      },
      '/sales?tab=collect&hypothesis=5&run=11',
    );
    const user = userEvent.setup();

    expect(await screen.findByText('Прогон №11', {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(calls(recorded, 'GET', '/api/sales/segment/runs/12')).toHaveLength(0);
    await user.click(screen.getByRole('textbox', { name: 'Прогон' }));
    const options = await screen.findAllByRole('option', {}, SCREEN_WAIT);
    // Стадия — в списке прогонов, под каждым.
    expect(options[0]).toHaveTextContent('готово');
    await user.click(options[0]!);

    expect(await screen.findByText('Прогон №12', {}, SCREEN_WAIT)).toBeInTheDocument();
  });
});
