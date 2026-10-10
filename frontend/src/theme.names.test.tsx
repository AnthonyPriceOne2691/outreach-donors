/**
 * Кнопки, которые Mantine рисует сам, — для программы чтения с экрана.
 *
 * Проверка QA 10.10.2026: крестик каждого окна читался как «кнопка» без имени —
 * у Mantine это кнопка с одним значком. Имя задаёт тема (`theme.ts`, `Modal`), одно
 * на все окна. Стрелки ▲▼ числового поля — наоборот: Mantine прячет их от диктора, и
 * имя им не нужно; тест держит это на случай, если новая версия Mantine перестанет.
 */

import { Modal, NumberInput } from '@mantine/core';
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderWith } from './test/render';

describe('кнопки Mantine для диктора', () => {
  it('крестик окна называется «Закрыть»', async () => {
    renderWith(
      <Modal opened onClose={() => undefined} title="Завести донора вручную">
        Поля окна.
      </Modal>,
    );

    expect(await screen.findByRole('button', { name: 'Закрыть' })).toBeInTheDocument();
  });

  it('стрелки числового поля спрятаны от диктора и не стоят в порядке Tab', () => {
    const { container } = renderWith(
      <NumberInput aria-label="Писем за раз" value={50} onChange={() => undefined} />,
    );

    const arrows = [...container.querySelectorAll('.mantine-NumberInput-control')];
    expect(arrows).toHaveLength(2);
    for (const arrow of arrows) {
      expect(arrow).toHaveAttribute('aria-hidden', 'true');
      expect(arrow).toHaveAttribute('tabindex', '-1');
    }
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });
});
