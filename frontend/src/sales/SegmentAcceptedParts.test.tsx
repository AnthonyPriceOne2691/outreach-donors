/**
 * Из чего складываются принятые — под кнопкой «Найти адреса ещё N из M принятых» (тест продаж
 * 22.10.2026: «Принято 99», а на кнопке «· 8», и откуда 8, с экрана было не понять). Ответы сервера
 * — записанные (`serve()`), незаписанный запрос роняет тест; домены и слова выдуманы, ниши и языки —
 * разные.
 *
 * Проверяется: части — числами ответа сервера и сходятся с «Принято» («Из M принятых»); не сходятся
 * (часть «без адреса» берёт и кнопка) — без «из M» и со словами почему, а не подогнанным числом;
 * ждут повтора — внутри «без адреса»: сумма и ближайший срок строк «Ждут адреса»; строка о
 * принятых вручную; без кнопки (прогон идёт, у принятых всё найдено) и у остановленного прогона
 * расшифровки нет; без права «run» расшифровка есть, а кнопки нет.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { PERMISSION_TITLES } from '../api/labels';
import type { Me } from '../api/types';
import { formatTime } from '../format';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';
import type { SegmentRunDetail, SegmentRunRow, SegmentWait } from './segmentApi';
import { acceptedParts } from './SegmentAcceptedParts';
import { SegmentReport } from './SegmentReport';

const WAIT = { timeout: 5000 };
const RUN_ID = 57;
const READ = `/api/sales/segment/runs/${RUN_ID}`;
/** «Сейчас» чистой функции — передаётся явно, часы не подменяются. */
const NOW = new Date('2026-10-22T09:00:00+00:00');
const LATER = '2026-10-22T15:40:00+00:00';
const PASSED = '2026-10-22T08:20:00+00:00';
const HELD = 'компания уже в работе гипотезы «Dachdecker AT» — адрес не ищем, лида не заводим';
const MANUAL =
  'Сайты, принятые вручную после прогона, ищет эта кнопка: поиск адреса платный, его запускает человек.';
const OVERLAP =
  'среди них и сайты без адреса: у них настал срок повтора или на домене уже есть адрес';

/** Тире перед числом — неразрывно, как на экране: «адреса на сайте нет\u00a0—\u00a01». */
function nb(text: string): string {
  return text.replace(/ — (?=\d)/g, '\u00a0—\u00a0');
}

const run: SegmentRunRow = {
  id: RUN_ID,
  hypothesis_id: 8,
  created_at: '2026-10-22T07:00:00+00:00',
  finished_at: '2026-10-22T07:30:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'de',
  language: 'en',
  depth_pages: 1,
  keywords: 2,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: null,
  alive_at: '2026-10-22T07:30:00+00:00',
  active: false,
  progress: null,
  watch: null,
};

/** Срок впереди — строка «Ждут адреса»; очистка в расшифровку принятых не входит. */
const SILENT_LATER: SegmentWait = {
  kind: 'address',
  words: 'сайт не ответил поиску адреса',
  count: 1,
  at: LATER,
  again: false,
};
const CLEANING: SegmentWait = {
  kind: 'cleaning',
  words: 'Hunter ещё проверяет адрес',
  count: 4,
  at: PASSED,
  again: true,
};

/** Прогон, где части сходятся: 12 принятых = 7 с лидом + 3 без адреса + 2 ещё не искали. */
function detailOf(changes: Partial<SegmentRunDetail> = {}): SegmentRunDetail {
  return {
    run,
    hypothesis: 'кровельщики DE',
    keywords: ['dachdecker münchen', 'dachsanierung kosten'],
    estimate_lines: [],
    summary: { hosts: 20, accepted: 12, review: 0, rejected: 8, no_address: 3, leads: 7 },
    lanes: { pending: 0, accept: 12, review: 0, reject: 8 },
    rejected: [{ words: 'каталог фирм, а не сайт компании', count: 8 }],
    no_address: [
      { words: 'адреса на сайте нет', count: 1 },
      { words: 'сайт не ответил — повтор по сроку', count: 1 },
      { words: HELD, count: 1 },
    ],
    waiting_address: 2,
    leads: { new: 1, ready: 5, rejected: 1 },
    lead_reasons: { duplicate: 1 },
    unanswered: 0,
    report: {},
    waits: [SILENT_LATER, CLEANING],
    ...changes,
  };
}

/** Прогон, где часть «без адреса» берёт и кнопка: квота платного поиска — «повтор следующим
 *  проходом», и срок повтора у сайта, не ответившего поиску, настал. 10 принятых, а частей —
 *  4 + 4 + 4. */
