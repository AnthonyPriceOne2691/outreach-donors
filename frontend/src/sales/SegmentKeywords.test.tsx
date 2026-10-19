/**
 * Ключи сбора чипами (`SegmentKeywords`) и снятие ключей галочкой в смете (`SegmentForm`): у
 * взятого — галочка, у отсеянного — нет; снятый — «снят вами», а не отсеян; смета
 * пересчитывается сама, без кнопки и с задержкой на щелчки; запуск — со снятыми строками и
 * отпечатком сметы, которую видел человек; сняты все — запуск закрыт словами; правка поля
 * ключей снятый не теряет; ответ на прошлое состояние галочек не перебивает новое. Ключи в
 * отчёте прогона — таблицей (`SegmentRunKeywords.test.tsx`).
 *
 * Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; ключи — разных ниш и
 * языков, домены выдуманы.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { HELP_QUIET } from './segmentHelpFixtures';
import type { SegmentAsk, SegmentEstimate, SegmentHome } from './segmentApi';

const SCREEN_WAIT = { timeout: 5000 };
const SEGMENT_TEXT = 'Talleres de bicicletas: reparan y venden bicicletas en su propio local.';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 9,
      name: 'talleres ES',
      description: SEGMENT_TEXT,
      created_at: '2026-10-03T10:00:00+00:00',
      leads: { new: 0, ready: 0, rejected: 0 },
      total: 0,
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

const HOME: SegmentHome = {
  hypothesis_id: 9,
  segment: SEGMENT_TEXT,
  mode: 'shadow',
  countries: ['de', 'es', 'us'],
  languages: ['ru', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [],
};

/** Пять строк: две взятые, оператор поиска, ещё взятая и повтор первой. */
const KEYWORDS = [
  'reparación de bicicletas',
  'reparación de bicicletas madrid',
  'site:bicis.example.test talleres',
  'taller de bicicletas',
  'Reparación de bicicletas',
].join('\n');
const LINES: Record<string, number> = {
  'reparación de bicicletas': 1,
  'reparación de bicicletas madrid': 2,
  'taller de bicicletas': 4,
};
const NEAR =
  'похож на «reparación de bicicletas» — общих слов 3 из 4: выдача может частично совпасть, ключ взят';
const OPERATOR = 'поисковый оператор: так пользователи не ищут';
const REPEAT = 'повтор строки 1: для выдачи это тот же запрос — второй раз за него не платим';
const ALL_OFF = 'все взятые ключи сняты — выдаче покупать нечего: верните галочку хотя бы одному';

/** Смета сервера по снятым строкам: снятые — отдельно, числа и отпечаток — свои. */
function estimateFor(excluded: number[]): SegmentEstimate {
  const off = Object.keys(LINES).filter((phrase) => excluded.includes(LINES[phrase] ?? 0));
  const bought = Object.keys(LINES).filter((phrase) => !off.includes(phrase));
  return {
    keywords: bought,
    excluded: off,
    keyword_lines: LINES,
    rejected: { 'site:bicis.example.test talleres': OPERATOR, 'Reparación de bicicletas': REPEAT },
    notes: { 'reparación de bicicletas madrid': NEAR },
    segment: SEGMENT_TEXT,
    digest: `seen-${excluded.join('-') || 'all'}`,
    country: 'es',
    language: 'en',
    depth_pages: 1,
    mode: 'shadow',
    serp_mode: 'live',
    serp_page_usd: 0.002,
    serp_wait_s: 10,
    results: bought.length * 10,
    share: 0.5,
    share_runs: 3,
    hosts: bought.length * 5,
    known: 0,
    sites: bought.length * 5,
    serp_usd: bought.length * 0.002,
    tokens: bought.length * 5500,
    tokens_usd: 0,
    tokens_left: null,
    hunter_searches: 0,
    hunter_checks: 0,
    lines: bought.length === 0 ? [] : [`Выдача — быстрая (Live): ключей ${bought.length}.`],
    refusal: bought.length === 0 ? ALL_OFF : null,
  };
}

function estimated(call: Call): Answer {
  return { body: estimateFor((call.body as SegmentAsk).excluded) };
}

async function openForm(routes: Record<string, Answer | ((call: Call) => Answer)> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...HELP_QUIET,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=9': { body: HOME },
    'POST /api/sales/segment/estimate': estimated,
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=collect');
  await screen.findByText('Сегмент гипотезы', {}, SCREEN_WAIT);
  return recorded;
}

/** Ключи — вставкой, одним изменением поля, и смета — кнопкой. */
async function countEstimate(user: ReturnType<typeof userEvent.setup>, text = KEYWORDS) {
  await user.click(screen.getByRole('textbox', { name: 'Ключи' }));
  await user.paste(text);
  await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
  await screen.findByRole('list', { name: 'Ключи сметы' }, SCREEN_WAIT);
}

