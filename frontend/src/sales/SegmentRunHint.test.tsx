/**
 * Отчёт прогона без простыни (замечание владельца 10.11.2026: «пояснения — не простынёй, а свёрнуто
 * или в «i»»). До правки перед таблицей ключей стояли семь абзацев: шапка, режим судьи, как судья
 * понял описание, второй шанс цитате, «уже в сборе гипотезы», лиды с причинами в скобках и что ждёт
 * повтора. Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; ниша, домены и
 * слова разбора выдуманы.
 *
 * Проверяется то, ради чего правка: как устроен прогон — в «i» у заголовка «Прогон №N», по порядку, и
 * на экране этих слов нет; сбой разбора описания — строкой на экране, а не в «i»; лиды — строкой без
 * числа плитки и без ждущих очистки, которых назвала группа с «Повторить сейчас», причины отказа
 * очистки — в свёрнутом «почему»; что требует решения сейчас, видно без раскрытия.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { SegmentHome, SegmentRunDetail, SegmentRunRow, SegmentWait } from './segmentApi';
import { leadsLine } from './segmentData';
import { RUN_HINT, runHintLines } from './SegmentRunHint';

const WAIT = { timeout: 5000 };
const RUN_ID = 57;
const SCOPE = 'страна сегмента по описанию — Польша; виды сайтов — сайты самих компаний';
const FAILED = 'описание сегмента не разобрано: ответ разбора описания не той формы';
const MODE = /^Судья в наблюдении: его «отказ» не отсекает сайт/;
const SCOPE_LINE = `Как судья понял описание: ${SCOPE}.`;
const RETRY_LINE = 'Цитату переспросили у модели: 7 сайтов, со второго раза нашлась у 5.';
const KNOWN_LINE =
  'Уже в сборе гипотезы: 4 — эти сайты нашли её прошлые прогоны, их не судим и не ищем снова.';

const run: SegmentRunRow = {
  id: RUN_ID,
  hypothesis_id: 21,
  created_at: '2026-11-10T08:00:00+00:00',
  finished_at: '2026-11-10T08:30:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'pl',
  language: 'en',
  depth_pages: 2,
  keywords: 5,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: null,
  alive_at: '2026-11-10T08:30:00+00:00',
  active: false,
  progress: null,
  watch: null,
};

const home: SegmentHome = {
  hypothesis_id: 21,
  segment: 'Студии йоги Кракова: свои занятия и абонементы на сайте, без каталогов чужих студий.',
  mode: 'shadow',
  countries: ['pl'],
  languages: ['en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [run],
};

const hypotheses: HypothesesView = {
  rows: [
    {
      id: 21,
      name: 'йога PL',
      description: home.segment,
      created_at: '2026-11-09T10:00:00+00:00',
      leads: { new: 3, ready: 5, rejected: 2 },
      total: 10,
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

/** Лиды ещё не очищены, срока нет — часы теста не нужны. */
const NOT_CLEANED: SegmentWait = {
  kind: 'cleaning',
  words: 'ещё не очищали',
  count: 3,
  at: null,
  again: true,
};

function detailOf(changes: Partial<SegmentRunDetail> = {}): SegmentRunDetail {
  return {
    run,
    hypothesis: 'йога PL',
    keywords: ['joga kraków'],
    estimate_lines: [],
    summary: { hosts: 30, accepted: 12, review: 2, rejected: 16, no_address: 2, leads: 10 },
    lanes: { accept: 12, review: 2, reject: 16 },
    rejected: [],
    no_address: [],
    waiting_address: 0,
    leads: { new: 3, ready: 5, rejected: 2 },
    lead_reasons: { duplicate: 2 },
    unanswered: 0,
    report: {
      scope: { words: SCOPE, countries: ['PL'] },
      judge: { judged: 28, quote_retried: 7, quote_retry_held: 5 },
      serp: { known: 4 },
    },
    waits: [NOT_CLEANED],
    again: [{ what: 'cleaning', count: 3, searches: 0, checks: 0 }],
    ...changes,
  };
}

async function openReport(detail: SegmentRunDetail = detailOf()): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/kb': { body: kb },
    'GET /api/sales/segment?hypothesis=21': { body: home },
    [`GET /api/sales/segment/runs/${RUN_ID}`]: { body: detail },
  });
  renderWith(<AppRoutes />, '/sales?tab=collect&hypothesis=21');
  await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
}