function overlapping(): SegmentRunDetail {
  return detailOf({
    hypothesis: 'kitabevleri TR',
    summary: { hosts: 15, accepted: 10, review: 0, rejected: 5, no_address: 4, leads: 4 },
    lanes: { pending: 0, accept: 10, review: 0, reject: 5 },
    no_address: [
      { words: 'сайт не ответил — повтор по сроку', count: 3 },
      { words: 'квота платного поиска — повтор следующим проходом', count: 1 },
    ],
    waiting_address: 4,
    waits: [
      SILENT_LATER,
      { ...SILENT_LATER, count: 2, at: PASSED, again: true },
      {
        kind: 'address',
        words: 'квота платного поиска — повтор следующим проходом',
        count: 1,
        at: null,
        again: true,
      },
    ],
  });
}

function openReport(detail: SegmentRunDetail, me: Me = ADMIN, routes: Record<string, Answer> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({ 'GET /api/auth/me': { body: me }, [`GET ${READ}`]: { body: detail }, ...routes });
  renderWith(<SegmentReport run={RUN_ID} />);
}

/** Части списка — строки верхнего уровня (вложенное «из них» — внутри строки «без адреса»). */
function partsShown(): HTMLElement[] {
  const list = screen.getByRole('list', { name: 'Принятые по частям' });
  return Array.from(list.children) as HTMLElement[];
}

/** Число части — после её названия: «с лидом — 7», «без адреса — 3: …», «возьмёт кнопка — 2». */
function partNumber(item: HTMLElement): number {
  const found = /^[^—]+?\u00a0—\u00a0(\d+)/.exec(item.textContent ?? '');
  return Number(found?.[1]);
}

describe('принятые по частям — словами', () => {
  it('части сходятся с «Принято» — «Из M принятых»; числа и слова — сервера', () => {
    const view = acceptedParts(detailOf(), true, NOW);

    expect(view).toEqual({
      whole: true,
      head: 'Из 12 принятых:',
      parts: [
        { key: 'leads', text: nb('с лидом — 7'), sub: null },
        {
          key: 'no_address',
          text: nb(
            `без адреса — 3: адреса на сайте нет — 1; сайт не ответил — повтор по сроку — 1; ${HELD} — 1`,
          ),
          sub: nb(
            `из них ждут повтора — 1, ближайший срок ещё не настал — повтор сам около ${formatTime(LATER)}`,
          ),
        },
        { key: 'take', text: nb('возьмёт кнопка — 2'), sub: null },
      ],
      manual: MANUAL,
    });
  });

  it('часть «без адреса» берёт и кнопка — без «из M», со словами почему; ближайший срок — ранний', () => {
    const view = acceptedParts(overlapping(), true, NOW);

    expect(view.whole).toBe(false);
    expect(view.head).toBe('Принятые:');
    expect(view.parts.map((part) => part.text)).toEqual([
      nb('с лидом — 4'),
      nb(
        'без адреса — 4: сайт не ответил — повтор по сроку — 3; квота платного поиска — повтор следующим проходом — 1',
      ),
      `${nb('возьмёт кнопка — 4')}, ${OVERLAP}`,
    ]);
    // Строки «Ждут адреса» — все три (и без срока); срок — самый ранний, он уже наступил.
    expect(view.parts[1]?.sub).toBe(
      nb(`из них ждут повтора — 4, ближайший срок повтора ${formatTime(PASSED)} наступил`),
    );
  });

  it('без права «run» — «продолжение прогона», а не «кнопка»', () => {
    const view = acceptedParts(detailOf(), false, NOW);

    expect(view.parts.at(-1)?.text).toBe(nb('возьмёт продолжение прогона — 2'));
    expect(view.manual).toBe(
      'Сайты, принятые вручную после прогона, ищет продолжение прогона: поиск адреса платный, его запускает человек.',
    );
  });

  it('нулевых частей нет; у сервера без строк ожидания «из них» нет', () => {
    const fresh = detailOf({
      summary: { hosts: 5, accepted: 5, review: 0, rejected: 0, no_address: 0, leads: 0 },
      no_address: [],
      waiting_address: 5,
    });
    delete fresh.waits;
    expect(acceptedParts(fresh, true, NOW).parts).toEqual([
      { key: 'take', text: nb('возьмёт кнопка — 5'), sub: null },
    ]);

    const older = detailOf();
    delete older.waits;
    expect(acceptedParts(older, true, NOW).parts[1]?.sub).toBeNull();
  });

  it('частей меньше «Принято» — без «из M» и без объяснения пересечением: расхождение видно', () => {
    const view = acceptedParts(detailOf({ waiting_address: 1 }), true, NOW);

    expect(view.head).toBe('Принятые:');
    expect(view.parts.at(-1)?.text).toBe(nb('возьмёт кнопка — 1'));
  });
});

