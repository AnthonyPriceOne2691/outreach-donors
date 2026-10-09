/**
 * Вкладка «Не привязаны»: счётчик, адрес, раскрытие, пустота и скачивание.
 *
 * До 28.09.2026 непривязанные ответы лежали в базе, и их не видел никто —
 * в том числе ответ на пробное письмо, которым проверяют, что ответы доходят.
 * Поэтому проверяется путь человека: число видно ещё с вкладки диалогов,
 * вкладка открывается щелчком и ссылкой, строка раскрывается в письмо, файл
 * скачивается с пропуском — и содержимым, а не фактом щелчка.
 *
 * Слова причин в записанных ответах — те, что пишет сервер
 * (`backend/features/replies/unbound.py`): в демо кладут то, что сервер
 * отдаёт, а не придуманное рядом.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { ReplyAttachment, ThreadCard, UnboundReply, UnboundView } from '../api/types';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Answer } from '../test/server';
import { serve } from '../test/server';

const THREAD: ThreadCard = {
  id: 1,
  host: 'digest-weekly.example.test',
  contact_email: 'editor@digest-weekly.example.test',
  campaign: 'Проверка',
  stage: 'donors',
  state: 'waiting',
  messages_sent: 1,
  last_event_at: '2026-09-28T10:00:00+00:00',
  last_reply_at: null,
  price_white: null,
  price_grey: null,
  currency: null,
};

const PRICE: ReplyAttachment = {
  id: 11,
  name: 'Прайс 2026.pdf',
  size: 1_572_864,
  content_type: 'application/pdf',
  accepted: true,
  reason: null,
};

const PROBE_REASON =
  'Ответ на пробное письмо: его метка — письмо №0, а писем с таким номером в базе не бывает. ' +
  'Так и проверяется, что ответы доходят: раз ответ здесь, поддомен ответов и приём работают.';

const FOREIGN_REASON =
  'Метки в адресе нет, а заголовки цепочки не совпали ни с одним нашим письмом: ответили ' +
  'на пересланное или чужое письмо. Донора ищут по отправителю и тексту.';

const PROBE_REPLY: UnboundReply = {
  id: 7,
  received_at: '2026-09-28T16:05:00+00:00',
  from_email: 'me@ours.example.test',
  to: ['anna+m0.1a2b3c4d5e@replies.mail-a.example.test'],
  subject: 'Re: Placement on example.org',
  kind: 'human',
  preview: 'Got it, looks fine.',
  text: 'Got it, looks fine.',
  attachments: [],
  reason: 'no_such_letter',
  reason_text: PROBE_REASON,
};

const STRAY_REPLY: UnboundReply = {
  id: 8,
  received_at: '2026-09-28T15:40:00+00:00',
  from_email: 'elena@donor.example.test',
  to: ['team@donor.example.test'],
  subject: 'Rates',
  kind: 'human',
  preview: 'Our rate card is attached.',
  text: 'Our rate card is attached.\n\nBest,\nElena from the ads desk',
  attachments: [PRICE],
  reason: 'foreign_thread',
  reason_text: FOREIGN_REASON,
};

function view(rows: UnboundReply[], total = rows.length, page = 1): UnboundView {
  return { rows, total, page, limit: 20 };
}

/** Где сейчас экран: адрес целиком, со вкладкой и страницей. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

async function openAt(path: string, routes: Record<string, Answer> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: [THREAD] },
    'GET /api/replies/calibration': { body: { versions: [] } },
    'GET /api/replies/unbound?page=1': { body: view([PROBE_REPLY, STRAY_REPLY]) },
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  await screen.findByRole('heading', { name: 'Диалоги' });
  return recorded;
}

/** Строка ответа по адресу отправителя. */
function rowOf(sender: string): HTMLElement {
  const row = screen.getByText(sender).closest('li');
  if (row === null) throw new Error(`строки ответа от ${sender} нет`);
  return row;
}

