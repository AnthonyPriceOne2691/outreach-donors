/**
 * Какой этап каким правом открыт на экране: одно правило на меню, списки и тревоги.
 * Копию таблицы сервера сверяет `tests/test_access_roles.py`.
 */

import { describe, expect, it } from 'vitest';

import { stageShown, stateShown } from './stageRights';
import type { Can } from './stageRights';

const ALL: Can = () => true;
const NO_SALES: Can = (right) => right !== 'sales';
const DONORS_ONLY: Can = (right) => right !== 'sales' && right !== 'advertisers';

describe('этап на общих экранах', () => {
  it.each([
    ['donors', ALL, true],
    ['donors', DONORS_ONLY, true],
    ['advertisers', ALL, true],
    ['advertisers', NO_SALES, true],
    ['advertisers', DONORS_ONLY, false],
    ['sales', NO_SALES, false],
    ['sales', ALL, true],
  ])('%s', (stage, can, shown) => {
    expect(stageShown(stage, can)).toBe(shown);
  });

  it('незнакомый этап не прячется: сервер новее экрана', () => {
    expect(stageShown('partners', DONORS_ONLY)).toBe(true);
  });
});

describe('состояние диалога', () => {
  it('состояния этапа — только с его правом, общие — всем', () => {
    expect(stateShown('lead', DONORS_ONLY)).toBe(false);
    expect(stateShown('lead_taken', DONORS_ONLY)).toBe(false);
    expect(stateShown('lead', NO_SALES)).toBe(true);
    expect(stateShown('sales_pending', NO_SALES)).toBe(false);
    expect(stateShown('needs_review', DONORS_ONLY)).toBe(true);
  });
});
