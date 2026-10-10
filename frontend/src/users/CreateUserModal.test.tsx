/**
 * Почта новой учётки: проверка формы адреса до сервера — и слова, которые говорят, что не так.
 *
 * Проверка QA 10.10.2026: экран пропускал всё, где есть «@», — `a@b` заводился
 * учёткой, которую потом не удалить, — а `no-at-sign` получал отказ «Почта — она же
 * логин», не называющий ошибки.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { Call } from '../test/server';
import { OPERATOR, card } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { CreateUserModal } from './CreateUserModal';

function posts(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'POST');
}

async function create(email: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText('Почта'), email);
  await user.click(screen.getByRole('button', { name: 'Завести' }));
}

describe('новая учётка: почта', () => {
  it.each([
    ['a@b', 'После «@» нужен домен с зоной через точку — например example.com'],
    ['no-at-sign', 'В почте нет «@» — нужен адрес целиком, например ivan@example.com'],
    ['ivan petrov@example.com', 'В почте пробел — адрес пишется без пробелов'],
    ['@example.com', 'Перед «@» нет имени ящика — например ivan@example.com'],
  ])('«%s» не уходит на сервер, и отказ называет, что не так', async (email, says) => {
    const recorded = serve({});
    renderWith(<CreateUserModal opened onClose={vi.fn()} onCreated={vi.fn()} />);

    await create(email);

    expect(await screen.findByText(says)).toBeInTheDocument();
    expect(posts(recorded.calls)).toHaveLength(0);
  });

  it('годный адрес уходит, пробелы по краям не мешают', async () => {
    const issued = {
      user: card({ ...OPERATOR, id: 9, email: 'ivan@example.com' }, { must_change_password: true }),
      password: 'разовый-пароль',
      note: 'Пароль показан один раз. При входе система потребует его сменить.',
    };
    const recorded = serve({ 'POST /api/users': { status: 201, body: issued } });
    const created = vi.fn();
    renderWith(<CreateUserModal opened onClose={vi.fn()} onCreated={created} />);

    await create(' ivan@example.com ');

    await vi.waitFor(() => expect(created).toHaveBeenCalledWith(issued));
    expect(posts(recorded.calls)).toHaveLength(1);
  });

  it('отказ сервера по форме адреса показан его словами', async () => {
    serve({
      'POST /api/users': {
        status: 422,
        body: {
          detail: [{ msg: 'После «@» нужен домен с зоной через точку — например example.com' }],
        },
      },
    });
    renderWith(<CreateUserModal opened onClose={vi.fn()} onCreated={vi.fn()} />);

    await create('ivan@example.com');

    expect(await screen.findByText('Не завели')).toBeInTheDocument();
    expect(screen.getByText(/нужен домен с зоной/)).toBeInTheDocument();
  });
});
