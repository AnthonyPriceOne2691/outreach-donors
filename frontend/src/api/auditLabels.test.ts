/**
 * Журнал словами: цель записи и подробности — словами экрана, а не кодами журнала;
 * незнакомое не пропадает и не роняет экран, а называется как записано.
 */

import { describe, expect, it } from 'vitest';

import { actionTitle, detailLines, targetParts } from './auditLabels';
import { formatNumber } from '../format';

const EMAILS = new Map([[3, 'иван@team.example.test']]);

describe('цель записи', () => {
  it('прогон и донор — ссылкой на свой экран, учётка — почтой', () => {
    expect(targetParts('run:27', EMAILS)).toEqual([{ text: 'прогон №27', to: '/runs/27' }]);
    expect(targetParts('donor:5', EMAILS)).toEqual([{ text: 'донор №5', to: '/donors/5' }]);
    expect(targetParts('user:3', EMAILS)).toEqual([{ text: 'учётка иван@team.example.test' }]);
    expect(targetParts('user:4', EMAILS)).toEqual([{ text: 'учётка №4' }]);
  });

  it('служебный номер не показывается: у порогов версия — в подробностях', () => {
    expect(targetParts('settings:9', EMAILS)).toEqual([{ text: 'пороги' }]);
    expect(targetParts('job:3f2a-b', EMAILS)).toEqual([{ text: 'задача в очереди' }]);
  });

  it('перечень чистки — каждой частью, незнакомое — как записано', () => {
    expect(targetParts('run:1, reply:5, probes:2', EMAILS)).toEqual([
      { text: 'прогон №1', to: '/runs/1' },
      { text: 'ответ №5' },
      { text: 'probes:2' },
    ]);
    expect(targetParts('sales_kb', EMAILS)).toEqual([{ text: 'база знаний продаж' }]);
    expect(targetParts(null, EMAILS)).toEqual([]);
  });
});

describe('действие записи', () => {
  it('общее действие уточняется целью, незнакомое — с кодом', () => {
    expect(actionTitle('user_updated', 'user:3')).toBe('Доступ учётки изменён');
    expect(actionTitle('user_updated', 'message:7')).toBe('Правка');
    expect(actionTitle('run_started', 'run:27')).toBe('Прогон запущен');
    expect(actionTitle('run_started', 'crawl:2')).toBe('Обход донора запущен');
    expect(actionTitle('new_thing', null)).toBe('Другое действие (new_thing)');
  });
});

describe('подробности записи', () => {
  it('коды — словами: роль, учётка, права, решение, причина стоп-листа', () => {
    expect(
      detailLines({
        role: 'operator',
        is_active: true,
        permissions: { send: true },
        решение: 'rejected',
        причина: 'supplier',
      }),
    ).toEqual([
      { label: 'роль', value: 'оператор' },
      { label: 'учётка', value: 'включена' },
      { label: 'права', value: 'отправлять письма — выдано' },
      { label: 'решение', value: 'отклонён' },
      { label: 'причина', value: 'поставщик' },
    ]);
  });

  it('числа с разрядами, да/нет, списки, было → стало, вложенное — «ключ: значение»', () => {
    expect(
      detailLines({
        писем: 1500,
        уверенность: 0.83,
        'ушло на самом деле': false,
        прогоны: [3, 4],
        версия: { было: 2, стало: 3 },
        поля: { email: 'a@b.example.test' },
      }),
    ).toEqual([
      // Разряды — тем же пробелом, что у всех чисел экрана (`format.ts`).
      { label: 'писем', value: formatNumber(1500) },
      { label: 'уверенность', value: '0,83' },
      { label: 'ушло на самом деле', value: 'нет' },
      { label: 'прогоны', value: '3, 4' },
      { label: 'версия', value: '2 → 3' },
      { label: 'поля', value: 'почта: a@b.example.test' },
    ]);
  });

  it('пустого нет строкой, а показанное в другом месте — не повторяется', () => {
    expect(
      detailLines(
        { действие: 'сборка писем', прогоны: [], примечание: '', до: null },
        new Set(['действие']),
      ),
    ).toEqual([]);
    expect(detailLines(null)).toEqual([]);
  });
});