describe('как устроен прогон — в «i» у заголовка', () => {
  it('режим, разбор описания, второй шанс цитате и «уже в сборе» — в «i», на экране их нет', async () => {
    await openReport();
    const user = userEvent.setup();

    for (const shown of [MODE, SCOPE_LINE, RETRY_LINE, KNOWN_LINE]) {
      expect(screen.queryByText(shown)).toBeNull();
    }
    // «i» — в одной строке с заголовком прогона.
    const heading = screen.getByRole('heading', { name: `Прогон №${RUN_ID}` });
    const hint = screen.getByRole('button', { name: RUN_HINT });
    expect(heading.parentElement).toContainElement(hint);
    await user.hover(hint);

    const mode = await screen.findByText(MODE, {}, WAIT);
    const order = [
      mode,
      ...[SCOPE_LINE, RETRY_LINE, KNOWN_LINE].map((line) => screen.getByText(line)),
    ];
    order.slice(1).forEach((next, at) => {
      expect(
        order[at]!.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });
  });

  it('шапка прогона — одной строкой фактов на экране', async () => {
    await openReport();

    expect(
      screen.getByText(/^Язык писем — английский · выдача — 20 результатов на ключ · /),
    ).toBeInTheDocument();
  });

  it('сбой разбора описания — строкой на экране: по нему сайты ждут человека; в «i» его нет', async () => {
    const report = { scope: { failed: 'не той формы', words: FAILED } };
    await openReport(detailOf({ report }));

    expect(screen.getByText(`О${FAILED.slice(1)}.`)).toBeInTheDocument();
    expect(runHintLines(detailOf({ report })).join(' ')).not.toMatch(/описани/);
  });

  it.each([
    ['enforce', 'Судья режет: «отказ» отсекает сайт.'],
    ['off', 'Судья выключен: каждый сайт решает человек.'],
  ] as const)(
    'режим «%s» — своими словами; без разбора, второго вопроса и «уже в сборе» — одна строка',
    (mode, words) => {
      const lines = runHintLines(detailOf({ run: { ...run, mode }, report: {} }));

      expect(lines).toEqual([words]);
    },
  );
});

describe('лиды — каждое число один раз', () => {
  it('строка лидов — без числа плитки и без ждущих очистки: их называет группа с кнопкой', async () => {
    await openReport();

    expect(
      screen.getByText('Лиды: готовы к письмам — 5, отклонены очисткой — 2.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Лидов заведено/)).toBeNull();
    // Решение «сейчас» — на виду, без раскрытия: группа, число и кнопка.
    expect(screen.getByText('Ждут очистки — 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить сейчас' })).toBeInTheDocument();
  });

  it('причины отказа очистки — в свёрнутом «почему», а не скобками в строке', async () => {
    await openReport();
    const user = userEvent.setup();
    const fold = screen.getByRole('button', { name: 'Почему отклонены очисткой' });

    expect(fold).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/дубль/)).toBeNull();
    await user.click(fold);

    const reasons = await screen.findByRole('list', { name: 'Отклонены очисткой — почему' }, WAIT);
    expect(within(reasons).getByRole('listitem')).toHaveTextContent('дубль — 2');
  });

  it('с отсеянными сайтами и без адреса — одна свёрнутая строка на всё «почему»', async () => {
    await openReport(
      detailOf({
        rejected: [{ words: 'каталог студий: списки чужих занятий', count: 16 }],
        no_address: [{ words: 'адреса на сайте нет', count: 2 }],
      }),
    );

    expect(
      screen.getByRole('button', { name: 'Почему отсечены, без адреса и отклонены очисткой' }),
    ).toBeInTheDocument();
  });

  it('группы ожидания нет — ждущие очистки в строке лидов; лидов нет — строки нет', () => {
    expect(leadsLine(detailOf({ waits: [] }))).toBe(
      'Лиды: готовы к письмам — 5, отклонены очисткой — 2, ждут очистки — 3.',
    );
    expect(leadsLine(detailOf({ leads: { new: 0, ready: 4, rejected: 0 } }))).toBe(
      'Лиды: готовы к письмам — 4.',
    );
    expect(leadsLine(detailOf({ leads: { new: 0, ready: 0, rejected: 0 } }))).toBeNull();
    expect(leadsLine(detailOf({ leads: {} }))).toBeNull();
  });
});
