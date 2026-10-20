/**
 * Отчёт прогона компактно (15.10.2026): «Сайты прогона» и «Расход и смета» — окнами, «На проверку
 * N — решить» вместо таблицы, причины отсева и «без адреса» — свёрнутой строкой. Ответы сервера —
 * записанные (`serve()`), незаписанный запрос роняет тест; домены и тексты выдуманы, ниши и языки —
 * разные.
 *
 * Проверяется то, ради чего правка: плитки полос открывают окно на своей вкладке, строка «решить» —
 * на вкладке проверки; решения в окне уходят на сервер и стоят на месте, отказ — словами; старой
 * таблицы в отчёте нет; «почему» свёрнуто и раскрывается; Esc закрывает окно и фокус возвращается;
 * вид окна — в адресе, «назад» закрывает окно, `?run=N` держится; окно идущего прогона
 * обновляется само; окно сметы пересуда поверх окна сайтов закрывается своим Esc.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation, useNavigate } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import type { SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import type { RejudgeView, SegmentSite, SegmentSitesPage, SiteTab } from './SegmentSitesApi';

const WAIT = { timeout: 5000 };
/**
 * Срок тестов с окном сайтов и шагами по истории: локально такой тест идёт до 1,5 с, в CI файл —
 * втрое дольше, а одно ожидание `WAIT` само может занять весь общий срок теста (5 с).
 */
const LONG = { timeout: 15_000 };
const RUN_ID = 31;
const SITES = `/api/sales/segment/runs/${RUN_ID}/sites`;
const REPORT = `/api/sales/segment/runs/${RUN_ID}`;
const WINDOW = `Сайты прогона №${RUN_ID}`;

const run: SegmentRunRow = {
  id: RUN_ID,
  hypothesis_id: 9,
  created_at: '2026-10-15T08:00:00+00:00',
  finished_at: '2026-10-15T08:12:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'pl',
  language: 'en',
  depth_pages: 2,
  keywords: 4,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: null,
  alive_at: '2026-10-15T08:12:00+00:00',
  active: false,
  progress: null,
  watch: null,
};

const home: SegmentHome = {
  hypothesis_id: 9,
  segment: 'Ветеринарные клиники: сами лечат животных, запись и цены на сайте.',
  mode: 'shadow',
  countries: ['pl', 'pt'],
  languages: ['en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [run],
};

const lanes = { pending: 2, accept: 4, review: 3, reject: 5 };

function detailOf(changes: Partial<SegmentRunDetail> = {}): SegmentRunDetail {
  return {
    run,
    hypothesis: 'клиники PL',
    keywords: ['klinika weterynaryjna'],
    estimate_lines: ['Выдача: ключей 4 × страниц 2 — результатов до 80.'],
    summary: {
      hosts: 14,
      accepted: 4,
      review: 3,
      rejected: 5,
      pending: 2,
      no_address: 1,
      leads: 2,
    },
    lanes,
    rejected: [{ words: 'каталог клиник: списки чужих врачей', count: 5 }],
    no_address: [{ words: 'адреса на сайте нет', count: 1 }],
    waiting_address: 0,
    leads: { new: 0, ready: 2, rejected: 0 },
    lead_reasons: {},
    unanswered: 0,
    report: {},
    ...changes,
  };
}

function siteOf(id: number, host: string, extra: Partial<SegmentSite> = {}): SegmentSite {
  return {
    id,
    host,
    page_url: `https://${host}/`,
    page_title: host,
    keyword: 'klinika weterynaryjna',
    lane: 'review',
    verdict: 'reject',
    decided_by: 'model',
    decided_by_user: null,
    decided_at: '2026-10-15T08:05:00+00:00',
    reason: 'судья в наблюдении отрезал бы: каталог клиник — списки чужих врачей и отзывы',
    quote: 'Znajdź najlepszego weterynarza w Twojej okolicy',
    unanswered: false,
    model: 'gpt-5-mini',
    undoable: false,
    judge_reason: null,
    outcome: null,
    change: null,
    ...extra,
  };
}

/** Ответ сервера на «Принять»: строка списка — решил человек, слова судьи рядом. */
function acceptedBy(site: SegmentSite, email: string): SegmentSite {
  return {
    ...site,
    lane: 'accept',
    decided_by: 'human',
    decided_by_user: email,
    decided_at: '2026-10-15T09:00:00+00:00',
    reason: 'принял человек',
    undoable: true,
    judge_reason: site.reason,
  };
}

const counts = { all: 14, accept: 4, review: 3, reject: 5, pending: 2, changed: 0 };

function pageOf(tab: SiteTab, rows: SegmentSite[], total = rows.length): SegmentSitesPage {
  return {
    run_id: RUN_ID,
    lane: tab,
    decider: null,
    page: 1,
    limit: 50,
    total,
    lanes: counts,
    deciders: { rule: 0, model: rows.length, human: 0, none: 0 },
    rows,
  };
}

const hypotheses: HypothesesView = {
  rows: [
    {
      id: 9,
      name: 'клиники PL',
      description: home.segment,
      created_at: '2026-10-14T10:00:00+00:00',
      leads: { new: 0, ready: 2, rejected: 0 },
      total: 2,
    },
  ],
  total: 1,
  module_enabled: true,
};

const kb = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

/** Где сейчас экран: адрес целиком. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

/** «Назад» браузера — шагом истории роутера. */
function Back() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => void navigate(-1)}>
      назад браузера
    </button>
  );
}

