/**
 * «Диалоги» доноров и рекламодателей — только переписки с ответом (ответ заказчика по задаче №2,
 * 16.11.2026). Отвечена ли переписка, говорит сервер (`ThreadCard.answered`), экран не
 * пересчитывает: здесь у отвеченной и у неотвеченной бывает одно и то же состояние.
 *
 * Проверяется: в частях доноров и рекламодателей неотвеченные (`answered === false`) скрыты, а
 * числа считают показанные; поля нет — строка видна, как раньше; у продаж видны все; в общем списке
 * скрыты только неотвеченные доноров и рекламодателей; список, где ответов ещё нет, так и говорит;
 * прямая ссылка открывает и неотвеченную переписку — и на узком окне, и рядом со списком, где её
 * строки нет. Данные — выдуманные, на `*.example.test`.
 */

import { screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { ThreadCard, ThreadState } from '../api/types';
import { SPLIT_QUERY } from '../layout/split';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { inDialogs } from './threadAnswered';

/** Короткая дата строки зависит от «сегодня» (`shortWhen`): часы закреплены. */
const NOW = new Date('2026-11-16T12:00:00+03:00');

/** Слово в имени сайта: с ответом, без ответа или сервер поля не дал. */
function mark(answered: boolean | undefined): string {
  if (answered === undefined) return 'unmarked';
  return answered ? 'answered' : 'silent';
}

/** Карточка списка; `answered` не назван — поля в карточке нет вовсе, как у сервера до поля. */
function thread(
  id: number,
  stage: ThreadCard['stage'],
  state: ThreadState,
  answered?: boolean,
): ThreadCard {
  const host = `${stage}-${mark(answered)}-${id}.example.test`;
  const card: ThreadCard = {
    id,
    host,
    contact_email: `editor@${host}`,
    campaign: 'Проверка',
    stage,
    state,
    messages_sent: 1,
    last_event_at: '2026-11-16T08:00:00+00:00',
    last_reply_at: answered === false ? null : '2026-11-16T08:00:00+00:00',
    price_white: null,
    price_grey: null,
    currency: null,
  };
  return answered === undefined ? card : { ...card, answered };
}

/** У доноров «ждём ответа» — и у отвеченной (мы ответили и ждём), и у той, где ответа не было:
 *  делит их только поле сервера. */
const DONOR = thread(1, 'donors', 'priced', true);
const DONOR_REPLIED = thread(3, 'donors', 'waiting', true);
const DONOR_SILENT = thread(2, 'donors', 'waiting', false);
const DONOR_BOUNCED = thread(4, 'donors', 'bounced', false);
const BRAND = thread(5, 'advertisers', 'lead', true);
const BRAND_SILENT = thread(6, 'advertisers', 'unsubscribed', false);
const LEAD = thread(7, 'sales', 'sales_pending', true);
const LEAD_SILENT = thread(8, 'sales', 'waiting', false);
/** Сервер поля не дал — не судим и не прячем. */
const DONOR_UNMARKED = thread(9, 'donors', 'waiting');

const THREADS = [
  DONOR,
  DONOR_SILENT,
  DONOR_REPLIED,
  DONOR_BOUNCED,
  BRAND,
  BRAND_SILENT,
  LEAD,
  LEAD_SILENT,
];

function open(path: string, threads = THREADS) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: threads },
    ...Object.fromEntries(
      threads.map((card) => [
        `GET /api/threads/${card.id}`,
        { body: { card, letters: [], incoming: [], corridor: { min: 0.15, max: 0.25 } } },
      ]),
    ),
    'GET /api/replies/calibration': { body: { versions: [] } },
    'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
  });
  renderWith(<AppRoutes />, path);
}

/** Список по разметке, а не по роли: так быстрее, и строки — ссылки на переписки в нём. */
const list = () =>
  waitFor(() => {
    const found = document.querySelector<HTMLElement>(
      '[role="navigation"][aria-label="Список диалогов"]',
    );
    expect(found).not.toBeNull();
    expect(found!.querySelector('[data-thread]')).not.toBeNull();
    return found!;
  });

