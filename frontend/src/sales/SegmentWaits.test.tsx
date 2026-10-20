/**
 * Что в прогоне ждёт повтора (разбор прогона сбора 20.10.2026): строки «ждут адреса» и «ждут
 * очистки» — почему словами, сколько, срок (часами человека: впереди — «ещё не настал», прошёл —
 * «наступил»), и «Повторить сейчас» у группы — только где сервер предложил повтор (`again`): что
 * он возьмёт и до скольких платных запросов, платный — через окно подтверждения. Кнопки нет, пока
 * прогон идёт, где повторять нечего и у учётки без права «run»; после нажатия — отчёт
 * перечитывается, и идущий повтор кнопки не даёт. Ответы сервера — записанные (`serve()`); слова —
 * разных ниш и языков.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Me } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatTime } from '../format';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';
import type { SegmentOffer, SegmentRunDetail, SegmentRunRow, SegmentWait } from './segmentApi';
import { useRun } from './segmentData';
import { offerWords, SegmentWaits, waitLine } from './SegmentWaits';
import { momentWords, termWords } from './termWords';

const WAIT = { timeout: 5000 };
const RUN_ID = 44;
const AGAIN = `/api/sales/segment/runs/${RUN_ID}/again`;
const READ = `/api/sales/segment/runs/${RUN_ID}`;
const JOB = `/api/jobs/sales-segment-${RUN_ID}`;
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

/** Строки ожидания отчёта: сайт не ответил (срок прошёл и впереди), Hunter ещё проверяет, первый
 *  unknown ждёт своего дня. */
const SILENT_DUE: SegmentWait = {
  kind: 'address',
  words: 'сайт не ответил поиску адреса',
  count: 2,
  at: inHours(-0.5),
  again: true,
};
const SILENT_LATER: SegmentWait = { ...SILENT_DUE, count: 1, at: inHours(20), again: false };
const CHECKING: SegmentWait = {
  kind: 'cleaning',
  words: 'Hunter ещё проверяет адрес',
  count: 2,
  at: inHours(-0.2),
  again: true,
};
const UNKNOWN: SegmentWait = {
  kind: 'cleaning',
  words: 'адрес не подтверждён (unknown) — перепроверка не раньше 27.10.2026',
  count: 1,
  at: null,
  again: false,
};
const WAITS: SegmentWait[] = [SILENT_DUE, SILENT_LATER, CHECKING, UNKNOWN];
/** Что предложил сервер: поиск двум сайтам без платной ступени, перепроверку двух лидов. */
const FREE_SEARCH: SegmentOffer = { what: 'addresses', count: 2, searches: 0, checks: 0 };
const PAID_CHECK: SegmentOffer = { what: 'cleaning', count: 2, searches: 0, checks: 2 };
const OFFERS: SegmentOffer[] = [FREE_SEARCH, PAID_CHECK];

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
    again: OFFERS,
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

const QUEUED: Record<string, Answer> = {
  [`POST ${AGAIN}`]: { status: 202, body: { run_id: RUN_ID, job_id: `sales-segment-${RUN_ID}` } },
  [`GET ${JOB}`]: { body: {} },
};

describe('срок и предложение словами', () => {
  it('сегодня — время, другой день — дата и время; впереди — «ещё не настал», прошёл — «наступил»', () => {
    const soon = '2026-10-20T09:35:00+00:00';
    const tomorrow = '2026-10-21T09:35:00+00:00';
    const past = '2026-10-20T08:50:00+00:00';

    expect(momentWords(tomorrow, NOW)).toBe(formatDateTime(tomorrow));
    expect(termWords(soon, NOW)).toBe(`срок ещё не настал — повтор сам около ${formatTime(soon)}`);
    expect(termWords(tomorrow, NOW)).toBe(
      `срок ещё не настал — повтор сам около ${formatDateTime(tomorrow)}`,
    );
    expect(termWords(past, NOW)).toBe(`срок повтора ${formatTime(past)} наступил`);
  });

  it('строка — почему, сколько и срок; без срока — без него', () => {
    const at = '2026-10-20T08:20:00+00:00';
    expect(waitLine({ ...CHECKING, at }, NOW)).toBe(
      `Hunter ещё проверяет адрес\u00a0—\u00a02, срок повтора ${formatTime(at)} наступил`,
    );
    expect(waitLine(UNKNOWN, NOW)).toBe(
      'адрес не подтверждён (unknown) — перепроверка не раньше 27.10.2026\u00a0—\u00a01',
    );
  });

  it('что возьмёт кнопка — верхней границей, поисками и проверками', () => {
    expect(offerWords(FREE_SEARCH)).toBe('поищет до 2 сайтов, без платных запросов');
    expect(offerWords(PAID_CHECK)).toBe('перепроверит до 2 лидов, до 2 проверок Hunter');
    // Найденный адрес становится лидом, и его проверка тоже платная — названа рядом с поиском.
    expect(offerWords({ what: 'addresses', count: 3, searches: 1, checks: 3 })).toBe(
      'поищет до 3 сайтов, до 1 поиска и до 3 проверок Hunter',
    );
    expect(offerWords({ what: 'addresses', count: 1, searches: 0, checks: 1 })).toBe(
      'поищет до 1 сайта, до 1 проверки Hunter',
    );
  });
});

