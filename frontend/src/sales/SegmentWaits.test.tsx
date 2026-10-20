/**
 * Что в прогоне ждёт повтора (разбор прогона сбора 20.10.2026): строки «ждут адреса» и «ждут
 * очистки» — почему словами, сколько, когда повтор пойдёт сам (часами человека, прошёл —
 * «наступил»), и «Повторить сейчас» у группы: запрос с видом повтора, отказ сервера — словами;
 * кнопки нет, пока прогон идёт, где повторять нечего и у учётки без права «run». Ответы сервера —
 * записанные (`serve()`); слова — разных ниш и языков.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Me } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatTime } from '../format';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';
import type { SegmentRunDetail, SegmentRunRow, SegmentWait } from './segmentApi';
import { SegmentWaits, termWords, waitLine } from './SegmentWaits';

const WAIT = { timeout: 5000 };
const RUN_ID = 44;
const AGAIN = `/api/sales/segment/runs/${RUN_ID}/again`;
/** «Сейчас» чистых функций — передаётся им явно, часы не подменяются. */
const NOW = new Date('2026-10-20T09:00:00+00:00');

const run: SegmentRunRow = {
  id: RUN_ID,
  hypothesis_id: 7,
  created_at: '2026-10-20T07:00:00+00:00',
  finished_at: '2026-10-20T07:40:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'tr',
  language: 'en',
  depth_pages: 1,
  keywords: 3,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: null,
  alive_at: '2026-10-20T07:40:00+00:00',
  active: false,
  progress: null,
  watch: null,
};

