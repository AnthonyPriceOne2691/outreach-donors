/**
 * Подробности записи журнала: что на виду, а что — по запросу (проверка прода 10.11.2026).
 */

import { describe, expect, it } from 'vitest';

import { FACE_CHARS, detailView } from './journalDetails';

const labels = (lines: { label: string }[]) => lines.map((line) => line.label);

describe('подробности записи журнала', () => {
  it('у письма итог — кому и от кого, этап и домен — по запросу, норма отправки не видна', () => {
    const view = detailView({
      кому: 'editor@ads.example.test',
      этап: 'advertisers',
      рекламодатель: 'ads.example.test',
      'от кого': 'hello@mail.example.test',
      транспорт: 'sendgrid',
      'ушло на самом деле': true,
    });

    expect(labels(view.face)).toEqual(['кому', 'от кого']);
    expect(labels(view.folded)).toEqual(['этап', 'рекламодатель']);
    expect(view.alerts).toEqual([]);
    expect(view.more).toEqual([]);
  });

  it('не ушло на самом деле — отклонение на виду, транспорт — в раскрытии', () => {
    const view = detailView({
      кому: 'ceo@lead.example.test',
      'от кого': 'hi@mail.example.test',
      транспорт: 'null',
      'ушло на самом деле': false,
    });

    expect(view.alerts).toEqual([{ label: 'ушло на самом деле', value: 'нет' }]);
    expect(labels(view.face)).toEqual(['кому', 'от кого']);
    expect(labels(view.more)).toEqual(['транспорт']);
  });

  it('пары письма — первыми, где бы их ни поставила запись', () => {
    // Порядок ключей задаёт база (JSONB — по длине ключа), а не важность.
    const view = detailView({ этап: 'sales', донор: 'x.example.test', 'от кого': 'a@b.test' });

    expect(labels(view.face)).toEqual(['от кого', 'этап']);
    expect(labels(view.more)).toEqual(['донор']);
  });

  it('длинное значение — только в раскрытии, даже одно', () => {
    const long = 'домен.example.test, '.repeat(10);
    expect(long.length).toBeGreaterThan(FACE_CHARS);

    const view = detailView({ домены: long, прогон: 7 });

    expect(labels(view.face)).toEqual(['прогон']);
    expect(view.more).toEqual([]);
    expect(labels(view.folded)).toEqual(['домены']);
  });

  it('показанное в другом месте и пустое — ни в итоге, ни в раскрытии', () => {
    const view = detailView(
      { действие: 'сборка писем', кто: 'консоль', прогоны: [] },
      new Set(['действие', 'кто']),
    );

    expect(view).toEqual({ alerts: [], face: [], more: [], folded: [] });
    expect(detailView(null)).toEqual({ alerts: [], face: [], more: [], folded: [] });
  });
});