describe('вкладка «Не привязаны»', () => {
  it('число видно ещё с вкладки диалогов', async () => {
    await openAt('/threads');

    expect(await screen.findByText('Не привязаны — 2')).toBeInTheDocument();
    expect(screen.getByText('Диалоги — 1')).toBeInTheDocument();
    // Вкладка диалогов — тот же список, что и был.
    expect(screen.getByRole('link', { name: /digest-weekly\.example\.test/ })).toBeInTheDocument();
  });

  it('вкладка пишет себя в адрес, и в строке видно, от кого, куда и почему', async () => {
    await openAt('/threads');
    const user = userEvent.setup();

    await user.click(await screen.findByText('Не привязаны — 2'));

    expect(screen.getByTestId('where')).toHaveTextContent('/threads?tab=unbound');
    const row = rowOf('elena@donor.example.test');
    expect(within(row).getByText('на team@donor.example.test')).toBeInTheDocument();
    expect(within(row).getByText('ответ человека')).toBeInTheDocument();
    expect(within(row).getByText('Rates')).toBeInTheDocument();
    expect(within(row).getByText('Our rate card is attached.')).toBeInTheDocument();
    expect(within(row).getByText('вложения · 1')).toBeInTheDocument();
    // Причина — без раскрытия: по ней и решают, что делать с ответом.
    expect(within(row).getByText(FOREIGN_REASON)).toBeInTheDocument();
    expect(within(rowOf('me@ours.example.test')).getByText(PROBE_REASON)).toBeInTheDocument();
    // Таблицы диалогов на этой вкладке нет — её не прячут, её не рисуют.
    expect(screen.queryByRole('link', { name: /digest-weekly\.example\.test/ })).toBeNull();
  });

  it('ссылка с вкладкой в адресе открывает её сразу', async () => {
    await openAt('/threads?tab=unbound');

    expect(await screen.findByText(PROBE_REASON)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Не привязаны — 2' })).toBeChecked();
  });

  it('строка раскрывается в письмо целиком, с вложениями', async () => {
    await openAt('/threads?tab=unbound');
    const user = userEvent.setup();
    await screen.findByText('elena@donor.example.test');
    const row = rowOf('elena@donor.example.test');
    const toggle = within(row).getByRole('button', { name: 'Показать ответ целиком' });

    // Свёрнутое не отрисовано вовсе: ни письма целиком, ни списка файлов.
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(within(row).queryByText(/Elena from the ads desk/)).toBeNull();
    expect(within(row).queryByRole('list', { name: 'Вложения' })).toBeNull();

    await user.click(toggle);

    const open = within(row).getByRole('button', { name: 'Свернуть ответ' });
    expect(open).toHaveAttribute('aria-expanded', 'true');
    expect(document.getElementById(open.getAttribute('aria-controls') ?? '')).not.toBeNull();
    expect(within(row).getByText(/Elena from the ads desk/)).toBeInTheDocument();
    const files = within(row).getByRole('list', { name: 'Вложения' });
    expect(files).toHaveTextContent('Прайс 2026.pdf');
    expect(within(files).getByText('1,5 МБ')).toBeInTheDocument();
  });

  it('щелчок по самой строке тоже раскрывает, второй — сворачивает', async () => {
    await openAt('/threads?tab=unbound');
    const user = userEvent.setup();

    await user.click(await screen.findByText('Rates'));
    const row = rowOf('elena@donor.example.test');
    expect(within(row).getByText(/Elena from the ads desk/)).toBeInTheDocument();

    await user.click(within(row).getByText('Rates'));
    await waitFor(() => expect(within(row).queryByText(/Elena from the ads desk/)).toBeNull());
  });

  it('пусто — словами: чего здесь нет и что сюда попадает', async () => {
    await openAt('/threads?tab=unbound', {
      'GET /api/replies/unbound?page=1': { body: view([]) },
    });

    expect(await screen.findByText('Непривязанных ответов нет.')).toBeInTheDocument();
    expect(screen.getByText(/Ответ на пробное письмо тоже встаёт сюда/)).toBeInTheDocument();
    expect(screen.getByText('Не привязаны — 0')).toBeInTheDocument();
  });

  it('страницы — в адресе и в запросе, размер называет сервер', async () => {
    const recorded = await openAt('/threads?tab=unbound', {
      'GET /api/replies/unbound?page=1': { body: view([PROBE_REPLY, STRAY_REPLY], 25) },
      'GET /api/replies/unbound?page=2': {
        body: view([{ ...STRAY_REPLY, id: 30, from_email: 'late@donor.example.test' }], 25, 2),
      },
    });
    const user = userEvent.setup();

    const pager = await screen.findByRole('navigation', { name: 'Страницы непривязанных ответов' });
    await user.click(within(pager).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('late@donor.example.test')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/threads?tab=unbound&page=2');
    expect(recorded.calls.map((call) => call.path)).toContain('/api/replies/unbound?page=2');
    // Счётчик во вкладке — всех, а не этой страницы.
    expect(screen.getByText('Не привязаны — 25')).toBeInTheDocument();
  });

  it('обратно на диалоги — адрес без вкладки и без страницы', async () => {
    await openAt('/threads?tab=unbound&page=1');
    const user = userEvent.setup();

    await user.click(await screen.findByText('Диалоги — 1'));

    expect(screen.getByTestId('where')).toHaveTextContent(/^\/threads$/);
    expect(
      await screen.findByRole('link', { name: /digest-weekly\.example\.test/ }),
    ).toBeInTheDocument();
  });

  it('отказ сервера — его словами, а вкладки остаются', async () => {
    await openAt('/threads?tab=unbound', {
      'GET /api/replies/unbound?page=1': {
        status: 403,
        body: { detail: 'Нет права «view»: смотреть переписку может только тот, кому его дали' },
      },
    });

    expect(await screen.findByText('Непривязанные ответы не загрузились')).toBeInTheDocument();
    expect(screen.getByText(/Нет права «view»/)).toBeInTheDocument();
    expect(screen.getByText('Диалоги — 1')).toBeInTheDocument();
  });
});

/** Текст файла: `Blob` от Node читается `text()`, от jsdom — `FileReader`
 *  (на Node 22 в CI у `Blob` из `Response` нет пути через `FileReader`). */
function textOf(blob: Blob): Promise<string> {
  if (typeof (blob as Partial<Blob>).text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === 'string'
        ? resolve(reader.result)
        : reject(new Error('файл прочитан не текстом'));
    reader.onerror = () => reject(reader.error ?? new Error('файл не прочитан'));
    reader.readAsText(blob);
  });
}

describe('вложение непривязанного ответа', () => {
  const created: Blob[] = [];
  const clicked: HTMLAnchorElement[] = [];

  beforeEach(() => {
    created.length = 0;
    clicked.length = 0;
    // В jsdom ссылок на объекты нет: подставляем их, запоминая файл.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn((blob: Blob) => {
        created.push(blob);
        return 'blob:unbound-file';
      }),
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

  it('скачивается запросом с пропуском, под настоящим именем и тем, что прислали', async () => {
    const path = '/api/replies/8/attachments/11';
    const recorded = await openAt('/threads?tab=unbound', {
      [`GET ${path}`]: {
        raw: '%PDF-1.4 rate card',
        headers: {
          'content-type': 'application/octet-stream',
          'content-disposition':
            'attachment; filename="2026.pdf"; filename*=UTF-8\'\'%D0%9F%D1%80%D0%B0%D0%B9%D1%81%202026.pdf',
        },
      },
    });
    const user = userEvent.setup();
    await user.click(await screen.findByText('Rates'));

    await user.click(screen.getByRole('button', { name: 'Скачать Прайс 2026.pdf' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(recorded.calls.find((call) => call.path === path)?.token).toBe('Bearer пропуск');
    expect(clicked[0]?.download).toBe('Прайс 2026.pdf');
    expect(await textOf(created[0]!)).toBe('%PDF-1.4 rate card');
  });
});