describe('строки ожидания в отчёте прогона', () => {
  it('группы с числом, причины и срок; у каждой группы, где сервер предложил, — кнопка и что возьмёт', async () => {
    open(detailOf());

    expect(await screen.findByText('Ждут адреса — 3')).toBeInTheDocument();
    expect(screen.getByText('Ждут очистки — 3')).toBeInTheDocument();
    const addresses = screen.getByRole('list', { name: 'Ждут адреса' });
    expect(
      within(addresses)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringMatching(
        /^сайт не ответил поиску адреса\u00a0—\u00a02, срок повтора .+ наступил$/,
      ),
      expect.stringMatching(
        /^сайт не ответил поиску адреса\u00a0—\u00a01, срок ещё не настал — повтор сам около /,
      ),
    ]);
    expect(await screen.findAllByRole('button', { name: 'Повторить сейчас' }, WAIT)).toHaveLength(
      2,
    );
    expect(screen.getByText('поищет до 2 сайтов, без платных запросов')).toBeInTheDocument();
    expect(screen.getByText('перепроверит до 2 лидов, до 2 проверок Hunter')).toBeInTheDocument();
  });

  it('бесплатный повтор уходит сразу, платный — после подтверждения у кнопки', async () => {
    const recorded = open(detailOf(), ADMIN, QUEUED);
    const [search, check] = await screen.findAllByRole(
      'button',
      { name: 'Повторить сейчас' },
      WAIT,
    );

    await userEvent.click(search ?? document.body);
    await userEvent.click(check ?? document.body);
    // Выпадающее окно Mantine в jsdom остаётся `display: none` — его ищут с `hidden`.
    const ask = await screen.findByRole('dialog', { name: /^Повторить сейчас\?/, hidden: true });
    expect(ask).toHaveTextContent(
      'Повторить сейчас? Повтор платный: до 2 проверок Hunter — каждый лид — не больше одной проверки.',
    );
    const sentBefore = recorded.calls.filter((call) => call.method === 'POST');
    await userEvent.click(within(ask).getByRole('button', { name: 'Повторить', hidden: true }));

    await waitFor(
      () => expect(recorded.calls.filter((call) => call.method === 'POST')).toHaveLength(2),
      WAIT,
    );
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sentBefore.map((call) => call.body)).toEqual([{ what: 'addresses' }]);
    expect(sent.map((call) => [call.path, call.body])).toEqual([
      [AGAIN, { what: 'addresses' }],
      [AGAIN, { what: 'cleaning' }],
    ]);
  });

  it('«Отмена» у подтверждения — запроса нет', async () => {
    const recorded = open(detailOf({ waits: [CHECKING], again: [PAID_CHECK] }), ADMIN, QUEUED);

    await userEvent.click(await screen.findByRole('button', { name: 'Повторить сейчас' }, WAIT));
    const ask = await screen.findByRole('dialog', { name: /^Повторить сейчас\?/, hidden: true });
    await userEvent.click(within(ask).getByRole('button', { name: 'Отмена', hidden: true }));

    expect(recorded.calls.filter((call) => call.method === 'POST')).toEqual([]);
  });

  it('после нажатия отчёт перечитывается: повтор идёт — кнопки нет, строки есть', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    let posted = false;
    const going = { ...run, active: true, finished_at: null, watch: null };
    serve({
      'GET /api/auth/me': { body: ADMIN },
      [`GET ${READ}`]: () => ({
        body: detailOf({ waits: [SILENT_DUE], again: [FREE_SEARCH], run: posted ? going : run }),
      }),
      [`POST ${AGAIN}`]: () => {
        posted = true;
        return { status: 202, body: { run_id: RUN_ID, job_id: `sales-segment-${RUN_ID}` } };
      },
      [`GET ${JOB}`]: { body: {} },
    });
    function Live() {
      const { data } = useRun(RUN_ID);
      return data === undefined ? null : <SegmentWaits detail={data} />;
    }
    renderWith(<Live />);

    await userEvent.click(await screen.findByRole('button', { name: 'Повторить сейчас' }, WAIT));

    await waitFor(
      () => expect(screen.queryByRole('button', { name: 'Повторить сейчас' })).toBeNull(),
      WAIT,
    );
    expect(screen.getByText('Ждут адреса — 2')).toBeInTheDocument();
    expect(screen.queryByText('поищет до 2 сайтов, без платных запросов')).toBeNull();
  });

  it('отказ сервера — его словами под группой', async () => {
    const words =
      'Повторять нечего: сайтов, чей срок повтора поиска адреса настал, в прогоне нет — у остальных повтор пойдёт сам в свой срок';
    open(detailOf({ waits: [SILENT_DUE], again: [FREE_SEARCH] }), ADMIN, {
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
          <SegmentWaits detail={detailOf({ waits: [SILENT_DUE], again: [FREE_SEARCH] })} />
        </section>
      </>,
    );
    const idle = screen.getByRole('region', { name: 'готов' });
    const busy = screen.getByRole('region', { name: 'идёт' });

    await within(idle).findByRole('button', { name: 'Повторить сейчас' }, WAIT);
    expect(within(busy).getByText('Ждут адреса — 3')).toBeInTheDocument();
    expect(within(busy).queryByRole('button', { name: 'Повторить сейчас' })).toBeNull();
  });

  it('сервер не предложил повтор — кнопки нет: срок впереди, первый unknown ждёт своего дня', async () => {
    open(detailOf({ waits: [SILENT_DUE, SILENT_LATER, UNKNOWN], again: [FREE_SEARCH] }));

    // Кнопка у адресов — знак, что вход прочитан; у очистки предложения нет — и кнопки нет.
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
        <SegmentWaits detail={detailOf({ waits: [SILENT_DUE], again: [FREE_SEARCH] })} />
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

  it('сервер старше строк ожидания — блока нет; старше предложений — кнопок нет', async () => {
    const older = detailOf();
    delete older.waits;
    open(older);
    expect(screen.queryByText(/^Ждут /)).not.toBeInTheDocument();

    const noOffers = detailOf();
    delete noOffers.again;
    renderWith(<SegmentWaits detail={noOffers} />);
    expect(await screen.findByText('Ждут адреса — 3')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Повторить сейчас' })).toBeNull();
  });
});