function inHours(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

/** Строки ожидания отчёта: сайт не ответил, Hunter ещё проверяет, первый unknown ждёт своего дня. */
const SILENT: SegmentWait = {
  kind: 'address',
  words: 'сайт не ответил поиску адреса',
  count: 3,
  at: inHours(1),
  again: true,
};
const CHECKING: SegmentWait = {
  kind: 'cleaning',
  words: 'Hunter ещё проверяет адрес',
  count: 2,
  at: inHours(0.3),
  again: true,
};
const UNKNOWN: SegmentWait = {
  kind: 'cleaning',
  words: 'адрес не подтверждён (unknown) — перепроверка не раньше 27.10.2026',
  count: 1,
  at: null,
  again: false,
};
const WAITS: SegmentWait[] = [SILENT, CHECKING, UNKNOWN];

function detailOf(changes: Partial<SegmentRunDetail> = {}): SegmentRunDetail {
  return {
    run,
    hypothesis: 'kitabevleri TR',
    keywords: ['ikinci el kitap'],
    estimate_lines: [],
    summary: { hosts: 9, accepted: 6, review: 0, rejected: 3, no_address: 3, leads: 3 },
    lanes: { pending: 0, accept: 6, review: 0, reject: 3 },
    rejected: [],
    no_address: [{ words: 'сайт не ответил — повтор по сроку', count: 3 }],
    waiting_address: 0,
    leads: { new: 3, ready: 0, rejected: 0 },
    lead_reasons: {},
    unanswered: 0,
    report: {},
    waits: WAITS,
    ...changes,
  };
}

/** Кто вошёл — когда учётка прочитана: до этого права ещё неизвестны. */
function Who() {
  const { user } = useSession();
  return <output>{user?.email ?? ''}</output>;
}

function open(detail: SegmentRunDetail, me: Me = ADMIN, routes: Record<string, Answer> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({ 'GET /api/auth/me': { body: me }, ...routes });
  renderWith(<SegmentWaits detail={detail} />);
  return recorded;
}

describe('срок словами', () => {
  it('сегодня — время, другой день — дата и время, прошёл — «наступил»', () => {
    const soon = '2026-10-20T09:35:00+00:00';
    const tomorrow = '2026-10-21T09:35:00+00:00';
    const past = '2026-10-20T08:50:00+00:00';

    expect(termWords(soon, NOW)).toBe(`повтор около ${formatTime(soon)}`);
    expect(termWords(tomorrow, NOW)).toBe(`повтор около ${formatDateTime(tomorrow)}`);
    expect(termWords(past, NOW)).toBe(`срок повтора ${formatTime(past)} наступил`);
  });

  it('строка — почему, сколько и срок; без срока — без него', () => {
    const at = '2026-10-20T09:20:00+00:00';
    expect(waitLine({ ...CHECKING, at }, NOW)).toBe(
      `Hunter ещё проверяет адрес\u00a0—\u00a02, повтор около ${formatTime(at)}`,
    );
    expect(waitLine(UNKNOWN, NOW)).toBe(
      'адрес не подтверждён (unknown) — перепроверка не раньше 27.10.2026\u00a0—\u00a01',
    );
  });
});

describe('строки ожидания в отчёте прогона', () => {
  it('группы с числом, причины и срок; «Повторить сейчас» у каждой группы, где повтор возможен', async () => {
    open(detailOf());

    expect(await screen.findByText('Ждут адреса — 3')).toBeInTheDocument();
    expect(screen.getByText('Ждут очистки — 3')).toBeInTheDocument();
    const cleaning = screen.getByRole('list', { name: 'Ждут очистки' });
    expect(
      within(cleaning)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringMatching(/^Hunter ещё проверяет адрес\u00a0—\u00a02, повтор около /),
      'адрес не подтверждён (unknown) — перепроверка не раньше 27.10.2026\u00a0—\u00a01',
    ]);
    expect(await screen.findAllByRole('button', { name: 'Повторить сейчас' }, WAIT)).toHaveLength(
      2,
    );
  });

  it('«Повторить сейчас» уходит на сервер своим видом повтора', async () => {
    const recorded = open(detailOf(), ADMIN, {
      [`POST ${AGAIN}`]: {
        status: 202,
        body: { run_id: RUN_ID, job_id: `sales-segment-${RUN_ID}` },
      },
      [`GET /api/jobs/sales-segment-${RUN_ID}`]: { body: {} },
    });
    const buttons = await screen.findAllByRole('button', { name: 'Повторить сейчас' }, WAIT);

    // По порядку отчёта: сначала адреса, затем очистка.
    for (const button of buttons) await userEvent.click(button);

    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => [call.path, call.body])).toEqual([
      [AGAIN, { what: 'addresses' }],
      [AGAIN, { what: 'cleaning' }],
    ]);
  });

  it('отказ сервера — его словами под группой', async () => {
    const words = 'Повторять нечего: сайтов, которые ждут повтора поиска адреса, в прогоне нет';
    open(detailOf({ waits: [SILENT] }), ADMIN, {
      [`POST ${AGAIN}`]: { status: 409, body: { detail: words } },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Повторить сейчас' }, WAIT));

    expect(await screen.findByText(words, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText('Повтор не поставлен')).toBeInTheDocument();
  });

  it('пока прогон идёт — кнопки нет, слова есть', async () => {
    // Рядом — тот же отчёт не идущего прогона: его кнопка — знак, что вход уже прочитан, и
    // отсутствие кнопки у идущего — правило, а не «учётка ещё не загрузилась».
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN } });
    renderWith(
      <>
        <section aria-label="идёт">
          <SegmentWaits detail={detailOf({ run: { ...run, active: true, stage: 'contacts' } })} />
        </section>
        <section aria-label="готов">
          <SegmentWaits detail={detailOf({ waits: [SILENT] })} />
        </section>
      </>,
    );
    const idle = screen.getByRole('region', { name: 'готов' });
    const going = screen.getByRole('region', { name: 'идёт' });

    await within(idle).findByRole('button', { name: 'Повторить сейчас' }, WAIT);
    expect(within(going).getByText('Ждут адреса — 3')).toBeInTheDocument();
    expect(within(going).queryByRole('button', { name: 'Повторить сейчас' })).toBeNull();
  });

  it('где повторять нечего — кнопки нет: первый unknown ждёт своего дня', async () => {
    open(detailOf({ waits: [SILENT, UNKNOWN] }));

    // Кнопка у адресов — знак, что вход прочитан; у очистки с одним unknown её нет.
    const [button] = await screen.findAllByRole('button', { name: 'Повторить сейчас' }, WAIT);
    expect(screen.getAllByRole('button', { name: 'Повторить сейчас' })).toHaveLength(1);
    expect(screen.getByText('Ждут адреса — 3').parentElement).toContainElement(button ?? null);
    expect(screen.getByText('Ждут очистки — 1').parentElement).not.toContainElement(button ?? null);
  });

  it('без права «run» — слова вместо кнопки', async () => {
    const seller: Me = {
      ...ADMIN,
      email: 'seller@ours.example.test',
      permissions: ['sales', 'view'],
    };
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: seller } });
    renderWith(
      <>
        <Who />
        <SegmentWaits detail={detailOf({ waits: [SILENT] })} />
      </>,
    );

    // Учётка прочитана — значит, слова стоят по праву, а не потому, что вход ещё грузится.
    await screen.findByText(seller.email, {}, WAIT);
    expect(screen.getByText(/^Повторяет сотрудник с правом «/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Повторить сейчас' })).not.toBeInTheDocument();
  });

  it('ждать нечего — блока нет', () => {
    open(detailOf({ waits: [] }));
    expect(screen.queryByText(/^Ждут /)).not.toBeInTheDocument();
  });

  it('сервер старше строк ожидания — блока нет', () => {
    const older = detailOf();
    delete older.waits;
    open(older);
    expect(screen.queryByText(/^Ждут /)).not.toBeInTheDocument();
  });
});
