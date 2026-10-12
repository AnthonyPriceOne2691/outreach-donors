/**
 * Вкладка «Агент: качество»: доли — от решений оператора, малая основа — «данных мало»,
 * ворота автоответа — словами сервера, версия агента — в адресе, правки — «черновик → ушло».
 * Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; тексты выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type {
  AgentQualityView,
  ChangeRow,
  CohortView,
  QualityCounts,
  SituationRow,
} from '../api/salesQuality';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';
import { percentOf, shareText } from './qualityData';

const SCREEN_WAIT = { timeout: 5000 };
const QUALITY = '/api/sales/agent-quality';

const HYPOTHESES = {
  rows: [
    {
      id: 5,
      name: 'тестовая гипотеза',
      description: null,
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

function counts(values: Partial<QualityCounts>): QualityCounts {
  return {
    as_is: 0,
    edited: 0,
    rejected: 0,
    true_silence: 0,
    false_silence: 0,
    held_sent: 0,
    held_rejected: 0,
    held_waiting: 0,
    waiting: 0,
    auto: 0,
    ...values,
  };
}

const NOW: CohortView = {
  id: 'aaaaaaaaaa',
  reply: 'sales-reply-v3',
  situation: 'sales-situation-v1',
  judge: 'sales-judge-v2',
  moves: 'sales-moves-v1',
  kb: 'kb-1a2b3c4d5e6f',
};
const BEFORE: CohortView = { ...NOW, id: 'bbbbbbbbbb', reply: 'sales-reply-v2' };

function situation(
  name: string,
  title: string,
  values: Partial<QualityCounts>,
  gate: SituationRow['gate'],
): SituationRow {
  return { situation: name, title, counts: counts(values), gate };
}

const EDITED: ChangeRow = {
  draft_id: 41,
  thread_id: 7,
  situation: 'asks_price',
  title: 'спрашивает цену',
  turn: 1,
  decided_at: '2026-10-12T09:00:00+00:00',
  decision: 'edited',
  held: false,
  held_reason: null,
  reason: null,
  draft: 'Спасибо за вопрос. Аудит стоит 500 долларов.',
  sent: 'Спасибо за вопрос. Цену назовём на созвоне.',
  parts: [
    { kind: 'same', text: 'Спасибо за вопрос. ' },
    { kind: 'removed', text: 'Аудит стоит 500 долларов.' },
    { kind: 'added', text: 'Цену назовём на созвоне.' },
  ],
};

const REJECTED: ChangeRow = {
  ...EDITED,
  draft_id: 42,
  thread_id: 8,
  decision: 'rejected',
  held: true,
  held_reason: 'судья: сумма не из базы',
  reason: 'длинно',
  draft: 'Очень длинный выдуманный черновик.',
  sent: null,
  parts: [],
};

function view(values: Partial<AgentQualityView> = {}): AgentQualityView {
  return {
    limits: { min_n: 30, as_is: 80, silence: 90, settle_days: 3 },
    current: NOW,
    shown: NOW,
    cohorts: [
      {
        cohort: NOW,
        current: true,
        first_at: '2026-10-10T09:00:00+00:00',
        last_at: '2026-10-12T09:00:00+00:00',
        counts: counts({ as_is: 34, edited: 4, rejected: 2 }),
      },
      {
        cohort: BEFORE,
        current: false,
        first_at: '2026-10-01T09:00:00+00:00',
        last_at: '2026-10-09T09:00:00+00:00',
        counts: counts({ as_is: 3, rejected: 9 }),
      },
    ],
    counts: counts({
      as_is: 34,
      edited: 4,
      rejected: 2,
      true_silence: 29,
      false_silence: 1,
      held_sent: 2,
      held_rejected: 1,
      waiting: 5,
    }),
    situations: [
      situation(
        'asks_price',
        'спрашивает цену',
        { as_is: 34, edited: 4, rejected: 2 },
        { state: 'passed', said: ['без правки 85% (34 из 40) — не ниже 80%'] },
      ),
      situation(
        'ack',
        'спасибо, получил',
        { true_silence: 29, false_silence: 1 },
        { state: 'passed', said: ['верное молчание 96,7% (29 из 30) — не ниже 90%'] },
      ),
      situation(
        'asks_info',
        'просит подробности',
        { as_is: 3, edited: 9 },
        { state: 'few', said: ['данных мало: решений по готовым черновикам 12 из 30'] },
      ),
    ],
    reasons: [
      { reason: 'длинно', count: 2 },
      { reason: 'другое', count: 0 },
    ],
    changes: [EDITED, REJECTED],
    ...values,
  };
}

/** Где сейчас экран: адрес целиком — по нему видно, что живёт в адресе. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

function openQuality(
  answer: Answer,
  path = '/sales?tab=agent',
  routes: Record<string, Answer> = {},
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [`GET ${QUALITY}`]: answer,
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  return recorded;
}

/** Плитка: подпись, число и пояснение — тексты её трёх строк. */
function tile(title: string): string[] {
  const label = screen.getByText(title, { selector: '.metricTitle' });
  const card = label.closest('.metricTile');
  if (!(card instanceof HTMLElement)) throw new Error(`плитки «${title}» нет`);
  return Array.from(card.children).map((child) => child.textContent ?? '');
}

