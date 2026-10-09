import { Button } from '@mantine/core';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { ConfirmPopover } from './ConfirmPopover';

describe('подтверждение у кнопки', () => {
  it('спрашивает, называет последствие, и только «Сбросить» делает дело', async () => {
    const done = vi.fn();
    renderWith(
      <ConfirmPopover
        message="Сбросить пароль? Старый перестанет действовать."
        confirm="Сбросить"
        onConfirm={done}
      >
        {(ask) => <Button onClick={ask}>Сбросить пароль</Button>}
      </ConfirmPopover>,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Сбросить пароль' }));
    const ask = await screen.findByRole('dialog', { name: /^Сбросить пароль\?/, hidden: true });
    expect(done).not.toHaveBeenCalled();
    // Выпадающее окно Mantine в jsdom остаётся `display: none` — раскладки здесь нет;
    // без `hidden` его кнопки не видны запросу, а щелчок по ним настоящий.
    await user.click(within(ask).getByRole('button', { name: 'Сбросить', hidden: true }));
    expect(done).toHaveBeenCalledTimes(1);
  });
});
