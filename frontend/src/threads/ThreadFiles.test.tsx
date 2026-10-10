/**
 * Файлы в переписке: прайс из вложения — текстом на экране, файлы нашего
 * ответа — значками со скачиванием, скрепка в ответе — загрузка до отправки,
 * отказ до загрузки по правилам сервера, восстановление после перезагрузки
 * и после перехода в соседний диалог.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { OutgoingFile } from '../api/files';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import {
  FILE_RULES,
  OUR_FILE,
  PRICE_FILE,
  letter,
  reply,
  threadWith,
} from '../test/threadFixtures';
import { refusalBefore } from './fileRules';

async function openThread(view = threadWith(), routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/8': { body: view },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/threads/8');
  await screen.findByRole('heading', { name: 'prices.example.test' });
  return recorded;
}

/**
 * Сервер, который помнит файлы переписки, как настоящий: скрепка кладёт файл, крестик
 * убирает, а переписка отдаёт то, что лежит (`pending_files`). Экран после загрузки
 * и снятия спрашивает переписку заново — сервер с неизменным ответом возвращал бы
 * снятый файл и терял загруженный.
 */
function rememberingFiles(start: OutgoingFile[] = []) {
  let pending = [...start];
  return {
    'GET /api/threads/8': () => ({ body: threadWith({ pending_files: [...pending] }) }),
    'POST /api/threads/8/files': () => {
      pending = [...pending, OUR_FILE];
      return { body: OUR_FILE };
    },
    'DELETE /api/threads/8/files/31': () => {
      pending = pending.filter((one) => one.id !== OUR_FILE.id);
      return { status: 204 };
    },
  };
}

const SENT = { body: { id: 60, sender_email: 'anna@mail.example.test', real: true } };

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) throw new Error('скрепки на экране нет');
  return input;
}

describe('правила файла до загрузки', () => {
  const pdf = (bytes: number, name = 'kit.pdf') => new File([new Uint8Array(bytes)], name);

  it('годный файл — можно', () => {
    expect(refusalBefore(pdf(1000), [], FILE_RULES)).toBeNull();
  });

  it('тот же файл второй раз — словами, до загрузки; похожий — можно', () => {
    // Проверка QA 10.10.2026: второй такой же файл молча ложился рядом с первым.
    const attached = [{ ...OUR_FILE, name: 'kit.pdf', size: 1000 }];

    expect(refusalBefore(pdf(1000), attached, FILE_RULES)).toBe(
      '«kit.pdf» уже приложен к ответу — второй такой же не нужен',
    );
    expect(refusalBefore(pdf(1000, 'other.pdf'), attached, FILE_RULES)).toBeNull();
    expect(refusalBefore(pdf(999), attached, FILE_RULES)).toBeNull();
  });

  it('чужой тип, большой файл, лишний файл, большое письмо — словами', () => {
    expect(refusalBefore(pdf(10, 'run.exe'), [], FILE_RULES)).toMatch(
      /такие файлы с письмом не уходят/,
    );
    expect(refusalBefore(pdf(7_000_001), [], FILE_RULES)).toMatch(/больше предела 7 МБ на файл/);
    const five = Array.from({ length: 5 }, (_, at) => ({ ...OUR_FILE, id: at, size: 10 }));
    expect(refusalBefore(pdf(10), five, FILE_RULES)).toMatch(/не больше 5 файлов/);
    const big = [{ ...OUR_FILE, size: 6_500_000 }];
    expect(refusalBefore(pdf(600_000), big, FILE_RULES)).toMatch(/больше предела 7 МБ на письмо/);
  });
});

describe('прайс во вложении', () => {
  it('«Текст» открывает то, что прочитано из файла, а пометка — под именем', async () => {
    await openThread(threadWith(), {
      'GET /api/replies/52/attachments/5/text': {
        body: { name: PRICE_FILE.name, text: '[лист «Prices»]\nGuest post\t300 USD', note: null },
      },
    });
    const user = userEvent.setup();

    expect(screen.getByText('скрытый лист «tmp» пропущен')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: `Текст ${PRICE_FILE.name}` }));

    const dialog = await screen.findByRole('dialog', { name: `Текст файла «${PRICE_FILE.name}»` });
    expect(await within(dialog).findByText(/Guest post\s+300 USD/)).toBeInTheDocument();
  });

  it('файл без текста — «Текст» нет, причина словами', async () => {
    const old = {
      ...PRICE_FILE,
      has_text: false,
      text_note: 'старый формат Excel — файл можно скачать',
    };
    await openThread(threadWith({ incoming: [reply({ attachments: [old] })] }));

    expect(screen.queryByRole('button', { name: `Текст ${PRICE_FILE.name}` })).toBeNull();
    expect(screen.getByText('старый формат Excel — файл можно скачать')).toBeInTheDocument();
  });
});