/** Ячейка таблицы: число и доля под ним — строками ячейки через « / ». */
function cellText(cell: HTMLElement): string {
  return Array.from(cell.querySelectorAll('p'))
    .map((line) => line.textContent)
    .join(' / ');
}

function calls(recorded: Recorded, path: string): string[] {
  return recorded.calls.filter((call) => call.path.startsWith(path)).map((call) => call.path);
}

describe('продажи: вкладка «Агент: качество»', () => {
  it('доли — от решений оператора по готовым черновикам, основа — числом под долей', async () => {
    openQuality({ body: view() });
    await screen.findByText('Как есть', { selector: '.metricTitle' }, SCREEN_WAIT);

    expect(tile('Решений оператора')).toEqual(['Решений оператора', '40', 'ждут решения: 5']);
    expect(tile('Как есть')).toEqual(['Как есть', '85%', '34 из 40']);
    expect(tile('С правкой')).toEqual(['С правкой', '10%', '4 из 40']);
    expect(tile('Отклонено')).toEqual(['Отклонено', '5%', '2 из 40']);
    expect(tile('Ложное молчание')).toEqual([
      'Ложное молчание',
      '1',
      'из 30 молчаний; верных — 96,7%',
    ]);
    expect(tile('Отдано человеку')).toEqual(['Отдано человеку', '3', 'отправлено 2 · отклонено 1']);
  });

  it('меньше N решений — «данных мало», без процента и без вывода', async () => {
    const few = view({ counts: counts({ as_is: 28, edited: 1, true_silence: 4 }) });
    openQuality({ body: few });
    await screen.findByText('Как есть', { selector: '.metricTitle' }, SCREEN_WAIT);

    expect(tile('Как есть')).toEqual(['Как есть', 'данных мало', '28 из 29']);
    expect(tile('С правкой')[1]).toBe('данных мало');
    expect(tile('Ложное молчание')[2]).toBe('из 4 молчаний; верных — данных мало');
    const situations = screen.getByRole('table', { name: 'Качество агента по ситуации письма' });
    const info = within(situations).getByText('просит подробности').closest('tr');
    if (!(info instanceof HTMLElement)) throw new Error('строки ситуации нет');
    expect(within(info).getByText('данных мало')).toBeInTheDocument();
    expect(info.querySelectorAll('.qualityShare')).toHaveLength(0);
  });

  it('ворота автоответа по ситуации — значок и слова сервера, флага на экране нет', async () => {
    openQuality({ body: view() });
    const table = await screen.findByRole(
      'table',
      { name: 'Качество агента по ситуации письма' },
      SCREEN_WAIT,
    );

    const price = within(table).getByText('спрашивает цену').closest('tr');
    if (!(price instanceof HTMLElement)) throw new Error('строки ситуации нет');
    expect(within(price).getByText('проходит')).toBeInTheDocument();
    expect(within(price).getByText('без правки 85% (34 из 40) — не ниже 80%')).toBeInTheDocument();
    expect(within(price).getByText('85%')).toBeInTheDocument();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(
      screen.getByText(/Автоответ ситуации включает человек/, { selector: '.qualityTableNote' }),
    ).toBeInTheDocument();
  });

  it('версия агента — выбором, номер уходит на сервер и пишется в адрес', async () => {
    const recorded = openQuality({ body: view() }, '/sales?tab=agent&hypothesis=5', {
      [`GET ${QUALITY}?cohort=${BEFORE.id}`]: {
        body: view({ shown: BEFORE, counts: counts({ as_is: 3, rejected: 9 }) }),
      },
    });
    const user = userEvent.setup();
    const field = await screen.findByRole('textbox', { name: 'Версия агента' }, SCREEN_WAIT);
    expect(field).toHaveValue('sales-reply-v3 · база kb-1a2b3c4d5e6f — текущая');

    // Выпадающий список Mantine в jsdom остаётся `display: none` — пункты ищутся с `hidden`.
    await user.click(field);
    await user.click(
      await screen.findByRole(
        'option',
        { name: 'sales-reply-v2 · база kb-1a2b3c4d5e6f', hidden: true },
        SCREEN_WAIT,
      ),
    );

    await waitFor(() => expect(tile('Решений оператора')[1]).toBe('12'), SCREEN_WAIT);
    expect(calls(recorded, QUALITY)).toEqual([QUALITY, `${QUALITY}?cohort=${BEFORE.id}`]);
    expect(screen.getByTestId('where').textContent).toBe(
      `/sales?tab=agent&hypothesis=5&cohort=${BEFORE.id}`,
    );
  });

  it('номер версии из адреса уходит на сервер — после перезагрузки та же версия', async () => {
    const recorded = openQuality({ body: view() }, `/sales?tab=agent&cohort=${BEFORE.id}`, {
      [`GET ${QUALITY}?cohort=${BEFORE.id}`]: { body: view({ shown: BEFORE }) },
    });
    await screen.findByText('Как есть', { selector: '.metricTitle' }, SCREEN_WAIT);

    expect(calls(recorded, QUALITY)).toEqual([`${QUALITY}?cohort=${BEFORE.id}`]);
  });

  it('номер не той формы из адреса — «текущая версия», в запрос он не идёт', async () => {
    const recorded = openQuality({ body: view() }, '/sales?tab=agent&cohort=../x');
    await screen.findByText('Как есть', { selector: '.metricTitle' }, SCREEN_WAIT);

    expect(calls(recorded, QUALITY)).toEqual([QUALITY]);
    expect(screen.getByTestId('where').textContent).toBe('/sales?tab=agent&cohort=../x');
  });

  it('текущая версия ещё не писала — сказано, что числа по прежней', async () => {
    const earlier = view({
      shown: BEFORE,
      cohorts: [view().cohorts[1]!].map((row) => ({ ...row, current: false })),
    });
    openQuality({ body: earlier });

    expect(
      await screen.findByText(
        'Текущая версия агента черновиков ещё не писала — числа ниже по прежней.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/черновик sales-reply-v2 · ситуация sales-situation-v1/)).toBeVisible();
  });

  it('правка — черновик и ушедшее: убранное зачёркнуто, дописанное подчёркнуто', async () => {
    openQuality({ body: view() });
    const changes = await screen.findByRole(
      'region',
      { name: 'Последние правки и отклонения' },
      SCREEN_WAIT,
    );
    const [edited, rejected] = within(changes).getAllByRole('article');
    if (edited === undefined || rejected === undefined) throw new Error('правок нет');

    expect(edited.querySelector('del')?.textContent).toBe('Аудит стоит 500 долларов.');
    expect(edited.querySelector('ins')?.textContent).toBe('Цену назовём на созвоне.');
    expect(within(edited).getByRole('link', { name: 'Открыть диалог' })).toHaveAttribute(
      'href',
      '/threads/7',
    );
    expect(within(edited).getByText('с правкой')).toBeInTheDocument();
    // Отклонение: справа — причина оператора; отданный человеку — почему отдан.
    expect(within(rejected).getByText('отклонено')).toBeInTheDocument();
    expect(within(rejected).getByText('отдан человеку')).toBeInTheDocument();
    expect(within(rejected).getByText('Причина отклонения')).toBeInTheDocument();
    expect(within(rejected).getByText('длинно')).toBeInTheDocument();
    expect(
      within(rejected).getByText('Агент отдал человеку: судья: сумма не из базы'),
    ).toBeInTheDocument();
    expect(rejected.querySelector('del')).toBeNull();
  });

  it('правок больше пяти — видны пять, остальные — по кнопке с её состоянием', async () => {
    const many = Array.from({ length: 7 }, (_, index) => ({ ...EDITED, draft_id: 100 + index }));
    openQuality({ body: view({ changes: many }) });
    const changes = await screen.findByRole(
      'region',
      { name: 'Последние правки и отклонения' },
      SCREEN_WAIT,
    );
    expect(within(changes).getAllByRole('article')).toHaveLength(5);
    const more = within(changes).getByRole('button', { name: 'Показать ещё 2' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    expect(more).toHaveAttribute('aria-controls', 'quality-changes-rest');

    await userEvent.setup().click(more);

    expect(within(changes).getAllByRole('article')).toHaveLength(7);
    expect(within(changes).getByRole('button', { name: 'Свернуть' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('у ситуации без основы — прочерк, а не ноль: агент там не молчит или не пишет', async () => {
    openQuality({ body: view() });
    const table = await screen.findByRole(
      'table',
      { name: 'Качество агента по ситуации письма' },
      SCREEN_WAIT,
    );

    const ack = within(table).getByText('спасибо, получил').closest('tr');
    if (!(ack instanceof HTMLElement)) throw new Error('строки ситуации нет');
    const cells = Array.from(ack.querySelectorAll('td')).map(cellText);
    // Решений 0 · как есть, правка, отклонено — без основы · молчание 29 из 30 · отдано 0.
    expect(cells.slice(1, 7)).toEqual(['0', '—', '—', '—', '29 / 96,7%', '0']);
  });

  it('причины отклонения — числом, доля — только от N отклонений', async () => {
    openQuality({ body: view() });
    const table = await screen.findByRole('table', { name: 'Причины отклонения' }, SCREEN_WAIT);

    const long = within(table).getByText('длинно').closest('tr');
    if (!(long instanceof HTMLElement)) throw new Error('строки причины нет');
    expect(Array.from(long.querySelectorAll('td')).map((cell) => cell.textContent)).toEqual([
      'длинно',
      '2',
      '—',
    ]);
  });

  it('версии агента — каждая своими числами, текущая и показанная помечены', async () => {
    openQuality({ body: view() });
    const table = await screen.findByRole('table', { name: 'Версии агента' }, SCREEN_WAIT);

    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('текущая, показана');
    expect(rows[1]).toHaveTextContent('sales-reply-v2');
    expect(rows[1]).not.toHaveTextContent('текущая');
  });

  it('черновиков ещё нет — словами, без таблиц', async () => {
    openQuality({ body: view({ cohorts: [], changes: [] }) });

    expect(
      await screen.findByText(/Черновиков агента продаж ещё нет/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('отказ сервера — словами', async () => {
    openQuality({ status: 503, body: { detail: 'выдуманный отказ теста' } });

    expect(
      await screen.findByText('Качество агента не загрузилось', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText('выдуманный отказ теста')).toBeInTheDocument();
  });
});

describe('продажи: доли качества агента', () => {
  const limits = { min_n: 30, as_is: 80, silence: 90, settle_days: 3 };

  it('у порога доля — по свою сторону порога, а не округлённая через него', () => {
    expect(percentOf(24, 30, 80)).toBe('80%');
    expect(percentOf(239, 300, 80)).toBe('79,7%');
    expect(percentOf(3999, 5000, 80)).toBe('79,98%');
    expect(percentOf(1, 3)).toBe('33,3%');
  });

  it('основа меньше N — «данных мало»; ровно N — доля', () => {
    expect(shareText(29, 29, limits)).toBe('данных мало');
    expect(shareText(24, 30, limits, 80)).toBe('80%');
    expect(shareText(0, 0, limits)).toBe('данных мало');
  });
});
