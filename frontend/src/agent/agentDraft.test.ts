/**
 * Черновик настроек агента: строки по одной на пункт и отказ до нажатия
 * тем же правилом, что у схемы сервера.
 */

import { describe, expect, it } from 'vitest';

import type { AgentSettingsBody } from '../api/agent';
import { formatNumber } from '../format';
import { bodyOf, draftOf, linesOf, refusalsOf, sameDraft } from './agentDraft';

const SAVED: AgentSettingsBody = {
  enabled: true,
  goal: 'Узнать цену',
  tone: 'Коротко',
  points: ['Спросить цену', 'Попросить скидку'],
  price_limit_usd: '150.00',
  stop_topics: ['Договор'],
};

/** Действующие в автопилоте: режим и предел ответов приходят с сервера. */
const AUTO: AgentSettingsBody = { ...SAVED, mode: 'autopilot', max_turns: 3 };

describe('черновик настроек агента', () => {
  it('пункты — по одному на строку, пустые строки не пункты', () => {
    expect(linesOf('  первый \n\n второй\n ')).toEqual(['первый', 'второй']);
  });

  it('туда и обратно — без потерь, цена с копейками', () => {
    expect(bodyOf(draftOf(SAVED))).toEqual(SAVED);
  });

  it('пустое поле цены — предела нет', () => {
    const body = bodyOf({ ...draftOf(SAVED), price: '' });
    expect(body?.price_limit_usd).toBeNull();
  });

  it('отказы — по полю и словами, тело с отказом не собирается', () => {
    const draft = {
      ...draftOf(SAVED),
      goal: '   ',
      price: '1 000 000',
      points: Array.from({ length: 21 }, (_, n) => `довод ${n}`).join('\n'),
    };

    expect(refusalsOf(draft)).toEqual({
      goal: 'Пусто — напишите хотя бы фразу',
      price: `Допустимо от 0 до ${formatNumber(100_000)}`,
      points: 'Пунктов 21 — больше 20',
    });
    expect(bodyOf(draft)).toBeNull();
  });

  // Числом поле стиралось на точке: «12.» — строка, и черновик делал из неё «пусто»
  // (проверка QA 10.10.2026: «12.5» → 5, «99,5» → 5).
  it.each([
    ['12.5', '12.50'],
    ['99,5', '99.50'],
    ['0.99', '0.99'],
    ['1 250,5', '1250.50'],
  ] as const)('цена «%s» — как набрана, на сервер — %s', (typed, sent) => {
    const draft = { ...draftOf(SAVED), price: typed };
    expect(refusalsOf(draft).price).toBeUndefined();
    expect(bodyOf(draft)?.price_limit_usd).toBe(sent);
  });

  it('не число и лишние центы — отказ, а не склеенные цифры', () => {
    const refusal = (price: string) => refusalsOf({ ...draftOf(SAVED), price }).price;

    // «ю» — точка на русской раскладке: 12ю5 не становится 125.
    expect(refusal('12ю5')).toBe('Только число, например 99,50');
    expect(refusal('12.345')).toBe('Не больше двух знаков после запятой');
    expect(refusal('-5')).toBe(`Допустимо от 0 до ${formatNumber(100_000)}`);
  });

  it('цена — та же, если записана иначе: «1250.5» при действующих «1250.50» — не правка', () => {
    const saved = { ...SAVED, price_limit_usd: '1250.50' };
    expect(draftOf(saved).price).toBe('1 250,5');
    expect(sameDraft({ ...draftOf(saved), price: '1250.5' }, saved)).toBe(true);
    expect(sameDraft({ ...draftOf(saved), price: '1250.6' }, saved)).toBe(false);
    expect(sameDraft({ ...draftOf(SAVED), price: 'абв' }, SAVED)).toBe(false);
  });

  it('длинная строка называется номером', () => {
    const draft = { ...draftOf(SAVED), stopTopics: `коротко\n${'я'.repeat(301)}` };
    expect(refusalsOf(draft).stopTopics).toBe('Строка 2 длиннее 300 знаков');
  });

  it('лишние пробелы и пустые строки — не правка', () => {
    const draft = {
      ...draftOf(SAVED),
      goal: ' Узнать цену ',
      points: 'Спросить цену\n\nПопросить скидку\n',
    };
    expect(sameDraft(draft, SAVED)).toBe(true);
    expect(sameDraft({ ...draft, enabled: false }, SAVED)).toBe(false);
  });

  it('режим и предел ответов автопилота уходят обратно, их смена — правка', () => {
    expect(bodyOf(draftOf(AUTO))).toEqual(AUTO);
    expect(sameDraft(draftOf(AUTO), AUTO)).toBe(true);
    expect(sameDraft({ ...draftOf(AUTO), mode: 'drafts' }, AUTO)).toBe(false);
    expect(sameDraft({ ...draftOf(AUTO), maxTurns: 2 }, AUTO)).toBe(false);
  });
});
