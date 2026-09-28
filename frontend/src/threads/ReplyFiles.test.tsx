/**
 * Вложения в карточке ответа: что видно и как скачивается.
 *
 * Скачивание проверяется содержимым файла и заголовком запроса, а не фактом
 * щелчка: выгрузка доноров когда-то уходила простой ссылкой без пропуска
 * и получала 401, а тест смотрел только на адрес ссылки.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { IncomingCard, ReplyAttachment } from '../api/types';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Answer } from '../test/server';
import { serve } from '../test/server';

const PRICE: ReplyAttachment = {
  id: 11,
  name: 'Прайс 2026.pdf',
  size: 1_572_864,
  content_type: 'application/pdf',
  accepted: true,
  reason: null,
};

const REFUSED: ReplyAttachment = {
  id: 12,
  name: 'prices.exe',
  size: 812,
  content_type: 'application/x-msdownload',
  accepted: false,
  reason: '«.exe» — исполняемый файл или скрипт, такие не принимаются',
};

const LOST: ReplyAttachment = {
  id: 13,
  name: 'rates.xlsx',
  size: null,
  content_type: null,
  accepted: false,
  reason: 'платформа назвала файл, но самого файла в письме не было',
};

const REPLY: IncomingCard = {
  id: 7,
  kind: 'human',
  raw_body: 'Our rate card is attached.',
  received_at: '2026-09-28T10:00:00+00:00',
  from_email: 'elena@donor.example.test',
  subject: 'Re: Advertising rates',
  attachments: [PRICE, REFUSED, LOST],
  price_white: null,
  price_grey: null,
  currency: null,
  payment_methods: null,
  confidence: null,
  placement: null,
  needs_review: true,
  reviewed_by: null,
  reviewed_at: null,
  lead: false,
};

const VIEW = {
  card: {
    id: 3,
    host: 'donor.example.test',
    contact_email: 'editor@donor.example.test',
    campaign: 'Проверка',
    stage: 'donors',
    state: 'needs_review',
    messages_sent: 1,
    last_event_at: '2026-09-28T10:00:00+00:00',
    last_reply_at: '2026-09-28T10:00:00+00:00',
    price_white: null,
    price_grey: null,
    currency: null,
  },
  letters: [],
  incoming: [REPLY],
  corridor: { min: 0.15, max: 0.25 },
};

const FILE_PATH = '/api/replies/7/attachments/11';

async function openThread(file: Answer) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/3': { body: VIEW },
    [`GET ${FILE_PATH}`]: file,
  });
  renderWith(<AppRoutes />, '/threads/3');
  await screen.findByRole('heading', { name: 'donor.example.test' });
  return recorded;
}

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

describe('вложения ответа', () => {
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
        return 'blob:reply-file';
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

  it('видны имя и размер, у несохранённого — причина словами', async () => {
    await openThread({ raw: '' });

    const list = screen.getByRole('list', { name: 'Вложения' });
    expect(list).toHaveTextContent('Прайс 2026.pdf');
    expect(screen.getByText('1,5 МБ')).toBeInTheDocument();
    expect(
      screen.getByText('812 байт · «.exe» — исполняемый файл или скрипт, такие не принимаются'),
    ).toBeInTheDocument();
    // Размер неизвестен — словами, а не «0 байт»: пустой файл и потерянный — разное.
    expect(
      screen.getByText(
        'размер неизвестен · платформа назвала файл, но самого файла в письме не было',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText('не сохранён')).toHaveLength(2);
    // Скачать можно только сохранённый.
    expect(screen.getAllByRole('button', { name: /^Скачать/ })).toHaveLength(1);
  });

  it('файл скачивается запросом с пропуском и под настоящим именем', async () => {
    const recorded = await openThread({
      raw: '%PDF-1.4 rate card',
      headers: {
        'content-type': 'application/octet-stream',
        'content-disposition':
          'attachment; filename="2026.pdf"; filename*=UTF-8\'\'%D0%9F%D1%80%D0%B0%D0%B9%D1%81%202026.pdf',
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Скачать Прайс 2026.pdf' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    const call = recorded.calls.find((sent) => sent.path === FILE_PATH);
    expect(call?.token).toBe('Bearer пропуск');
    // Имя — из `filename*`, целиком, а не запасное латиницей.
    expect(clicked[0]?.download).toBe('Прайс 2026.pdf');
    expect(clicked[0]?.getAttribute('href')).toBe('blob:reply-file');
    expect(await textOf(created[0]!)).toBe('%PDF-1.4 rate card');
  });

  it('отказ сервера — уведомлением с его текстом', async () => {
    await openThread({
      status: 404,
      body: { detail: 'Файл «Прайс 2026.pdf» не сохранён: причина не записана' },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Скачать Прайс 2026.pdf' }));

    expect(await screen.findByText('Файл не скачался')).toBeInTheDocument();
    expect(
      screen.getByText('Файл «Прайс 2026.pdf» не сохранён: причина не записана'),
    ).toBeInTheDocument();
    expect(clicked).toHaveLength(0);
  });
});