describe('расшифровка под кнопкой в отчёте прогона', () => {
  it('готовый прогон: под кнопкой — части числами сервера, сумма сходится с плиткой «Принято»', async () => {
    openReport(detailOf());

    const button = await screen.findByRole(
      'button',
      { name: 'Найти адреса ещё 2 из 12 принятых' },
      WAIT,
    );
    const head = screen.getByText('Из 12 принятых:');
    // Под кнопкой — следом за ней по документу.
    expect(button.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const parts = partsShown();
    const retry = nb(
      `из них ждут повтора — 1, ближайший срок ещё не настал — повтор сам около ${formatTime(LATER)}`,
    );
    // Строка «без адреса» несёт и вложенное «из них» — текстом следом за причинами.
    expect(parts.map((item) => item.textContent)).toEqual([
      nb('с лидом — 7'),
      nb(
        `без адреса — 3: адреса на сайте нет — 1; сайт не ответил — повтор по сроку — 1; ${HELD} — 1`,
      ) + retry,
      nb('возьмёт кнопка — 2'),
    ]);
    const tile = screen.getByRole('link', { name: /^Принято/ });
    const accepted = Number(within(tile).getByText(/^\d+$/).textContent);
    expect(accepted).toBe(12);
    expect(parts.map(partNumber).reduce((sum, count) => sum + count, 0)).toBe(accepted);
    // `toHaveTextContent` сводит неразрывный пробел к обычному — сверяется сам текст.
    expect(
      within(screen.getByRole('list', { name: 'Без адреса — ждут повтора' })).getByRole('listitem')
        .textContent,
    ).toBe(retry);
  });

  it('строка о принятых вручную — словами', async () => {
    openReport(detailOf());

    expect(await screen.findByText(MANUAL, {}, WAIT)).toBeInTheDocument();
  });

  it('нет кнопки — нет расшифровки: прогон идёт; у принятых всё найдено', async () => {
    openReport(detailOf({ run: { ...run, active: true, finished_at: null, stage: 'contacts' } }));
    await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
    expect(screen.queryByRole('button', { name: /^Найти адреса ещё/ })).toBeNull();
    expect(screen.queryByRole('list', { name: 'Принятые по частям' })).toBeNull();
    expect(screen.queryByText(MANUAL)).toBeNull();
  });

  it('у принятых всё найдено (ждать нечего) — ни кнопки, ни расшифровки', async () => {
    openReport(
      detailOf({
        summary: { hosts: 20, accepted: 10, review: 0, rejected: 8, no_address: 3, leads: 7 },
        waiting_address: 0,
      }),
    );
    await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
    expect(screen.queryByRole('button', { name: /^Найти адреса ещё/ })).toBeNull();
    expect(screen.queryByRole('list', { name: 'Принятые по частям' })).toBeNull();
  });

  it('остановленный прогон — «Продолжить прогон», как было, без расшифровки', async () => {
    openReport(detailOf({ run: { ...run, stage: 'stopped', finished_at: null } }));

    expect(
      await screen.findByRole('button', { name: 'Продолжить прогон' }, WAIT),
    ).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Принятые по частям' })).toBeNull();
  });

  it('без права «run» — расшифровка видна, кнопки нет, сказано, кто продолжает', async () => {
    const seller: Me = {
      ...ADMIN,
      email: 'seller@ours.example.test',
      permissions: ['sales', 'view'],
    };
    openReport(detailOf(), seller);

    expect(
      await screen.findByText(
        `Продолжает прогон сотрудник с правом «${PERMISSION_TITLES.run}».`,
        {},
        WAIT,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Найти адреса ещё/ })).toBeNull();
    expect(partsShown().map((item) => item.textContent)).toContain(
      nb('возьмёт продолжение прогона — 2'),
    );
    expect(screen.getByText('Из 12 принятых:')).toBeInTheDocument();
  });

  it('отказ сервера — сразу под кнопкой, над расшифровкой: ответ на нажатие рядом с ним', async () => {
    const words = 'ключ модели не принят — проверьте ключ';
    openReport(detailOf(), ADMIN, {
      [`POST ${READ}/resume`]: { status: 409, body: { detail: words } },
    });

    await userEvent.click(
      await screen.findByRole('button', { name: 'Найти адреса ещё 2 из 12 принятых' }, WAIT),
    );

    const refusal = await screen.findByText(words, {}, WAIT);
    expect(screen.getByText('Прогон не продолжен')).toBeInTheDocument();
    const head = screen.getByText('Из 12 принятых:');
    expect(refusal.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