function where(): string {
  return screen.getByTestId('where').textContent ?? '';
}

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

/** Записанные ответы вкладок окна: у каждой полосы — своя строка. */
const TABS: Routes = {
  [`GET ${SITES}?lane=all&page=1`]: {
    body: pageOf('all', [siteOf(1, 'klinika-sample.example.test', { lane: 'accept' })], 14),
  },
  [`GET ${SITES}?lane=accept&page=1`]: {
    body: pageOf('accept', [siteOf(1, 'klinika-sample.example.test', { lane: 'accept' })], 4),
  },
  [`GET ${SITES}?lane=review&page=1`]: {
    body: pageOf('review', [siteOf(2, 'katalog-weterynarzy.example.test')], 3),
  },
  [`GET ${SITES}?lane=reject&page=1`]: {
    body: pageOf('reject', [siteOf(3, 'portal-pets.example.test', { lane: 'reject' })], 5),
  },
  [`GET ${SITES}?lane=pending&page=1`]: {
    body: pageOf('pending', [siteOf(4, 'clinica-vet-sample.example.test', { lane: 'pending' })], 2),
  },
};

async function open(
  routes: Routes = {},
  path = `/sales?tab=collect&hypothesis=9`,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/kb': { body: kb },
    'GET /api/sales/segment?hypothesis=9': { body: home },
    [`GET ${REPORT}`]: { body: detailOf() },
    ...TABS,
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
      <Back />
    </>,
    path,
  );
  await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
  return recorded;
}

function gets(recorded: Recorded, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === 'GET' && call.path === path);
}

/** Строки тела таблицы окна: над ними — подписи колонок и строка фильтров. */
function bodyRows(dialog: HTMLElement): HTMLElement[] {
  return within(dialog)
    .getAllByRole('row')
    .filter((row) => row.closest('tbody'));
}