function estimates(recorded: Recorded): SegmentAsk[] {
  return recorded.calls
    .filter((call) => call.method === 'POST' && call.path === '/api/sales/segment/estimate')
    .map((call) => call.body as SegmentAsk);
}

function chip(name: string): HTMLElement {
  return screen.getByRole('checkbox', { name });
}

function itemOf(text: string): HTMLElement {
  const list = screen.getByRole('list', { name: 'Ключи сметы' });
  const found = within(list)
    .getAllByRole('listitem')
    .find((item) => item.textContent?.includes(text));
  if (found === undefined) throw new Error(`чипа «${text}» нет`);
  return found;
}

function startButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Запустить прогон' });
}

describe('ключи сметы чипами и снятие галочкой', () => {
  it('у взятого — галочка, у отсеянного — нет; пометка и причина — рядом', async () => {
    await openForm();
    const user = userEvent.setup();

    await countEstimate(user);

    const boxes = screen.getAllByRole('checkbox');
    expect(
      ['reparación de bicicletas', 'reparación de bicicletas madrid', 'taller de bicicletas'].map(
        (name) => (chip(name) as HTMLInputElement).checked,
      ),
    ).toEqual([true, true, true]);
    expect(boxes).toHaveLength(3);
    expect(screen.queryByRole('checkbox', { name: /site:/ })).toBeNull();
    expect(itemOf('site:bicis.example.test talleres')).toHaveTextContent(
      `отсеян: site:bicis.example.test talleres ${OPERATOR}`,
    );
    expect(itemOf('Reparación de bicicletas')).toHaveTextContent(
      `отсеян: Reparación de bicicletas ${REPEAT}`,
    );
    expect(itemOf('reparación de bicicletas madrid')).toHaveTextContent(
      `reparación de bicicletas madrid ${NEAR}`,
    );
    expect(
      screen.getByText(/^Взято 3 из 5 · отсеяно 2\. Снимите галочку — ключ не покупается/),
    ).toBeInTheDocument();
  });

  it('снял галочку — смета пересчитывается сама: номер строки, как вписана, и запуск ждёт пересчёта', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    await countEstimate(user);
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);

    await user.click(chip('reparación de bicicletas madrid'));

    // Сразу: снятый стоит на месте «снят вами» — это не отсев; запуск закрыт до пересчёта.
    expect(chip('reparación de bicicletas madrid')).not.toBeChecked();
    expect(itemOf('reparación de bicicletas madrid')).toHaveTextContent(`снят вами; ${NEAR}`);
    expect(itemOf('reparación de bicicletas madrid')).not.toHaveTextContent('отсеян');
    expect(startButton()).toBeDisabled();
    expect(screen.getByText(/Пересчитываем смету…/)).toBeInTheDocument();

    // Без кнопки: смета пересчиталась сама — со снятой строкой 2.
    await waitFor(() => expect(estimates(recorded)).toHaveLength(2), SCREEN_WAIT);
    expect(estimates(recorded)[1]?.excluded).toEqual([2]);
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);
    expect(screen.getByText('Выдача — быстрая (Live): ключей 2.')).toBeInTheDocument();
    expect(screen.getByText(/^Взято 2 из 5 · снято вами 1 · отсеяно 2\./)).toBeInTheDocument();

    // Галочку вернули — ключ снова покупается.
    await user.click(chip('reparación de bicicletas madrid'));
    await waitFor(() => expect(estimates(recorded)).toHaveLength(3), SCREEN_WAIT);
    expect(estimates(recorded)[2]?.excluded).toEqual([]);
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);
    expect(chip('reparación de bicicletas madrid')).toBeChecked();
  });

  it('с клавиатуры: пробел снимает и возвращает галочку, фокус остаётся на ключе', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    await countEstimate(user);

    chip('reparación de bicicletas madrid').focus();
    await user.keyboard(' ');

    expect(chip('reparación de bicicletas madrid')).not.toBeChecked();
    expect(chip('reparación de bicicletas madrid')).toHaveFocus();
    await waitFor(() => expect(estimates(recorded)).toHaveLength(2), SCREEN_WAIT);
    expect(chip('reparación de bicicletas madrid')).toHaveFocus();
    await user.keyboard(' ');
    expect(chip('reparación de bicicletas madrid')).toBeChecked();
    expect(chip('reparación de bicicletas madrid')).toHaveFocus();
  });

  it('щелчки подряд — один пересчёт, после последнего', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    await countEstimate(user);

    await user.click(chip('reparación de bicicletas madrid'));
    await user.click(chip('taller de bicicletas'));

    await waitFor(() => expect(estimates(recorded)).toHaveLength(2), SCREEN_WAIT);
    expect(estimates(recorded)[1]?.excluded).toEqual([2, 4]);
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);
    // Третьего запроса нет: два щелчка — одна смета.
    await new Promise((settle) => setTimeout(settle, 700));
    expect(estimates(recorded)).toHaveLength(2);
  });

  it('запуск — со снятыми строками и отпечатком той сметы, которую видел человек', async () => {
    const recorded = await openForm({
      'POST /api/sales/segment/runs': { body: { run_id: 31, job_id: 'sales-segment-31' } },
      'GET /api/sales/segment?hypothesis=9': { body: HOME },
    });
    const user = userEvent.setup();
    await countEstimate(user);

    await user.click(chip('taller de bicicletas'));
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);
    await user.click(startButton());

    await waitFor(
      () =>
        expect(
          recorded.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/runs')),
        ).toHaveLength(1),
      SCREEN_WAIT,
    );
    const [sent] = recorded.calls.filter(
      (call) => call.method === 'POST' && call.path === '/api/sales/segment/runs',
    );
    expect(sent?.body).toEqual({ ...estimates(recorded)[1], digest: 'seen-4' });
    expect((sent?.body as SegmentAsk).excluded).toEqual([4]);
  });

  it('сняты все взятые — запуск закрыт словами, а галочки можно вернуть', async () => {
    await openForm();
    const user = userEvent.setup();
    await countEstimate(user);

    for (const name of Object.keys(LINES)) await user.click(chip(name));

    expect(await screen.findByText(ALL_OFF, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByText('Прогон не запустится')).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    expect(screen.getAllByRole('checkbox').every((box) => !(box as HTMLInputElement).checked)).toBe(
      true,
    );
  });

  it('правка поля ключей снятый не теряет: строка выше — номер ищется по тексту', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    await countEstimate(user);
    await user.click(chip('taller de bicicletas'));
    await waitFor(() => expect(estimates(recorded)).toHaveLength(2), SCREEN_WAIT);

    const field = screen.getByRole('textbox', { name: 'Ключи' });
    fireEvent.change(field, { target: { value: `bicicletas eléctricas\n${KEYWORDS}` } });
    // Правка поля снимает смету — её считают заново кнопкой.
    expect(screen.queryByRole('list', { name: 'Ключи сметы' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    await waitFor(() => expect(estimates(recorded)).toHaveLength(3), SCREEN_WAIT);
    expect(estimates(recorded)[2]?.excluded).toEqual([5]);
  });

  it('ответ на прошлое состояние галочек не перебивает новое', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((settle) => {
      release = settle;
    });
    await openForm();
    // Ответ сметы со снятой строкой 2 задержан, пока его не отпустят.
    let asked = false;
    const fetchSpy = vi.mocked(globalThis.fetch);
    const answer = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation(async (input, init) => {
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as SegmentAsk) : null;
      if (body?.excluded?.includes(2)) {
        asked = true;
        await held;
      }
      return answer === undefined ? Promise.reject(new Error('нет ответа')) : answer(input, init);
    });
    const user = userEvent.setup();
    await countEstimate(user);
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);

    await user.click(chip('reparación de bicicletas madrid'));
    await waitFor(() => expect(asked).toBe(true), SCREEN_WAIT);
    // Передумали до ответа: галочка снова стоит — смета та же, что посчитана.
    await user.click(chip('reparación de bicicletas madrid'));
    await waitFor(() => expect(startButton()).toBeEnabled(), SCREEN_WAIT);

    release();
    await new Promise((settle) => setTimeout(settle, 50));

    expect(chip('reparación de bicicletas madrid')).toBeChecked();
    expect(startButton()).toBeEnabled();
    expect(screen.getByText('Выдача — быстрая (Live): ключей 3.')).toBeInTheDocument();
  });

  it('пересчёт не прошёл — сказано словами, запуск закрыт, смету считают кнопкой', async () => {
    await openForm({
      'POST /api/sales/segment/estimate': (call: Call) =>
        (call.body as SegmentAsk).excluded.length > 0
          ? { status: 503, body: { detail: 'База не ответила — попробуйте через минуту' } }
          : estimated(call),
    });
    const user = userEvent.setup();
    await countEstimate(user);

    await user.click(chip('taller de bicicletas'));

    expect(
      await screen.findByText('База не ответила — попробуйте через минуту', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText(/Смета не пересчитана — посчитайте её кнопкой/)).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
  });

  it('описание поправили в другой вкладке — смета не показана, описание перечитано', async () => {
    const recorded = await openForm({
      'POST /api/sales/segment/estimate': {
        body: { ...estimateFor([]), segment: 'Talleres y tiendas de bicicletas.' },
      },
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole('textbox', { name: 'Ключи' }));
    await user.paste(KEYWORDS);
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('Смета не показана', {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Ключи сметы' })).toBeNull();
    expect(startButton()).toBeDisabled();
    await waitFor(
      () =>
        expect(
          recorded.calls.filter((call) => call.path === '/api/sales/segment?hypothesis=9').length,
        ).toBeGreaterThan(1),
      SCREEN_WAIT,
    );
  });
});
