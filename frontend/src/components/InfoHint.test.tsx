/**
 * Значок «i» (`InfoHint`): подсказка — наведением, как всегда; окошко со ссылкой
 * (`interactive`) — наведением до ухода указателя, нажатием — закреплённым, с фокусом на
 * ссылке, до Esc или повторного нажатия; Esc возвращает фокус на значок, а в окне
 * закрывает только окошко — окно с тем, что в нём выбрано, остаётся.
 */

import { Modal } from '@mantine/core';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { InfoHint } from './InfoHint';

/** Окошко держится столько после ухода указателя — и ещё немного. */
const PAST_LEAVE_MS = 250;

function card() {
  return screen.queryByRole('dialog', { name: 'Какой файл' });
}

function renderCard(pressed: () => void) {
  renderWith(
    <InfoHint name="Какой файл" width={300} interactive>
      <button type="button" onClick={pressed}>
        Скачать
      </button>
    </InfoHint>,
  );
  return screen.getByRole('button', { name: 'Какой файл' });
}

describe('значок «i»', () => {
  it('подсказка — наведением, как прежде', async () => {
    renderWith(<InfoHint name="Что значит «DR»">Рейтинг домена.</InfoHint>);
    const user = userEvent.setup();

    await user.hover(screen.getByRole('button', { name: 'Что значит «DR»' }));

    expect(await screen.findByText('Рейтинг домена.')).toBeInTheDocument();
  });

  it('окошко нажатием держится и без указателя, фокус — на ссылке, Esc закрывает', async () => {
    const pressed = vi.fn();
    const icon = renderCard(pressed);
    const user = userEvent.setup();

    await user.click(icon);
    const shown = await screen.findByRole('dialog', { name: 'Какой файл' });
    const link = within(shown).getByRole('button', { name: 'Скачать' });
    await waitFor(() => expect(link).toHaveFocus());

    await user.unhover(icon);
    await new Promise((resolve) => setTimeout(resolve, PAST_LEAVE_MS));
    expect(card()).toBeInTheDocument();
    await user.click(link);
    expect(pressed).toHaveBeenCalledOnce();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(card()).toBeNull());
    expect(icon).toHaveFocus();
  });

  it('наведением — до окошка указатель доходит, со значка и окошка уходит — закрыто', async () => {
    const pressed = vi.fn();
    const icon = renderCard(pressed);
    const user = userEvent.setup();

    await user.hover(icon);
    const shown = await screen.findByRole('dialog', { name: 'Какой файл' });
    await user.hover(shown);
    await new Promise((resolve) => setTimeout(resolve, PAST_LEAVE_MS));
    expect(card()).toBeInTheDocument();

    await user.unhover(shown);
    await waitFor(() => expect(card()).toBeNull());
  });

  it('в окне Esc закрывает окошко, а не окно; следующий Esc — уже окно', async () => {
    const closed = vi.fn();
    renderWith(
      <Modal opened onClose={closed} title="Загрузка">
        <InfoHint name="Какой файл" interactive>
          <button type="button">Скачать</button>
        </InfoHint>
      </Modal>,
    );
    const user = userEvent.setup();
    const icon = await screen.findByRole('button', { name: 'Какой файл' });

    await user.click(icon);
    const shown = await screen.findByRole('dialog', { name: 'Какой файл' });
    await waitFor(() =>
      expect(within(shown).getByRole('button', { name: 'Скачать' })).toHaveFocus(),
    );
    await user.keyboard('{Escape}');

    await waitFor(() => expect(card()).toBeNull());
    expect(closed).not.toHaveBeenCalled();
    expect(icon).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(closed).toHaveBeenCalledOnce();
  });

  it('повторное нажатие по значку закрывает окошко', async () => {
    const icon = renderCard(vi.fn());
    const user = userEvent.setup();

    await user.click(icon);
    expect(await screen.findByRole('dialog', { name: 'Какой файл' })).toBeInTheDocument();
    await user.click(icon);

    await waitFor(() => expect(card()).toBeNull());
  });
});
