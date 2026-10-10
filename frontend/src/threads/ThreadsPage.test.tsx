/**
 * Список диалогов: что показано и чем фильтруется.
 *
 * Отдельная проверка на то, что строка — это донор, а не адрес: у сайта
 * их несколько, и список по адресам превращает восемь доноров в двадцать
 * строк, по которым непонятно, с кем уже договорились.
 *
 * **Фильтр состояния живёт в адресе** (`?state=needs_review`): по нему
 * ведут плитки главной. Проверяется в обе стороны — адрес сужает список,
 * смена фильтра на экране пишет адрес, — и незнакомое значение, с которым
 * адрес приходит устаревшим.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const THREADS = [
  {
    id: 1,
    host: 'digest-weekly.example.test',
    contact_email: 'editor@digest-weekly.example.test',
    campaign: 'Демонстрация',
    stage: 'donors',
    state: 'priced',
    messages_sent: 1,
    last_event_at: '2026-09-19T10:00:00+00:00',
    last_reply_at: '2026-09-19T10:00:00+00:00',
    price_white: '1250.00',
    price_grey: '180.00',
    currency: 'EUR',
  },
  {
    id: 2,
    host: 'green-blog.example.test',
    contact_email: 'hello@green-blog.example.test',
    campaign: 'Демонстрация',
    stage: 'donors',
    state: 'waiting',
    messages_sent: 1,
    last_event_at: '2026-09-18T10:00:00+00:00',
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  },
  {
    id: 3,
    host: 'tech-review.example.test',
    contact_email: 'ads@tech-review.example.test',
    campaign: 'Демонстрация',
    stage: 'donors',
    state: 'needs_review',
    messages_sent: 1,
    last_event_at: '2026-09-17T10:00:00+00:00',
    last_reply_at: '2026-09-17T10:00:00+00:00',
    price_white: null,
    price_grey: '90.00',
    currency: 'USDT',
  },
];

const CALIBRATION = {
  versions: [
    {
      version: 'reply-parse-v4-named-price',
      reviewed: 10,
      as_is: 7,
      edited: 3,
      wrong: { price_white: 2, price_grey: 0, currency: 1, placement: 0 },
      auto_stored: 12,
      waiting: 4,
    },
  ],
};

/** Непривязанных ответов нет: экран спрашивает их число и на вкладке диалогов. */
const NO_UNBOUND = { rows: [], total: 0, page: 1, limit: 20 };

/** Где сейчас экран: адрес целиком, с фильтром. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

async function openThreads(
  path = '/threads',
  ready = 'digest-weekly.example.test',
  extra: Parameters<typeof serve>[0] = {},
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: THREADS },
    'GET /api/replies/calibration': { body: CALIBRATION },
    'GET /api/replies/unbound?page=1': { body: NO_UNBOUND },
    ...extra,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  await screen.findByText(ready);
  return recorded;
}

async function chooseState(option: string) {
  const user = userEvent.setup();
  // Выпадающий список Mantine в jsdom остаётся `display: none` — раскладки
  // здесь нет, и без `hidden` его пункты не видны запросу. Клик по пункту
  // при этом настоящий, и проверяется именно он.
  await user.click(screen.getByRole('textbox', { name: 'Состояние' }));
  await user.click(await screen.findByRole('option', { name: option, hidden: true }));
}

/** Строки списка — их сайты, сверху вниз. */
function hosts(): string[] {
  return within(screen.getByRole('navigation', { name: 'Список диалогов' }))
    .queryAllByRole('link')
    .map((row) => row.querySelector('.threadRowHost')?.textContent ?? '');
}

/** Строка списка — ссылка на переписку — по сайту. */
function rowOf(host: string): HTMLElement {
  return screen.getByText(host).closest('a')!;
}

