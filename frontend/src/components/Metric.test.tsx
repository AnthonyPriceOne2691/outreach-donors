/**
 * Плитка с числом: строка пояснения — только там, где пояснение есть.
 *
 * Раскладку jsdom не считает, поэтому здесь проверяется договор с правилом
 * в `glass.css` (26.09.2026): третью строку сетки заводит плитка с классом
 * `metricHint`. Переименуй класс молча — и под числом у всех плиток ряда
 * снова встанет пустая полоса, а тесты экранов этого не заметят.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { Metric } from './Metric';

describe('плитка с числом', () => {
  it('без пояснения — две строки: подпись и число, третьей нет', () => {
    const { container } = renderWith(<Metric title="Приняты" value="202" />);

    const tile = container.querySelector('.metricTile');
    expect(tile?.children).toHaveLength(2);
    expect(container.querySelector('.metricHint')).toBeNull();
  });

  it('пояснение помечено для сетки: по нему ряд заводит строку под числом', () => {
    renderWith(<Metric title="Выпадет из базы" value="3" hint="из них с ценой: 1" />);

    expect(screen.getByText('из них с ценой: 1')).toHaveClass('metricHint');
  });

  it('«i» у подписи: что значит число — подсказкой, а строки плитки прежние', async () => {
    // Аудит экранов 09.10.2026: определения шагов воронки стояли абзацем под плитками.
    const { container } = renderWith(
      <Metric title="Ответ" value="3" info="Человек ответил сам или попросил не писать." />,
    );

    // Значок — в строке подписи, а не четвёртой строкой: подсетка ряда раскладывает три.
    expect(container.querySelector('.metricTile')?.children).toHaveLength(2);
    const button = screen.getByRole('button', { name: 'Что значит «Ответ»' });
    expect(button.closest('.metricTitle')).toHaveTextContent('Ответ');
    expect(screen.queryByText(/Человек ответил сам/)).toBeNull();

    await userEvent.setup().hover(button);

    expect(
      await screen.findByText('Человек ответил сам или попросил не писать.'),
    ).toBeInTheDocument();
  });
});