describe('файлы нашего ответа', () => {
  it('значком в нашем письме — со скачиванием', async () => {
    await openThread(threadWith({ letters: [letter({ attachments: [OUR_FILE] } as never)] }));

    const ours = screen.getByRole('article', { name: 'Наше письмо: первое письмо' });
    expect(
      within(ours).getByRole('button', { name: `Скачать ${OUR_FILE.name}` }),
    ).toBeInTheDocument();
  });

  it('скрепка: файл уходит на сервер сразу, ответ — с его номером', async () => {
    const recorded = await openThread(threadWith(), {
      ...rememberingFiles(),
      'POST /api/threads/8/answer': SENT,
    });
    const user = userEvent.setup();

    await user.upload(
      fileInput(),
      new File(['%PDF-1.7'], OUR_FILE.name, { type: 'application/pdf' }),
    );
    expect(await screen.findByText(/media-kit\.pdf/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Текст ответа'), 'Our media kit is attached.');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() =>
      expect(recorded.calls.some((call: Call) => call.path === '/api/threads/8/answer')).toBe(true),
    );
    const sent = recorded.calls.find((call: Call) => call.path === '/api/threads/8/answer');
    expect(sent?.body).toEqual({
      reply_id: 52,
      body: 'Our media kit is attached.',
      file_ids: [31],
    });
  });

  it('тот же файл второй раз не загружается — отказ словами до сервера', async () => {
    const recorded = await openThread(threadWith({ pending_files: [OUR_FILE] }));
    const user = userEvent.setup();

    await user.upload(fileInput(), new File([new Uint8Array(OUR_FILE.size)], OUR_FILE.name));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      `«${OUR_FILE.name}» уже приложен к ответу`,
    );
    expect(recorded.calls.some((call: Call) => call.path === '/api/threads/8/files')).toBe(false);
  });

  it('лишний файл не загружается — отказ словами до сервера', async () => {
    const recorded = await openThread();
    const user = userEvent.setup({ applyAccept: false });

    await user.upload(fileInput(), new File(['MZ'], 'setup.exe'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/такие файлы с письмом не уходят/);
    expect(recorded.calls.some((call: Call) => call.path === '/api/threads/8/files')).toBe(false);
  });

  it('загруженный и не ушедший файл — после перезагрузки на месте, убирается крестиком', async () => {
    const recorded = await openThread(
      threadWith({ pending_files: [OUR_FILE] }),
      rememberingFiles([OUR_FILE]),
    );
    const user = userEvent.setup();

    // Ответ начат — файл у поля ответа, и срок, после которого его уберут.
    expect(screen.getByLabelText('Текст ответа')).toBeInTheDocument();
    expect(
      screen.getByText('Не ушедший с письмом за 7 дней файл уберётся сам'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: `Убрать ${OUR_FILE.name}` }));

    await waitFor(() =>
      expect(
        recorded.calls.some(
          (call: Call) => call.method === 'DELETE' && call.path === '/api/threads/8/files/31',
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText(/media-kit\.pdf/)).toBeNull());
    // Файлов нет — и срока не о чем называть.
    expect(screen.queryByText(/уберётся сам/)).toBeNull();
  });

  it('приложенный файл на месте, когда вернулись из соседнего диалога, и уходит с ответом', async () => {
    // Проверка QA 10.10.2026: приложил файлы в диалоге 21, открыл 20 и вернулся — текст
    // ответа на месте, значков нет, хотя файлы на сервере: ответ ушёл бы без них.
    const card = { ...threadWith().card, id: 9, host: 'other.example.test' };
    const other = threadWith({ card: { ...card, contact_email: 'editor@other.example.test' } });
    const recorded = await openThread(threadWith(), {
      ...rememberingFiles(),
      'GET /api/threads': { body: [threadWith().card, other.card] },
      'GET /api/threads/9': { body: other },
      'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
      'POST /api/threads/8/answer': SENT,
    });
    const user = userEvent.setup();
    const row = async (host: RegExp) =>
      within(await screen.findByRole('navigation', { name: 'Список диалогов' })).getByRole('link', {
        name: host,
      });

    await user.upload(fileInput(), new File(['%PDF-1.7'], OUR_FILE.name));
    expect(await screen.findByText(/media-kit\.pdf/)).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'К списку' }));
    await user.click(await row(/^other\.example\.test/));
    await screen.findByRole('heading', { name: 'other.example.test' });
    await user.click(screen.getByRole('link', { name: 'К списку' }));
    await user.click(await row(/^prices\.example\.test/));
    await screen.findByRole('heading', { name: 'prices.example.test' });

    expect(screen.getByText(/media-kit\.pdf/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Текст ответа'), 'Our media kit is attached.');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() =>
      expect(recorded.calls.some((call: Call) => call.path === '/api/threads/8/answer')).toBe(true),
    );
    const sent = recorded.calls.find((call: Call) => call.path === '/api/threads/8/answer');
    expect(sent?.body).toMatchObject({ file_ids: [31] });
  });
});