describe('диалоги', () => {
  it('показывает состояние и цену — деньгами, а не сырой строкой сервера', async () => {
    await openThreads();

    const row = rowOf('digest-weekly.example.test');
    expect(within(row).getByText('цена получена')).toBeInTheDocument();
    // Разряды, запятая, знак валюты — и какая из цен какая.
    expect(within(row).getByText('белая 1 250,00 € · серая 180,00 €')).toBeInTheDocument();
    expect(screen.queryByText(/1250\.00|180\.00/)).not.toBeInTheDocument();
  });

  it('одна серая цена так и названа, а валюта без знака — кодом', async () => {
    await openThreads();

    const row = rowOf('tech-review.example.test');
    expect(within(row).getByText('серая 90,00 USDT')).toBeInTheDocument();
    expect(within(row).queryByText(/белая/)).not.toBeInTheDocument();
  });

  it('фильтр состояния сужает список и пишет себя в адрес', async () => {
    await openThreads();

    await chooseState('ждём ответа · 1');

    expect(hosts()).toEqual(['green-blog.example.test']);
    expect(screen.getByTestId('where')).toHaveTextContent('/threads?state=waiting');
    expect(screen.getByText('найдено 1 из 3')).toBeInTheDocument();
  });

  it('фильтр из адреса: плитка главной ведёт на диалоги, ждущие разбора', async () => {
    await openThreads('/threads?state=needs_review', 'tech-review.example.test');

    expect(hosts()).toEqual(['tech-review.example.test']);
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('ждёт разбора · 1');
  });

  it('незнакомое состояние в адресе — не сужать, а не пустой экран', async () => {
    await openThreads('/threads?state=no_such_state');

    expect(hosts()).toHaveLength(3);
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('все · 3');
  });

  it('фильтр из адреса без таких диалогов объясняет, что их нет вовсе', async () => {
    await openThreads('/threads?state=lead', 'В состоянии «лид — ждёт человека» диалогов нет.');

    expect(screen.getByText(/Всего диалогов — 3/)).toBeInTheDocument();
    // Выбранное состояние стоит в фильтре, даже когда его нет в списке.
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue(
      'лид — ждёт человека · 0',
    );
  });

  it('поиск идёт и по донору, и по адресу, и уходит в адрес экрана', async () => {
    await openThreads();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'hello@');

    expect(hosts()).toEqual(['green-blog.example.test']);
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/threads?search=hello%40'),
    );
  });

  it('пустой результат называет условия и что база не пуста', async () => {
    await openThreads();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'такого нет');

    expect(screen.getByText('Под фильтр ничего не попало.')).toBeInTheDocument();
    expect(screen.getByText(/«такого нет» в доноре или адресе. Всего диалогов — 3/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));
    expect(hosts()).toHaveLength(3);
  });

  it('из отфильтрованного списка в диалог и «К списку» — обратно с фильтром', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads': { body: THREADS },
      'GET /api/replies/calibration': { body: CALIBRATION },
      'GET /api/replies/unbound?page=1': { body: NO_UNBOUND },
      'GET /api/threads/2': {
        body: {
          card: THREADS[1],
          letters: [],
          incoming: [],
          corridor: { min: 0.15, max: 0.25 },
        },
      },
    });
    renderWith(
      <>
        <AppRoutes />
        <Where />
      </>,
      '/threads?state=waiting',
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: /green-blog\.example\.test/ }));
    await user.click(await screen.findByRole('link', { name: 'К списку' }));

    expect(await screen.findByText('green-blog.example.test')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/threads?state=waiting');
  });

  it('с «Не привязаны» обратно на «Диалоги» — с тем же фильтром, а не с начала', async () => {
    // Проверка QA 10.10.2026: вкладка «Диалоги» вела в начало списка без фильтра.
    await openThreads('/threads?state=priced');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('radio', { name: /^Не привязаны/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/threads?tab=unbound');
    await user.click(screen.getByRole('radio', { name: /^Диалоги/ }));

    expect(await screen.findByText('digest-weekly.example.test')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/threads?state=priced');
  });

  it('калибровка разбора — в подсказке «i» у заголовка', async () => {
    // Какие поля человек правит чаще — тем и занимается следующая версия
    // промпта. Приём соседней системы, работавший в бою. С 09.10.2026 — в «i»
    // рядом с числом диалогов, а не строкой над таблицей (аудит экранов).
    await openThreads();
    const user = userEvent.setup();

    await user.hover(
      await screen.findByRole('button', { name: 'Как считается состояние диалога' }),
    );

    const line = await screen.findByText(
      /подтвердил как есть 7 из 10, поправил 3 — чаще всего белая цена 2, валюта 1/,
    );
    // Версия промпта — номером, а не кодом сервера (замечание 06.10.2026).
    expect(line).toHaveTextContent(/^Разбор ответов \(версия 4\):/);
    expect(screen.queryByText(/reply-parse/)).not.toBeInTheDocument();
  });
});

describe('выгрузка лидов', () => {
  const clicked: HTMLAnchorElement[] = [];

  beforeEach(() => {
    clicked.length = 0;
    // В jsdom ссылок на объекты нет: подставляем их, как в выгрузке доноров.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:leads'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(URL, 'createObjectURL');
    Reflect.deleteProperty(URL, 'revokeObjectURL');
  });

  it('без права prices кнопки выгрузки лидов нет — файл отдаётся только под ним', async () => {
    await openThreads('/threads', 'digest-weekly.example.test', {
      'GET /api/auth/me': { body: { ...OPERATOR, permissions: ['view'] } },
    });

    expect(screen.queryByRole('button', { name: 'Выгрузить лиды' })).not.toBeInTheDocument();
  });

  it('лиды уходят файлом с пропуском — те же поля, что в вебхуке CRM', async () => {
    const recorded = await openThreads('/threads', 'digest-weekly.example.test', {
      'GET /api/replies/leads.csv': {
        raw: 'lead_id,advertiser\r\n21,brand.test\r\n',
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'content-disposition': 'attachment; filename="leads-2026-10-04.csv"',
          'x-export-rows': '1',
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Выгрузить лиды' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    const call = recorded.calls.find((sent) => sent.path === '/api/replies/leads.csv');
    expect(call?.token).toBe('Bearer пропуск');
    expect(clicked[0]?.download).toBe('leads-2026-10-04.csv');
    expect(await screen.findByText('Выгружено лидов: 1')).toBeInTheDocument();
  });
});