/** Номера переписок в строках списка — по возрастанию: порядок строк проверяют другие тесты. */
async function rows(): Promise<number[]> {
  const found = await list();
  return [...found.querySelectorAll<HTMLElement>('[data-thread]')]
    .map((row) => Number(row.dataset.thread))
    .sort((a, b) => a - b);
}

/** Широкое окно: список — колонкой рядом с перепиской. */
function wide() {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: query === SPLIT_QUERY,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

describe('строка «Диалогов» — по полю сервера', () => {
  it('у продаж — любая переписка, у доноров и рекламодателей — кроме неотвеченных', () => {
    expect([...THREADS, DONOR_UNMARKED].map((card) => [card.id, inDialogs(card)])).toEqual([
      [1, true],
      [2, false],
      [3, true],
      [4, false],
      [5, true],
      [6, false],
      [7, true],
      [8, true],
      [9, true],
    ]);
    expect('answered' in DONOR_UNMARKED).toBe(false);
  });
});

describe('«Диалоги» — только переписки с ответом', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('доноры: неотвеченные скрыты, «всего» и вкладка считают показанные', async () => {
    open('/threads?stage=donors');

    expect(await rows()).toEqual([1, 3]);
    expect(screen.getByText('всего 2')).toBeInTheDocument();
    expect(screen.getByText('Диалоги — 2')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('все · 2');
  });

  it('рекламодатели: неотвеченная скрыта, числа — по показанным', async () => {
    open('/threads?stage=advertisers');

    expect(await rows()).toEqual([5]);
    expect(screen.getByText('всего 1')).toBeInTheDocument();
    expect(screen.getByText('Диалоги — 1')).toBeInTheDocument();
  });

  it('продажи: видны все переписки, и без ответа тоже', async () => {
    open('/threads?stage=sales');

    expect(await rows()).toEqual([7, 8]);
    expect(screen.getByText('всего 2')).toBeInTheDocument();
  });

  it('общий список: доноры и рекламодатели вместе — скрыты только их неотвеченные', async () => {
    open('/threads');

    expect(await rows()).toEqual([1, 3, 5]);
    expect(screen.getByText('всего 3')).toBeInTheDocument();
  });

  it('поля нет — сервер не судил: строка видна, как раньше', async () => {
    open('/threads?stage=donors', [DONOR, DONOR_SILENT, DONOR_UNMARKED]);

    expect(await rows()).toEqual([1, 9]);
    expect(screen.getByText('всего 2')).toBeInTheDocument();
  });

  it('ответов ещё нет — так и сказано, а не «диалогов ещё нет»', async () => {
    open('/threads?stage=donors', [DONOR_SILENT, DONOR_BOUNCED, BRAND]);

    expect(await screen.findByText('Ответов ещё нет.')).toBeInTheDocument();
    expect(screen.getByText('Диалог появится здесь с первым ответом.')).toBeInTheDocument();
    expect(screen.queryByText('Диалогов ещё нет.')).toBeNull();
    expect(screen.getByText('всего 0')).toBeInTheDocument();
  });
});

describe('прямая ссылка на переписку без ответа', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('узкое окно: переписка открывается', async () => {
    open(`/threads/${DONOR_SILENT.id}`);

    expect(await screen.findByRole('heading', { name: DONOR_SILENT.host })).toBeInTheDocument();
  });

  it('рядом со списком: переписка открыта справа, а строки её в списке нет', async () => {
    wide();
    open(`/threads/${DONOR_SILENT.id}?stage=donors`);

    expect(await screen.findByRole('heading', { name: DONOR_SILENT.host })).toBeInTheDocument();
    expect(await rows()).toEqual([1, 3]);
    expect(document.querySelector('[aria-current="page"][data-thread]')).toBeNull();
  });
});
