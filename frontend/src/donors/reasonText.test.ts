/**
 * Причина вердикта: у «не проверен» своя подпись (12.10.2026) — «Причина отсева»
 * у домена, которого никто не отсеивал, читалась бы приговором.
 */

import { describe, expect, it } from 'vitest';

import { reasonLabel, reasonText } from './reasonText';

describe('причина вердикта', () => {
  it('у отказа — «Причина отсева», под доменом — как есть', () => {
    expect(reasonLabel('unsuitable')).toBe('Причина отсева');
    expect(reasonText('unsuitable', 'DR 8 ниже 20')).toBe('DR 8 ниже 20');
  });

  it('у «не проверен» — «Почему не проверен», и под доменом тоже', () => {
    expect(reasonLabel('unchecked')).toBe('Почему не проверен');
    expect(reasonText('unchecked', 'страны не получены')).toBe(
      'Почему не проверен: страны не получены',
    );
  });
});