describe('отчёт прогона компактно: окна', () => {
  it.each([
    ['Сайтов из выдачи', 'all', 'Все · 14'],
    ['Принято', 'accept', 'Приняты · 4'],
    ['На проверку', 'review', 'На проверку · 3'],
    ['Отсечено', 'reject', 'Отсечены · 5'],
    ['Ещё не судили', 'pending', 'Ещё не судили · 2'],
  ] as const)('плитка «%s» открывает окно сайтов на своей вкладке', async (tile, tab, radio) => {
    const recorded = await open();
    const user = userEvent.setup();

    // Плитка — ссылка целиком: подпись и число.
    await user.click(screen.getByRole('link', { name: new RegExp(`^${tile}\\s*\\d+$`) }));

    const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    // Вкладки — с числами сервера: встают с первым ответом списка.
    expect(await within(dialog).findByRole('radio', { name: radio }, WAIT)).toBeChecked();
    await waitFor(
      () => expect(gets(recorded, `${SITES}?lane=${tab}&page=1`)).toHaveLength(1),
      WAIT,
    );
    // Вкладка и прогон — в адресе: ссылка и «назад» откроют то же окно; «Все» — умолчание, не пишется.
    const shown = tab === 'all' ? '' : `&sites=${tab}`;
    expect(where()).toBe(
      `/sales?tab=collect&hypothesis=9&run=${RUN_ID}&sites_run=${RUN_ID}${shown}`,
    );
  });

  it('старой таблицы «На проверку» нет: строка «На проверку N — решить» открывает вкладку решений', async () => {
    const recorded = await open();
    const user = userEvent.setup();

    // В отчёте — ни таблиц, ни запроса списка сайтов.
    expect(screen.queryByRole('table')).toBeNull();
    expect(recorded.calls.filter((call) => call.path.startsWith(SITES))).toHaveLength(0);
    await user.click(screen.getByRole('link', { name: 'На проверку 3 — решить' }));

    const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    expect(
      await within(dialog).findByRole('radio', { name: 'На проверку · 3' }, WAIT),
    ).toBeChecked();
    expect(
      await within(dialog).findByText('katalog-weterynarzy.example.test', { exact: false }, WAIT),
    );
  });

  it('проверять нечего — строки «решить» нет', async () => {
    await open({
      [`GET ${REPORT}`]: {
        body: detailOf({
          summary: { ...detailOf().summary, review: 0 },
          lanes: { ...lanes, review: 0 },
        }),
      },
    });

    expect(screen.queryByRole('link', { name: /— решить$/ })).toBeNull();
  });

  it('решения в окне: тот же ответ — принят, противоположное — отказ словами; строки на месте', async () => {
    const first = siteOf(2, 'katalog-weterynarzy.example.test');
    const second = siteOf(5, 'clinica-veterinaria-sample.example.test', {
      reason: 'по тексту не понять: клиника или блог ветеринара',
      quote: 'Marque a sua consulta online',
    });
    const refusal = 'сайт не ждёт решения: он уже «принят» — обновите отчёт прогона';
    const recorded = await open(
      {
        [`GET ${SITES}?lane=review&page=1`]: { body: pageOf('review', [first, second], 3) },
        // Повтор того же решения: сайт уже принят человеком — сервер отвечает 200.
        'POST /api/sales/segment/candidates/2/decision': {
          body: acceptedBy(first, 'colleague@ours.example.test'),
        },
        // Противоположное по решённому — 409 словами.
        'POST /api/sales/segment/candidates/5/decision': { status: 409, body: { detail: refusal } },
      },
      `/sales?tab=collect&hypothesis=9&run=${RUN_ID}&sites_run=${RUN_ID}&sites=review`,
    );
    const user = userEvent.setup();
    const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    await within(dialog).findByRole('table', {}, WAIT);

    await user.click(within(bodyRows(dialog)[0]!).getByRole('button', { name: 'Принять' }));
    await waitFor(
      () => expect(within(dialog).getByText('решено: принят')).toBeInTheDocument(),
      WAIT,
    );
    await user.click(within(bodyRows(dialog)[1]!).getByRole('button', { name: 'Отказ' }));

    expect(await within(dialog).findByText(refusal, {}, WAIT)).toBeInTheDocument();
    expect(within(dialog).getByText('Решение не записано')).toBeInTheDocument();
    const posted = recorded.calls.filter((call) => call.method === 'POST');
    expect(posted.map((call) => [call.path, call.body])).toEqual([
      ['/api/sales/segment/candidates/2/decision', { accept: true }],
      ['/api/sales/segment/candidates/5/decision', { accept: false }],
    ]);
    // Строки на местах: решённая — с пометкой, отказанная — снова с кнопками.
    const [top, bottom] = bodyRows(dialog);
    expect(top).toHaveTextContent(first.host);
    expect(bottom).toHaveTextContent(second.host);
    expect(within(bottom!).getByRole('button', { name: 'Отказ' })).toBeEnabled();
    // Числа вкладок — с принятым решением, отказанное не в счёт.
    expect(within(dialog).getByRole('radio', { name: 'На проверку · 2' })).toBeChecked();
    expect(within(dialog).getByRole('radio', { name: 'Приняты · 5' })).toBeInTheDocument();
  });

  it('пока сервер пишет решение — «принят — записываем…», второе нажатие не уходит', async () => {
    const site = siteOf(2, 'katalog-weterynarzy.example.test');
    let release: () => void = () => undefined;
    const answered = new Promise<void>((resolve) => {
      release = resolve;
    });
    const recorded = await open(
      {
        [`GET ${SITES}?lane=review&page=1`]: { body: pageOf('review', [site], 3) },
        'POST /api/sales/segment/candidates/2/decision': { body: acceptedBy(site, ADMIN.email) },
      },
      `/sales?tab=collect&hypothesis=9&run=${RUN_ID}&sites_run=${RUN_ID}&sites=review`,
    );
    // Ответ на решение задержан: сервер ещё пишет.
    const spy = vi.mocked(globalThis.fetch);
    const served = spy.getMockImplementation()!;
    spy.mockImplementation(async (input, init) => {
      if (init?.method === 'POST') await answered;
      return served(input, init);
    });
    const user = userEvent.setup();
    const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    await within(dialog).findByRole('table', {}, WAIT);

    await user.dblClick(within(dialog).getByRole('button', { name: 'Принять' }));

    expect(await within(dialog).findByText('принят — записываем…', {}, WAIT)).toBeInTheDocument();
    release();
    expect(await within(dialog).findByText('решено: принят', {}, WAIT)).toBeInTheDocument();
    expect(recorded.calls.filter((call) => call.method === 'POST')).toHaveLength(1);
  });

  it('«почему» — одной свёрнутой строкой, раскрывается по нажатию', async () => {
    await open();
    const user = userEvent.setup();
    const toggle = screen.getByRole('button', { name: 'Почему отсечены и без адреса' });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('list', { name: 'Отсечены — почему' })).toBeNull();
    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const cut = await screen.findByRole('list', { name: 'Отсечены — почему' }, WAIT);
    expect(within(cut).getByRole('listitem')).toHaveTextContent(
      'каталог клиник: списки чужих врачей — 5',
    );
    expect(screen.getByRole('list', { name: 'Без адреса — почему' })).toBeInTheDocument();
  });

  it('«почему» называет только то, что в нём есть', async () => {
    await open({ [`GET ${REPORT}`]: { body: detailOf({ no_address: [] }) } });

    expect(screen.getByRole('button', { name: 'Почему отсечены' })).toBeInTheDocument();
  });

  it('Esc закрывает окно сайтов, фокус возвращается на ссылку, адрес — прежний', async () => {
    await open();
    const user = userEvent.setup();
    const before = where();
    const tile = screen.getByRole('link', { name: /^Отсечено\s*5$/ });

    await user.click(tile);
    await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), WAIT);
    await waitFor(() => expect(tile).toHaveFocus(), WAIT);
    // Окно открыла ссылка с отчёта — «Закрыть» вернуло на шаг назад, а не добавило запись.
    expect(where()).toBe(before);
  });

  it('«Закрыть» у окна расхода — фокус на кнопку «Расход и смета»', async () => {
    await open();
    const user = userEvent.setup();
    const button = screen.getByRole('button', { name: 'Расход и смета' });

    await user.click(button);
    const dialog = await screen.findByRole('dialog', { name: `Расход и смета прогона №${RUN_ID}` });
    expect(within(dialog).getByText('Смета на запуске')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Закрыть' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), WAIT);
    await waitFor(() => expect(button).toHaveFocus(), WAIT);
  });

  it(
    'вид окна — в адресе: ссылка открывает то же окно, «назад» его закрывает, ?run=N держится',
    LONG,
    async () => {
      const recorded = await open(
        {},
        `/sales?tab=collect&hypothesis=9&run=${RUN_ID}&sites_run=${RUN_ID}&sites=reject`,
      );
      const user = userEvent.setup();

      // Присланная ссылка — то же окно на той же вкладке.
      const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
      expect(
        await within(dialog).findByRole('radio', { name: 'Отсечены · 5' }, WAIT),
      ).toBeChecked();
      expect(gets(recorded, `${SITES}?lane=reject&page=1`)).toHaveLength(1);
      // Вкладка — заменой записи: адрес новый, «назад» по вкладкам не ходит.
      await user.click(within(dialog).getByRole('radio', { name: 'Приняты · 4' }));
      await waitFor(() => expect(where()).toContain('sites=accept'), WAIT);
      // Окно по ссылке снаружи закрывается заменой записи: прогон в адресе остаётся.
      await user.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), WAIT);
      expect(where()).toBe(`/sales?tab=collect&hypothesis=9&run=${RUN_ID}`);

      // Открыли ссылкой отчёта — «назад» браузера закрывает окно, вперёд — открывает снова.
      await user.click(screen.getByRole('link', { name: 'Сайты прогона · 14' }));
      await screen.findByRole('dialog', { name: WINDOW }, WAIT);
      await user.click(screen.getByRole('button', { name: 'назад браузера', hidden: true }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), WAIT);
      expect(where()).toBe(`/sales?tab=collect&hypothesis=9&run=${RUN_ID}`);
      expect(screen.getByText(`Прогон №${RUN_ID}`)).toBeInTheDocument();
    },
  );

  it(
    'вкладка и страница — заменой записи: «назад» закрывает окно, а не листает его',
    LONG,
    async () => {
      const many = Array.from({ length: 50 }, (_, n) => siteOf(100 + n, `vet-${n}.example.test`));
      const rest = [siteOf(200, 'vet-tail.example.test')];
      await open({
        [`GET ${SITES}?lane=review&page=1`]: { body: pageOf('review', many, 51) },
        [`GET ${SITES}?lane=review&page=2`]: { body: { ...pageOf('review', rest, 51), page: 2 } },
      });
      const user = userEvent.setup();
      const before = where();

      await user.click(screen.getByRole('link', { name: /^Принято\s*4$/ }));
      const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
      await user.click(await within(dialog).findByRole('radio', { name: 'На проверку · 3' }, WAIT));
      expect(await within(dialog).findByText('Показаны 1–50 из 51.', {}, WAIT)).toBeInTheDocument();
      await user.click(within(dialog).getByRole('button', { name: 'Страница 2' }));
      expect(
        await within(dialog).findByText('Показаны 51–51 из 51.', {}, WAIT),
      ).toBeInTheDocument();
      expect(where()).toContain('sites=review&sites_page=2');

      // Окно — одна запись истории: шаг назад — отчёт без окна, а не прежняя вкладка или страница.
      await user.click(screen.getByRole('button', { name: 'назад браузера', hidden: true }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), WAIT);
      expect(where()).toBe(before);
    },
  );

  it(
    '«Закрыть» после смены вкладки и страницы — к отчёту без окна, шагом назад',
    LONG,
    async () => {
      const many = Array.from({ length: 50 }, (_, n) => siteOf(100 + n, `vet-${n}.example.test`));
      const rest = [siteOf(200, 'vet-tail.example.test')];
      await open({
        [`GET ${SITES}?lane=review&page=1`]: { body: pageOf('review', many, 51) },
        [`GET ${SITES}?lane=review&page=2`]: { body: { ...pageOf('review', rest, 51), page: 2 } },
      });
      const user = userEvent.setup();
      const before = where();

      await user.click(screen.getByRole('link', { name: /^Отсечено\s*5$/ }));
      const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
      await user.click(await within(dialog).findByRole('radio', { name: 'На проверку · 3' }, WAIT));
      await user.click(await within(dialog).findByRole('button', { name: 'Страница 2' }, WAIT));
      expect(
        await within(dialog).findByText('Показаны 51–51 из 51.', {}, WAIT),
      ).toBeInTheDocument();
      await user.click(within(dialog).getByRole('button', { name: 'Закрыть' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), WAIT);
      expect(where()).toBe(before);
      // Ещё шаг назад — уже не окно: записей окна в истории не осталось.
      await user.click(screen.getByRole('button', { name: 'назад браузера', hidden: true }));
      await new Promise((resolve) => setTimeout(resolve, 300));
      expect(screen.queryByRole('dialog')).toBeNull();
    },
  );

  it(
    'окно идущего прогона обновляется само; решённая строка стоит на месте и после опроса',
    { timeout: 20_000 },
    async () => {
      const going: SegmentRunRow = {
        ...run,
        stage: 'judge',
        finished_at: null,
        active: true,
        watch: { going: true, stalled: null, workers: 1, silent: false },
      };
      const first = siteOf(2, 'katalog-weterynarzy.example.test');
      const later = siteOf(9, 'nowa-klinika-sample.example.test', {
        reason: 'по тексту не понять: сеть клиник или каталог',
      });
      let decided = false;
      let reads = 0;
      const recorded = await open(
        {
          'GET /api/sales/segment?hypothesis=9': { body: { ...home, runs: [going] } },
          [`GET ${REPORT}`]: { body: detailOf({ run: going }) },
          // Судья идёт: следующий опрос приносит новый сайт «на проверку», а решённого — уже нет;
          // числа вкладок сервер считает сам, с решением.
          [`GET ${SITES}?lane=review&page=1`]: () => {
            reads += 1;
            const rows = reads === 1 ? [first] : decided ? [later] : [first, later];
            const page = pageOf('review', rows, rows.length);
            const accept = counts.accept + (decided ? 1 : 0);
            return { body: { ...page, lanes: { ...counts, review: rows.length, accept } } };
          },
          'POST /api/sales/segment/candidates/2/decision': () => {
            decided = true;
            return { body: acceptedBy(first, ADMIN.email) };
          },
        },
        `/sales?tab=collect&hypothesis=9&run=${RUN_ID}&sites_run=${RUN_ID}&sites=review`,
      );
      const user = userEvent.setup();
      const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
      await within(dialog).findByRole('table', {}, WAIT);

      await user.click(within(dialog).getByRole('button', { name: 'Принять' }));
      await within(dialog).findByText('решено: принят', {}, WAIT);

      // Опрос (5 с) принёс новый сайт — решённая строка на своём месте, над ним.
      expect(
        await within(dialog).findByText(later.host, { exact: false }, { timeout: 12_000 }),
      ).toBeInTheDocument();
      expect(gets(recorded, `${SITES}?lane=review&page=1`).length).toBeGreaterThan(1);
      const [top, bottom] = bodyRows(dialog);
      expect(top).toHaveTextContent(first.host);
      expect(top).toHaveTextContent('решено: принят');
      expect(bottom).toHaveTextContent(later.host);
      // Сервер уже знает о решении — во вкладке не вычитается второй раз.
      expect(within(dialog).getByRole('radio', { name: 'На проверку · 1' })).toBeChecked();
    },
  );

  it('окно сметы пересуда поверх окна сайтов: Esc закрывает только его', LONG, async () => {
    const estimate: RejudgeView = {
      run_id: RUN_ID,
      estimate: {
        sites: 12,
        model: 10,
        per_site: 1100,
        tokens: 11000,
        tokens_left: null,
        kept_human: 0,
        with_lead: 2,
        pending: 2,
        digest: 'digest-of-segment',
        mode: 'shadow',
        resumes: false,
        done: 0,
        lines: ['Пересудить сайтов: 12 — по тексту, который видел прошлый суд.'],
        refusal: null,
      },
      last: null,
      going: false,
    };
    await open({ [`GET ${REPORT}/rejudge`]: { body: estimate } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('link', { name: 'Сайты прогона · 14' }));
    await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    await user.click(screen.getByRole('button', { name: 'Пересудить прогон' }));
    const over = await screen.findByRole('dialog', { name: `Пересудить прогон №${RUN_ID}` }, WAIT);
    expect(
      await within(over).findByText(estimate.estimate.lines[0]!, {}, WAIT),
    ).toBeInTheDocument();
    await user.keyboard('{Escape}');

    await waitFor(
      () =>
        expect(screen.queryByRole('dialog', { name: `Пересудить прогон №${RUN_ID}` })).toBeNull(),
      WAIT,
    );
    // Окно сайтов не закрылось: его вид — в адресе, а уходящее окно ещё стоит в разметке, пока
    // идёт переход, — поэтому по адресу, а не по разметке.
    expect(where()).toContain(`sites_run=${RUN_ID}`);
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(where()).toContain(`sites_run=${RUN_ID}`);
    expect(screen.getByRole('dialog', { name: WINDOW })).toBeInTheDocument();
    await waitFor(
      () => expect(screen.getByRole('button', { name: 'Пересудить прогон' })).toHaveFocus(),
      WAIT,
    );
  });
});
